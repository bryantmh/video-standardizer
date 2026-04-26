"""Per-segment duplicate-frame phase detector for 30fps→24fps telecine duplication.

Pattern this detects:
    Source was originally 24fps film, converted to 30fps by duplicating one frame
    per group of 5 source frames. No blending — one frame appears twice.

    Per group of 5 source frames, one adjacent pair is near-identical:
        Phase 0: [A, A, B, C, D]  — pair at positions (0,1)
        Phase 1: [A, B, B, C, D]  — pair at positions (1,2)
        Phase 2: [A, B, C, C, D]  — pair at positions (2,3)
        Phase 3: [A, B, C, D, D]  — pair at positions (3,4)

    The "phase" is the absolute source frame index (mod 5) of the FIRST frame
    of each duplicate pair. After aligning with skip=(phase-seg_start)%5, every
    5-cycle is [dup1, dup2, uniq1, uniq2, uniq3] and dup2 can be dropped with
    SelectEvery(cycle=5, offsets=[0, 2, 3, 4]).

Detection approach:
    For each analysis window, pre-compute the mean-absolute-difference (MAD)
    between every consecutive frame pair. Then, for all 20 combinations of
    (cycle_start_offset 0–4, pair_position 0–3), find which combination
    consistently produces the lowest MAD across all 5-frame cycles in the window.
    The "gap" (mean of other-pair MADs minus dup-pair MAD, in pixel units) and
    "consistency" (fraction of cycles where this pair position has the minimum
    MAD) together form the confidence score.

    Because compressed video introduces coding artifacts, duplicate frames are
    rarely 100% identical — MAD of 0–3 pixel units is typical for dup pairs,
    vs. 5–30+ for genuine frame transitions.

Usage:
    python detect_dedupe.py <source.mp4> [--start N] [--end N]
                            [--window 25] [--stride 5] [--save segments.json]

The output segments JSON is a list of {"start_frame": int, "end_frame": int,
"phase": int} records, where phase is the absolute frame index mod 5 of the
first frame of each duplicate pair. start_frame/end_frame are absolute source
frame indices.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import vapoursynth as vs
from vapoursynth import core


def best_phase_for_window(frames: list[np.ndarray]) -> tuple[int | None, float, float]:
    """Given decoded luma frames for a window, find the duplicate-pair phase.

    Tries all 5 cycle-start offsets × 4 pair positions (20 candidates total).
    Returns (rel_pair_offset, gap, consistency) where:
        rel_pair_offset — offset within the window (mod 5) of the first frame of
                          each dup pair; caller adds window_start to get abs_phase.
        gap             — mean(other_pair_MADs) - dup_pair_MAD, in pixel units.
        consistency     — fraction of 5-cycles where this pair has the min MAD.
    Returns (None, 0.0, 0.0) if no confident phase found.
    """
    n = len(frames)
    if n < 10:
        return None, 0.0, 0.0

    # Precompute all consecutive-pair MADs once.
    mads = [
        float(np.mean(np.abs(
            frames[i].astype(np.float32) - frames[i + 1].astype(np.float32)
        )))
        for i in range(n - 1)
    ]

    best = (None, -1.0, 0.0, 0.0)
    for s in range(5):  # cycle starting offset within the window
        cycles = []
        for c in range(s, n - 4, 5):
            # Need pair MADs at c, c+1, c+2, c+3 — all valid since c <= n-5.
            cycles.append([mads[c], mads[c + 1], mads[c + 2], mads[c + 3]])
        if len(cycles) < 2:
            continue
        arr = np.array(cycles)  # (n_cycles, 4)

        for p in range(4):
            col = arr[:, p]
            # Mean of the other 3 pair MADs per cycle.
            others_mean = (arr.sum(axis=1) - col) / 3.0
            gap = float((others_mean - col).mean())
            min_pos = np.argmin(arr, axis=1)
            consistency = float((min_pos == p).mean())
            score = gap + 10.0 * consistency  # weight consistency heavily
            if score > best[1]:
                rel_pair_offset = (s + p) % 5
                best = (rel_pair_offset, score, gap, consistency)

    if best[0] is None or best[2] <= 0:
        return None, 0.0, 0.0
    return best[0], best[2], best[3]


def _analyze_window(gray_clip, pos: int, window_frames: int,
                    min_gap: float, min_consistency: float) -> dict:
    """Decode frames for one window and return its phase sample dict."""
    frames = []
    for i in range(window_frames):
        f = gray_clip.get_frame(pos + i)
        frames.append(np.asarray(f[0], dtype=np.uint8))

    rel_pair_offset, gap, consistency = best_phase_for_window(frames)
    if rel_pair_offset is not None:
        abs_phase = (pos + rel_pair_offset) % 5
    else:
        abs_phase = None
    return {
        "frame": pos,
        "phase": abs_phase,
        "gap": gap,
        "consistency": consistency,
        "ok": (abs_phase is not None
               and gap >= min_gap
               and consistency >= min_consistency),
    }


def _worker_chunk(src_path: str, positions: list[int], window_frames: int,
                  min_gap: float, min_consistency: float,
                  worker_id: int = 0,
                  indexed_flags=None, done_counts=None) -> list[dict]:
    """Subprocess worker: open its own VapourSynth core and process a chunk.

    Each worker has its own decoder so windows are processed in true parallel
    across CPUs without GIL or shared-clip contention.
    """
    import vapoursynth as vs  # noqa: F811 - re-import inside subprocess
    from vapoursynth import core as worker_core
    clip = worker_core.lsmas.LWLibavSource(source=src_path)
    gray = worker_core.resize.Bicubic(clip, format=vs.GRAY8, matrix_in_s="709")
    if indexed_flags is not None:
        indexed_flags[worker_id] = 1
    results = []
    for pos in positions:
        results.append(_analyze_window(gray, pos, window_frames, min_gap, min_consistency))
        if done_counts is not None:
            done_counts[worker_id] += 1
    return results


def detect_segments(
    src: Path,
    start_frame: int,
    end_frame: int,
    window_frames: int,
    stride: int,
    min_gap: float,
    min_consistency: float,
    run_len: int,
    snap_unit: int,
    threads: int,
) -> list[dict]:
    """Walk the source, return [{start_frame, end_frame, phase}]."""
    clip = core.lsmas.LWLibavSource(source=str(src))
    if end_frame <= 0:
        end_frame = clip.num_frames
    end_frame = min(end_frame, clip.num_frames)

    print(f"Detecting duplicate-frame phase: frames [{start_frame}, {end_frame}), "
          f"window={window_frames}, stride={stride}, threads={threads}",
          file=sys.stderr)

    positions = list(range(start_frame, end_frame - window_frames + 1, stride))
    if not positions:
        raise RuntimeError("not enough source frames for any analysis window")

    total = len(positions)
    samples = [None] * total
    last_progress = -1

    if threads <= 1 or total < threads * 4:
        # Sequential path: single VS core in this process.
        gray = core.resize.Bicubic(clip, format=vs.GRAY8, matrix_in_s="709")
        for i, pos in enumerate(positions):
            samples[i] = _analyze_window(gray, pos, window_frames, min_gap, min_consistency)
            progress = int(100 * (i + 1) / total)
            if progress != last_progress:
                print(f"  detect: {i + 1}/{total} windows ({progress}%)",
                      file=sys.stderr, flush=True)
                last_progress = progress
    else:
        # Process-pool path: each worker opens its own VS + lsmas.
        chunk_size = (total + threads - 1) // threads
        chunks = [positions[i:i + chunk_size] for i in range(0, total, chunk_size)]
        chunk_offsets = [i for i in range(0, total, chunk_size)]
        n_chunks = len(chunks)
        chunk_sizes = [len(c) for c in chunks]
        print(f"  Spawning {n_chunks} worker(s) — waiting for source index...",
              file=sys.stderr, flush=True)

        with multiprocessing.Manager() as mgr:
            indexed_flags = mgr.list([0] * n_chunks)  # 0=indexing, 1=indexed
            done_counts   = mgr.list([0] * n_chunks)  # windows done per worker

            stop_event = threading.Event()

            def _monitor():
                while not stop_event.is_set():
                    time.sleep(6.0)
                    parts = []
                    total_done = 0
                    for i in range(n_chunks):
                        done = done_counts[i]
                        total_done += done
                        sz = chunk_sizes[i]
                        if indexed_flags[i] == 0:
                            parts.append(f"W{i + 1}:idx")
                        elif done >= sz:
                            parts.append(f"W{i + 1}:done")
                        else:
                            pct = int(100 * done / sz) if sz else 100
                            parts.append(f"W{i + 1}:{pct}%")
                    overall = int(100 * total_done / total) if total else 100
                    print(f"  detect [{overall}%] " + "  ".join(parts),
                          file=sys.stderr, flush=True)

            monitor = threading.Thread(target=_monitor, daemon=True)
            monitor.start()

            with ProcessPoolExecutor(max_workers=threads) as ex:
                future_to_offset = {
                    ex.submit(_worker_chunk, str(src), chunk, window_frames,
                              min_gap, min_consistency, wid,
                              indexed_flags, done_counts): off
                    for wid, (chunk, off) in enumerate(zip(chunks, chunk_offsets))
                }
                for fut in as_completed(future_to_offset):
                    off = future_to_offset[fut]
                    results = fut.result()
                    for j, r in enumerate(results):
                        samples[off + j] = r

            stop_event.set()
            monitor.join()

    print(f"Collected {len(samples)} samples.", file=sys.stderr)

    # Fill non-confident samples from nearest confident neighbor.
    ok_idx = [i for i, s in enumerate(samples) if s["ok"]]
    if not ok_idx:
        raise RuntimeError(
            "no high-confidence duplicate-frame detections; "
            "the source may not be 24→30fps telecine duplication"
        )
    for i, s in enumerate(samples):
        if not s["ok"]:
            nearest = min(ok_idx, key=lambda j: abs(j - i))
            s["phase"] = samples[nearest]["phase"]
            s["filled"] = True

    # Run-length filter: suppress single-window phase blips.
    smoothed = [s["phase"] for s in samples]
    n = len(smoothed)
    for i in range(n):
        prev_window = smoothed[max(0, i - run_len):i]
        next_window = smoothed[i + 1:min(n, i + run_len + 1)]
        if not prev_window or not next_window:
            continue
        prev_majority = Counter(prev_window).most_common(1)[0]
        next_majority = Counter(next_window).most_common(1)[0]
        if (prev_majority[0] == next_majority[0]
                and prev_majority[0] != smoothed[i]
                and prev_majority[1] >= 2 and next_majority[1] >= 2):
            smoothed[i] = prev_majority[0]
    for s, p in zip(samples, smoothed):
        s["phase"] = p

    # Coalesce adjacent same-phase samples into segments. Snap boundaries to
    # multiples of snap_unit source frames.
    segments = []
    cur_start = samples[0]["frame"]
    cur_phase = samples[0]["phase"]
    for i in range(1, len(samples)):
        if samples[i]["phase"] != cur_phase:
            boundary = samples[i]["frame"]
            boundary = (boundary // snap_unit) * snap_unit
            if boundary > cur_start:
                segments.append({
                    "start_frame": cur_start,
                    "end_frame": boundary,
                    "phase": cur_phase,
                })
                cur_start = boundary
                cur_phase = samples[i]["phase"]
    final_end = (end_frame // snap_unit) * snap_unit
    if final_end > cur_start:
        segments.append({
            "start_frame": cur_start,
            "end_frame": final_end,
            "phase": cur_phase,
        })

    return segments


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("source", type=Path, help="Source video file")
    ap.add_argument("--start", type=int, default=0,
                    help="First source frame to analyze (default 0)")
    ap.add_argument("--end", type=int, default=0,
                    help="Last source frame (exclusive). 0 = end of file (default).")
    ap.add_argument("--window", type=int, default=25,
                    help="Frames per analysis window (default 25, ~5 cadence cycles)")
    ap.add_argument("--stride", type=int, default=5,
                    help="Frames to advance between windows (default 5)")
    ap.add_argument("--min-gap", type=float, default=2.0,
                    help="Minimum MAD gap (mean_other_pairs - dup_pair, pixel units) "
                         "to count a window as confident (default 2.0)")
    ap.add_argument("--min-consistency", type=float, default=0.6,
                    help="Minimum fraction of 5-cycles where the detected pair has "
                         "the lowest MAD (default 0.6)")
    ap.add_argument("--run-len", type=int, default=3,
                    help="Phase change must persist N consecutive windows (default 3)")
    ap.add_argument("--snap", type=int, default=5,
                    help="Snap segment boundaries to multiples of N frames (default 5)")
    ap.add_argument("--threads", type=int,
                    default=max(1, (os.cpu_count() or 16) - 8),
                    help="Parallel worker processes (default cpu_count-8, min 1)")
    ap.add_argument("--save", type=Path, default=None,
                    help="Write segments JSON to this path (default: stdout)")
    args = ap.parse_args()

    if not args.source.is_file():
        print(f"not found: {args.source}", file=sys.stderr)
        return 1

    segments = detect_segments(
        args.source, args.start, args.end,
        window_frames=args.window, stride=args.stride,
        min_gap=args.min_gap, min_consistency=args.min_consistency,
        run_len=args.run_len, snap_unit=args.snap,
        threads=args.threads,
    )

    print(f"\nFound {len(segments)} segment(s):", file=sys.stderr)
    for s in segments:
        print(f"  [{s['start_frame']:8d}, {s['end_frame']:8d})  phase={s['phase']}",
              file=sys.stderr)

    out = json.dumps(segments, indent=2)
    if args.save:
        args.save.write_text(out)
        print(f"Saved to {args.save}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
