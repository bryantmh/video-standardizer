"""Per-segment cadence-phase detector for 30fps -> 24fps telecine reversal.

Two modes:

  * ``unblend`` -- 5:4 weighted-blend pulldown:
        s[0..2] are clean, s[3] = 0.5*F_c + 0.5*F_d, s[4] = 0.5*F_d + 0.5*F_e.
        The interlaced positions (3 and 4 per cycle of 5 source frames) are
        identified by HIGH adjacent-row mean-abs-diff on motion pixels.
        The "phase" returned is the absolute source frame index (mod 5) of
        the F_a position (the first clean frame of each cycle).

  * ``dedupe`` -- duplicate-frame pulldown:
        One frame per cycle of 5 is duplicated (no blending). The duplicate
        pair is identified by LOW frame-to-frame mean-abs-diff (MAD).
        The "phase" returned is the absolute source frame index (mod 5) of
        the first frame of each duplicate pair.

Both modes share:
  * Sliding analysis windows of WINDOW_FRAMES source frames, advanced by STRIDE.
  * Per-window confidence based on a "gap" (mode-dependent score difference)
    and "consistency" (fraction of cycles where the detected pair holds).
  * Run-length filtering to suppress single-window phase noise.
  * Process-pool parallelism: each worker opens its own VapourSynth + lsmas
    decoder so multi-core scaling is real (no GIL or shared-clip contention).

Output: a list of {"start_frame", "end_frame", "phase"} dicts with absolute
source-frame indices and absolute (mod 5) phase. The renderer uses
``skip = (phase - seg_start) % 5`` to align each segment.
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


# Tunables. Defaults work well on test material; adjust if you get noisy
# detection on very low-motion content.
WINDOW_FRAMES = 50
STRIDE = 25
MIN_GAP = 2.0
MIN_CONSISTENCY = 0.6
RUN_LEN = 3


# ---------------------------------------------------------------------------
# Mode-specific window scoring. Both return (rel_phase, gap, consistency) where
# rel_phase is the offset within the window (mod 5) of the cadence-anchor
# frame; the caller adds the absolute window position to get the absolute
# source-frame phase.
# ---------------------------------------------------------------------------

def _field_score_for_frame(luma: np.ndarray, motion_mask: np.ndarray) -> float:
    """Mean |row[r] - row[r+1]| over rows where motion_mask has any motion.

    Vectorized: compute all row-pair diffs once, then mask to motion pixels.
    """
    row_pair_diffs = np.abs(luma[:-1] - luma[1:])
    mask_pair = motion_mask[:-1] | motion_mask[1:]
    row_motion_count = mask_pair.sum(axis=1)
    valid_rows = row_motion_count >= 10
    if not valid_rows.any():
        return 0.0
    masked_sum = (row_pair_diffs * mask_pair).sum(axis=1)
    per_row_mean = np.where(
        row_motion_count > 0,
        masked_sum / np.maximum(row_motion_count, 1),
        0.0,
    )
    return float(per_row_mean[valid_rows].mean())


def _unblend_phase_for_window(frames: list[np.ndarray]
                              ) -> tuple[int | None, float, float]:
    """Find the phase whose positions 3,4 are reliably the highest-scoring
    interlaced frames in each cycle. Returns offset of position-0 (F_a)
    within the window. See module docstring."""
    n = len(frames)
    if n < 10:
        return None, 0.0, 0.0
    # Per-frame field score uses motion mask = pixels that differ from neighbour.
    scores = []
    for i, img in enumerate(frames):
        nxt = frames[i + 1] if i + 1 < len(frames) else frames[i - 1]
        motion = np.abs(img.astype(np.float32) - nxt.astype(np.float32)) > 12.0
        scores.append(_field_score_for_frame(img.astype(np.float32), motion))

    best = (None, -1.0, 0.0, 0.0)  # (rel_phase, score, gap, consistency)
    for p in range(5):
        cycles = [scores[c:c + 5] for c in range(p, n - 4, 5)]
        if len(cycles) < 2:
            continue
        avg_int = float(np.mean([c[3] + c[4] for c in cycles])) / 2
        avg_cln = float(np.mean([c[0] + c[1] + c[2] for c in cycles])) / 3
        gap = avg_int - avg_cln
        good = sum(
            1 for c in cycles
            if set(sorted(range(5), key=lambda k: c[k], reverse=True)[:2]) == {3, 4}
        )
        consistency = good / len(cycles)
        score = gap + 5.0 * consistency
        if score > best[1] and avg_cln > 0:
            best = (p, score, gap, consistency)
    if best[0] is None or best[2] <= 0:
        return None, 0.0, 0.0
    return best[0], best[2], best[3]


def _dedupe_phase_for_window(frames: list[np.ndarray]
                             ) -> tuple[int | None, float, float]:
    """Find the (cycle_offset, pair_position) whose pair has consistently the
    lowest frame-to-frame MAD. Returns the rel offset of the FIRST frame of
    each duplicate pair within the window."""
    n = len(frames)
    if n < 10:
        return None, 0.0, 0.0
    mads = [
        float(np.mean(np.abs(
            frames[i].astype(np.float32) - frames[i + 1].astype(np.float32)
        )))
        for i in range(n - 1)
    ]

    best = (None, -1.0, 0.0, 0.0)
    for s in range(5):
        cycles = []
        for c in range(s, n - 4, 5):
            cycles.append([mads[c], mads[c + 1], mads[c + 2], mads[c + 3]])
        if len(cycles) < 2:
            continue
        arr = np.array(cycles)
        for p in range(4):
            col = arr[:, p]
            others_mean = (arr.sum(axis=1) - col) / 3.0
            gap = float((others_mean - col).mean())
            min_pos = np.argmin(arr, axis=1)
            consistency = float((min_pos == p).mean())
            score = gap + 10.0 * consistency
            if score > best[1]:
                rel = (s + p) % 5
                best = (rel, score, gap, consistency)
    if best[0] is None or best[2] <= 0:
        return None, 0.0, 0.0
    return best[0], best[2], best[3]


PHASE_SCORERS = {
    "unblend": _unblend_phase_for_window,
    "dedupe": _dedupe_phase_for_window,
}


# ---------------------------------------------------------------------------
# Window analysis
# ---------------------------------------------------------------------------

def _analyze_window(gray_clip, pos: int, window_frames: int,
                    min_gap: float, min_consistency: float, mode: str) -> dict:
    """Decode one window and return its phase sample dict."""
    frames = []
    for i in range(window_frames):
        f = gray_clip.get_frame(pos + i)
        frames.append(np.asarray(f[0], dtype=np.uint8))
    rel_phase, gap, consistency = PHASE_SCORERS[mode](frames)
    abs_phase = (pos + rel_phase) % 5 if rel_phase is not None else None
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
                  min_gap: float, min_consistency: float, mode: str,
                  worker_id: int = 0,
                  indexed_flags=None, done_counts=None) -> list[dict]:
    """Subprocess worker. Each opens its own VapourSynth core + decoder so
    parallelism is real."""
    import vapoursynth as vs  # noqa: F811 - re-import inside subprocess
    from vapoursynth import core as worker_core
    clip = worker_core.lsmas.LWLibavSource(source=src_path)
    gray = worker_core.resize.Bicubic(clip, format=vs.GRAY8, matrix_in_s="709")
    if indexed_flags is not None:
        indexed_flags[worker_id] = 1
    out = []
    for pos in positions:
        out.append(_analyze_window(
            gray, pos, window_frames, min_gap, min_consistency, mode))
        if done_counts is not None:
            done_counts[worker_id] += 1
    return out


# ---------------------------------------------------------------------------
# Top-level segment detection
# ---------------------------------------------------------------------------

def detect_segments(
    src: Path,
    *,
    mode: str,
    start_frame: int = 0,
    end_frame: int = 0,
    threads: int = 8,
    window_frames: int = WINDOW_FRAMES,
    stride: int = STRIDE,
    min_gap: float = MIN_GAP,
    min_consistency: float = MIN_CONSISTENCY,
    run_len: int = RUN_LEN,
) -> list[dict]:
    """Walk the source, return [{start_frame, end_frame, phase}] segments."""
    if mode not in PHASE_SCORERS:
        raise ValueError(f"unknown mode {mode!r}, expected one of {list(PHASE_SCORERS)}")

    clip = core.lsmas.LWLibavSource(source=str(src))
    if end_frame <= 0:
        end_frame = clip.num_frames
    end_frame = min(end_frame, clip.num_frames)

    print(f"Detecting {mode} cadence: frames [{start_frame}, {end_frame}), "
          f"window={window_frames}, stride={stride}, threads={threads}",
          file=sys.stderr)

    positions = list(range(start_frame, end_frame - window_frames + 1, stride))
    if not positions:
        raise RuntimeError("not enough source frames for any analysis window")

    total = len(positions)
    samples: list[dict | None] = [None] * total
    last_progress = -1

    if threads <= 1 or total < threads * 4:
        gray = core.resize.Bicubic(clip, format=vs.GRAY8, matrix_in_s="709")
        for i, pos in enumerate(positions):
            samples[i] = _analyze_window(
                gray, pos, window_frames, min_gap, min_consistency, mode)
            progress = int(100 * (i + 1) / total)
            if progress != last_progress:
                print(f"  detect: {i + 1}/{total} windows ({progress}%)",
                      file=sys.stderr, flush=True)
                last_progress = progress
    else:
        chunk_size = (total + threads - 1) // threads
        chunks = [positions[i:i + chunk_size] for i in range(0, total, chunk_size)]
        chunk_offsets = list(range(0, total, chunk_size))
        n_chunks = len(chunks)
        chunk_sizes = [len(c) for c in chunks]
        print(f"  Spawning {n_chunks} worker(s) -- waiting for source index...",
              file=sys.stderr, flush=True)

        with multiprocessing.Manager() as mgr:
            indexed_flags = mgr.list([0] * n_chunks)
            done_counts = mgr.list([0] * n_chunks)
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
                              min_gap, min_consistency, mode, wid,
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

    # Fill non-confident samples from nearest confident neighbour.
    ok_idx = [i for i, s in enumerate(samples) if s and s["ok"]]
    if not ok_idx:
        raise RuntimeError(
            f"no high-confidence {mode} cadence detections; "
            "the source may not match the expected pattern"
        )
    for i, s in enumerate(samples):
        if s is None:
            samples[i] = {"frame": positions[i], "phase": samples[ok_idx[0]]["phase"], "ok": False}
            s = samples[i]
        if not s["ok"]:
            nearest = min(ok_idx, key=lambda j: abs(j - i))
            s["phase"] = samples[nearest]["phase"]
            s["filled"] = True

    # Run-length filter to suppress single-window phase blips.
    smoothed = [s["phase"] for s in samples]
    n = len(smoothed)
    for i in range(n):
        prev = smoothed[max(0, i - run_len):i]
        nxt = smoothed[i + 1:min(n, i + run_len + 1)]
        if not prev or not nxt:
            continue
        prev_maj = Counter(prev).most_common(1)[0]
        next_maj = Counter(nxt).most_common(1)[0]
        if (prev_maj[0] == next_maj[0]
                and prev_maj[0] != smoothed[i]
                and prev_maj[1] >= 2 and next_maj[1] >= 2):
            smoothed[i] = prev_maj[0]
    for s, p in zip(samples, smoothed):
        s["phase"] = p

    # Coalesce same-phase samples into segments. The boundary between two
    # adjacent segments with phases P_old and P_new is snapped so that the
    # boundary frame is the first F_a / dup1 of the new segment's cadence
    # (i.e. boundary mod 5 == P_new). This removes the head-of-segment trim
    # the renderer would otherwise apply, and minimizes per-boundary frame
    # loss to just the tail of the previous segment's incomplete cycle.
    segments = []
    cur_start = samples[0]["frame"]
    cur_phase = samples[0]["phase"]
    for i in range(1, len(samples)):
        if samples[i]["phase"] != cur_phase:
            new_phase = samples[i]["phase"]
            raw_boundary = samples[i]["frame"]
            # Round raw_boundary down to the nearest frame whose mod-5 residue
            # equals new_phase. That way segment N+1's first frame is its own
            # cadence anchor (skip=0).
            boundary = raw_boundary - ((raw_boundary - new_phase) % 5)
            if boundary > cur_start:
                segments.append({
                    "start_frame": cur_start,
                    "end_frame": boundary,
                    "phase": cur_phase,
                })
                cur_start = boundary
                cur_phase = new_phase
    final_end = end_frame  # don't snap past the actual file end
    if final_end > cur_start:
        segments.append({
            "start_frame": cur_start,
            "end_frame": final_end,
            "phase": cur_phase,
        })

    # Extend the FIRST segment to cover [start_frame, segments[0].start_frame)
    # so we don't drop the head of the file before the first sample window.
    if segments and segments[0]["start_frame"] > start_frame:
        segments[0]["start_frame"] = start_frame

    return segments


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("source", type=Path, help="Source video file")
    ap.add_argument("--mode", choices=list(PHASE_SCORERS), required=True,
                    help="Cadence model to detect.")
    ap.add_argument("--start", type=int, default=0,
                    help="First source frame to analyze (default 0)")
    ap.add_argument("--end", type=int, default=0,
                    help="Last source frame (exclusive). 0 = end of file (default).")
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
        args.source,
        mode=args.mode,
        start_frame=args.start, end_frame=args.end,
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
