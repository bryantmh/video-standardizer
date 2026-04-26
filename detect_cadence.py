"""Robust per-segment cadence phase detector for 5:4 weighted-blend pulldown.

Approach:
1. Walk the source in WINDOW_FRAMES-frame windows, advancing by STRIDE frames.
2. For each frame, compute a "field_score": mean abs-diff of adjacent rows
   restricted to motion pixels (pixels that differ from the next frame). On
   a progressive frame this is low; on a frame whose top/bottom halves come
   from different time moments it is high.
3. Within each window, find which 5-frame phase produces the cleanest
   "HH pair at positions 3,4" pattern - phase that maximizes
   (avg(field_score[3,4]) - avg(field_score[0,1,2])).
4. Score each phase choice by (a) the gap between interlaced and clean
   averages and (b) how consistently the same two adjacent positions are
   the HH pair across all 5-frame cycles in the window.
5. Smooth the per-window phase trace: only emit a phase change when the
   new phase is supported by RUN_LEN consecutive windows.
6. Snap segment boundaries to source-frame multiples of 5 closest to where
   the smoothed trace transitions.

Usage:
    python detect_cadence.py <source.mp4> [--start SEC] [--duration SEC]
                             [--window 25] [--stride 5] [--save segments.json]

The output segments JSON is a list of {"start_frame": int, "end_frame": int,
"phase": int} records, where start_frame / end_frame are absolute SOURCE
frame indices (not output indices).
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


def field_score_for_frame(luma: np.ndarray, motion_mask: np.ndarray) -> float:
    """Mean |row[r] - row[r+1]| over rows where motion_mask has any True
    pixels in either row r or r+1. Returns 0.0 if no motion."""
    # Vectorized: compute all row-pair diffs at once, then mask to motion.
    row_pair_diffs = np.abs(luma[:-1] - luma[1:])           # shape (h-1, w)
    mask_pair = motion_mask[:-1] | motion_mask[1:]          # shape (h-1, w)
    row_motion_count = mask_pair.sum(axis=1)                # per-row motion-pixel count
    valid_rows = row_motion_count >= 10
    if not valid_rows.any():
        return 0.0
    # Per-row mean of diffs over motion pixels.
    masked_sum = (row_pair_diffs * mask_pair).sum(axis=1)   # (h-1,)
    per_row_mean = np.where(
        row_motion_count > 0,
        masked_sum / np.maximum(row_motion_count, 1),
        0.0,
    )
    return float(per_row_mean[valid_rows].mean())


def field_scores_for_window(gray_clip, start_frame: int, count: int) -> list[float]:
    """Compute per-frame field scores for `count` frames starting at start_frame."""
    luma = []
    for i in range(count):
        f = gray_clip.get_frame(start_frame + i)
        luma.append(np.asarray(f[0], dtype=np.float32))
    scores = []
    for i, img in enumerate(luma):
        if i + 1 < len(luma):
            motion = np.abs(img - luma[i + 1]) > 12.0
        else:
            motion = np.abs(img - luma[i - 1]) > 12.0
        scores.append(field_score_for_frame(img, motion))
    return scores


def best_phase_for_scores(scores: list[float]) -> tuple[int | None, float, float]:
    """Find the phase 0..4 that best fits "interlaced frames at positions 3,4
    of each 5-cycle". Returns (phase, gap, consistency).

    `gap` is (avg interlaced - avg clean) score; higher = more confident.
    `consistency` is the fraction of cycles where positions 3 AND 4 are the
    top-2 highest-scoring positions in their cycle; near 1.0 = very consistent.
    """
    n = len(scores)
    if n < 10:
        return None, 0.0, 0.0
    best = (None, -1.0, 0.0)
    for p in range(5):
        # Cycles starting at offset p, p+5, p+10, ...
        cycles = []
        for c_start in range(p, n - 4, 5):
            cycles.append(scores[c_start:c_start + 5])
        if len(cycles) < 2:
            continue
        # Compute interlaced/clean averages.
        interlaced = [c[3] + c[4] for c in cycles]
        clean = [c[0] + c[1] + c[2] for c in cycles]
        avg_interlaced = float(np.mean(interlaced)) / 2
        avg_clean = float(np.mean(clean)) / 3
        gap = avg_interlaced - avg_clean
        # Consistency: in how many cycles are positions 3 and 4 the top-2?
        good = 0
        for c in cycles:
            ranking = sorted(range(5), key=lambda k: c[k], reverse=True)
            if set(ranking[:2]) == {3, 4}:
                good += 1
        consistency = good / len(cycles)
        # Composite score: prefer high gap AND high consistency.
        score = gap + 5.0 * consistency  # consistency is in [0,1], weight it heavily
        if score > best[1] and avg_clean > 0:
            best = (p, score, gap, consistency)
    if best[0] is None:
        return None, 0.0, 0.0
    return best[0], best[2], best[3]


def _analyze_window(gray, pos: int, window_frames: int,
                    min_gap: float, min_consistency: float) -> dict:
    """Worker: compute scores + phase for a single window."""
    scores = field_scores_for_window(gray, pos, window_frames)
    win_phase, gap, consistency = best_phase_for_scores(scores)
    if win_phase is not None:
        abs_phase = (pos + win_phase) % 5
    else:
        abs_phase = None
    return {
        "frame": pos,
        "phase": abs_phase,
        "gap": gap,
        "consistency": consistency,
        "ok": abs_phase is not None and gap >= min_gap and consistency >= min_consistency,
    }


def _worker_chunk(src_path: str, positions: list[int], window_frames: int,
                  min_gap: float, min_consistency: float,
                  worker_id: int = 0,
                  indexed_flags=None, done_counts=None) -> list[dict]:
    """Subprocess worker: open its own VapourSynth core, analyze a chunk of
    window positions, return the resulting sample dicts.

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
    threads: int = 8,
) -> list[dict]:
    """Walk the source, return a list of {start_frame, end_frame, phase}."""
    # We need num_frames for sizing decisions; open a temporary clip in this
    # process. Workers will reopen their own clips.
    clip = core.lsmas.LWLibavSource(source=str(src))
    if end_frame <= 0:
        end_frame = clip.num_frames
    end_frame = min(end_frame, clip.num_frames)

    print(f"Detecting cadence: frames [{start_frame}, {end_frame}), "
          f"window={window_frames}, stride={stride}, threads={threads}",
          file=sys.stderr)

    # Build the list of window start positions up front.
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
            samples[i] = _analyze_window(gray, pos, window_frames,
                                         min_gap, min_consistency)
            progress = int(100 * (i + 1) / total)
            if progress != last_progress:
                print(f"  detect: {i + 1}/{total} windows ({progress}%)",
                      file=sys.stderr, flush=True)
                last_progress = progress
    else:
        # Process-pool path: each worker opens its own VS + lsmas, processes a
        # contiguous chunk of windows. Sequential decode within a chunk keeps
        # lsmas fast; parallelism comes from running N decoders in N processes.
        chunk_size = (total + threads - 1) // threads
        chunks = [positions[i:i + chunk_size] for i in range(0, total, chunk_size)]
        chunk_offsets = [i for i in range(0, total, chunk_size)]
        n_chunks = len(chunks)
        chunk_sizes = [len(c) for c in chunks]
        print(f"  Spawning {n_chunks} worker(s) — waiting for source index...",
              file=sys.stderr, flush=True)

        with multiprocessing.Manager() as mgr:
            indexed_flags = mgr.list([0] * n_chunks)  # 0=indexing, 1=indexed
            done_counts   = mgr.list([0] * n_chunks)  # windows completed per worker

            stop_event = threading.Event()

            def _monitor():
                interval = 6.0
                while not stop_event.is_set():
                    time.sleep(interval)
                    parts = []
                    total_done = 0
                    for i in range(n_chunks):
                        done = done_counts[i]
                        total_done += done
                        sz = chunk_sizes[i]
                        if indexed_flags[i] == 0:
                            parts.append(f"W{i+1}:idx")
                        elif done >= sz:
                            parts.append(f"W{i+1}:done")
                        else:
                            pct = int(100 * done / sz) if sz else 100
                            parts.append(f"W{i+1}:{pct}%")
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

    # Smooth: replace not-ok samples with the nearest ok phase.
    ok_idx = [i for i, s in enumerate(samples) if s["ok"]]
    if not ok_idx:
        raise RuntimeError("no high-confidence cadence detections; "
                           "the source may not match this 5:4 cadence")
    for i, s in enumerate(samples):
        if not s["ok"]:
            nearest = min(ok_idx, key=lambda j: abs(j - i))
            s["phase"] = samples[nearest]["phase"]
            s["filled"] = True

    # Run-length filter: only keep a phase change if it persists for run_len
    # consecutive samples. Single-sample blips become noise to be replaced.
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

    # Coalesce adjacent same-phase samples into segments. Boundaries are
    # snapped to the nearest multiple of `snap_unit` source frames.
    segments = []
    cur_start = samples[0]["frame"]
    cur_phase = samples[0]["phase"]
    for i in range(1, len(samples)):
        if samples[i]["phase"] != cur_phase:
            boundary = samples[i]["frame"]
            boundary = (boundary // snap_unit) * snap_unit
            if boundary > cur_start:
                segments.append({"start_frame": cur_start, "end_frame": boundary, "phase": cur_phase})
                cur_start = boundary
                cur_phase = samples[i]["phase"]
    # Final segment runs to end_frame.
    final_end = (end_frame // snap_unit) * snap_unit
    if final_end > cur_start:
        segments.append({"start_frame": cur_start, "end_frame": final_end, "phase": cur_phase})

    return segments


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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
                    help="Minimum interlaced-vs-clean gap to count a window as confident (default 2.0)")
    ap.add_argument("--min-consistency", type=float, default=0.6,
                    help="Minimum fraction of 5-frame cycles where positions 3,4 are the "
                         "two highest-scoring positions (default 0.6)")
    ap.add_argument("--run-len", type=int, default=3,
                    help="Phase change must persist N consecutive windows to be real (default 3)")
    ap.add_argument("--snap", type=int, default=5,
                    help="Snap segment boundaries to multiples of N source frames (default 5)")
    ap.add_argument("--threads", type=int,
                    default=max(1, os.cpu_count() - 8),
                    help="Parallel worker processes (default cpu_count-8, min 1). Each opens "
                         "its own decoder so the speedup is real (no GIL contention).")
    ap.add_argument("--save", type=Path, default=None,
                    help="Write segments JSON to this path (default: stdout only)")
    args = ap.parse_args()

    if not args.source.is_file():
        print(f"not found: {args.source}", file=sys.stderr); return 1

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
