"""Reverse a 5:4 weighted-blend pulldown ("interlaced telecine") on 30fps
progressive sources that were originally 24fps film.

Pattern this fixes:
    Per group of 5 source frames:
        s[0]: F_a clean
        s[1]: F_b clean
        s[2]: F_c clean
        s[3]: 0.5*F_c + 0.5*F_d (interlaced; F_c lines + F_d lines)
        s[4]: 0.5*F_d + 0.5*F_e (interlaced; F_d lines + F_e lines)

Output: 24fps progressive, where the missing F_d is reconstructed
algebraically from b1 + b2 - 0.5*F_c - 0.5*F_e. The clean source frames
F_a/F_b/F_c are passed through untouched.

The output preserves the comb stripes on the reconstructed F_d frames
(roughly 1 in every 4 output frames). This is intentional -- AI upscalers
like Topaz Video AI / Proteus produce noticeably better results when fed
sharp, slightly-flawed frames vs pre-deinterlaced softened frames.

Phase tracking:
    Scene cuts in edited sources reset the cadence to a different phase.
    By default the script auto-detects per-segment cadence by sampling the
    field-vs-clean signature throughout the file. Pass --phase N to force
    a single global phase (faster; only correct for unedited sources).

Requires VapourSynth >= R65 with the lsmas plugin, plus ffmpeg in PATH.
Dependencies (in venv): numpy, pillow.

Usage:
    python unblend_telecine.py -i movie.mp4 -o movie.mov --audio
    python unblend_telecine.py -i movie.mp4 -o test.mp4 --codec h264 \\
        --start 12:30 --duration 5:00
    python unblend_telecine.py -i movie.mp4 -o movie.mov --phase 2  # uniform
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
VENV_VSPIPE = _SCRIPT_DIR / ".venv" / "Scripts" / "vspipe.exe"
VENV_PYTHON = _SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"
DETECT_SCRIPT = _SCRIPT_DIR / "detect_cadence.py"


def parse_timecode(tc: str | None) -> float | None:
    if tc is None:
        return None
    tc = tc.strip()
    if not tc:
        return None
    parts = tc.split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Cannot parse timecode {tc!r}")


def run_detector(
    src: Path,
    start_frame: int,
    end_frame: int,
    window: int,
    stride: int,
    min_gap: float,
    min_consistency: float,
    run_len: int,
    threads: int,
) -> list[dict]:
    """Invoke detect_cadence.py and return its segments JSON."""
    py = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        json_path = tf.name
    try:
        cmd = [py, str(DETECT_SCRIPT), str(src),
               "--start", str(start_frame),
               "--end", str(end_frame),
               "--window", str(window),
               "--stride", str(stride),
               "--min-gap", str(min_gap),
               "--min-consistency", str(min_consistency),
               "--run-len", str(run_len),
               "--threads", str(threads),
               "--save", json_path]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=None)
        if proc.returncode != 0:
            raise RuntimeError("detect_cadence.py failed")
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            os.unlink(json_path)
        except OSError:
            pass


VPY_TEMPLATE = r'''import vapoursynth as vs
from vapoursynth import core

SRC = r"{src}"
# Each segment: [start_frame, end_frame, phase] where phase is a
# source-absolute residue (frame_index mod 5 of the F_a position).
SEGMENTS = {segments_json}

clip = core.lsmas.LWLibavSource(source=SRC)
total = clip.num_frames


def unblend_segment(seg, skip):
    """Apply 5:4 weighted-blend pulldown reversal. `skip` = frames to drop
    from the front of `seg` so seg[skip] is the first F_a (cadence aligned)."""
    if skip:
        seg = seg[skip:]
    usable = (seg.num_frames // 5) * 5
    if usable < 5:
        return None
    seg = seg[:usable]
    Fa = core.std.SelectEvery(seg, cycle=5, offsets=[0])
    Fb = core.std.SelectEvery(seg, cycle=5, offsets=[1])
    Fc = core.std.SelectEvery(seg, cycle=5, offsets=[2])
    b1 = core.std.SelectEvery(seg, cycle=5, offsets=[3])
    b2 = core.std.SelectEvery(seg, cycle=5, offsets=[4])
    next_seg = seg[5:]
    usable_next = (next_seg.num_frames // 5) * 5
    if usable_next < 5:
        # Last group has no successor; fall back to b2 as F_e stand-in.
        Fe = b2
    else:
        next_seg = next_seg[:usable_next]
        Fe = core.std.SelectEvery(next_seg, cycle=5, offsets=[0])
    n = min(Fa.num_frames, Fb.num_frames, Fc.num_frames,
            b1.num_frames, b2.num_frames, Fe.num_frames)
    if n == 0:
        return None
    Fa = Fa[:n]; Fb = Fb[:n]; Fc = Fc[:n]
    b1 = b1[:n]; b2 = b2[:n]; Fe = Fe[:n]
    Fd = core.std.Expr(
        clips=[b1, b2, Fc, Fe],
        expr="x y + z 0.5 * - a 0.5 * -",
    )
    return core.std.Interleave([Fa, Fb, Fc, Fd])


parts = []
for seg_start, seg_end, phase in SEGMENTS:
    seg_start = max(0, seg_start)
    seg_end = min(total, seg_end)
    if seg_end <= seg_start:
        continue
    seg = clip[seg_start:seg_end]
    # phase is an absolute residue class. Skip = how many frames to drop
    # from seg's front so seg[skip] lands on F_a.
    skip = (phase - seg_start) % 5
    out = unblend_segment(seg, skip)
    if out is not None and out.num_frames > 0:
        parts.append(out)

if not parts:
    raise RuntimeError("no usable segments after phase alignment")

out = parts[0]
for p in parts[1:]:
    out = out + p

out = core.std.AssumeFPS(out, fpsnum=24000, fpsden=1001)
out.set_output()
'''


def build_vpy(src: Path, segments: list) -> str:
    seg_list = [[s["start_frame"], s["end_frame"], s["phase"]] for s in segments]
    return VPY_TEMPLATE.format(
        src=str(src),
        segments_json=json.dumps(seg_list),
    )


def ffmpeg_args_for_codec(codec: str, output: Path) -> list[str]:
    if codec == "prores":
        return [
            "-c:v", "prores_ks",
            "-profile:v", "2",
            "-vendor", "apl0",
            "-pix_fmt", "yuv422p10le",
            "-c:a", "copy",
            str(output),
        ]
    if codec == "prores_hq":
        return [
            "-c:v", "prores_ks",
            "-profile:v", "3",
            "-vendor", "apl0",
            "-pix_fmt", "yuv422p10le",
            "-c:a", "copy",
            str(output),
        ]
    if codec == "h264":
        return [
            "-c:v", "libx264",
            "-crf", "18",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output),
        ]
    raise ValueError(f"Unknown codec: {codec}")


def _stream_progress(ffmpeg_proc, total_frames: int) -> None:
    """Read ffmpeg's progress stream and print a clean one-line progress
    update each time it ticks. Reads `key=value` lines from `-progress pipe:2`
    on stderr; emits "render: N/M frames (PP%)  speed=Sx".
    """
    import re
    last_pct = -1
    last_print_time = 0.0
    import time
    pat = re.compile(r"^(\w+)=(.*)$")
    state = {}
    while True:
        line = ffmpeg_proc.stderr.readline()
        if not line:
            break
        line = line.rstrip()
        m = pat.match(line)
        if not m:
            # Non-key=value line: pass through (errors, warnings).
            print(line, file=sys.stderr)
            continue
        key, val = m.group(1), m.group(2)
        state[key] = val
        if key == "progress":
            try:
                frame = int(state.get("frame", "0"))
            except ValueError:
                frame = 0
            speed = state.get("speed", "?")
            pct = int(100 * frame / total_frames) if total_frames > 0 else 0
            now = time.time()
            # Print once per percent change OR at most every 2 seconds.
            if pct != last_pct or (now - last_print_time) >= 2.0:
                print(f"  render: {frame}/{total_frames} frames ({pct}%) speed={speed}",
                      file=sys.stderr, flush=True)
                last_pct = pct
                last_print_time = now


def main() -> int:
    p = argparse.ArgumentParser(
        description="Reverse 5:4 weighted-blend pulldown with per-segment phase tracking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-i", "--input", required=True, type=Path, help="Source video file")
    p.add_argument("-o", "--output", default=None, type=Path,
                   help="Output video file. Default: <input_stem> [unblended]<ext> "
                        "next to the source (.mov for ProRes, .mp4 for H.264).")
    p.add_argument("--start", default=None,
                   help="Start time (HH:MM:SS / MM:SS / seconds). Default: file start.")
    p.add_argument("--duration", default=None,
                   help="Duration (HH:MM:SS / MM:SS / seconds). Default: rest of file.")
    p.add_argument("--phase", type=int, default=None, choices=range(5),
                   help="Force a single global phase 0..4 (skips auto-detect). "
                        "Use only on sources known to have uniform cadence (no edits).")
    p.add_argument("--codec", default="prores",
                   choices=["prores", "prores_hq", "h264"],
                   help="Output codec (default: prores).")
    p.add_argument("--audio", action="store_true",
                   help="Mux source audio (with the trim offset applied if --start used).")
    p.add_argument("--save-segments", type=Path, default=None,
                   help="Write detected segments JSON to this path.")
    p.add_argument("--load-segments", type=Path, default=None,
                   help="Skip detection; load segments from a previous --save-segments JSON.")
    p.add_argument("--detect-window", type=int, default=50,
                   help="Detection window size in source frames (default 50).")
    p.add_argument("--detect-stride", type=int, default=25,
                   help="Detection stride in source frames (default 25).")
    p.add_argument("--detect-min-gap", type=float, default=4.0,
                   help="Detection minimum gap to count a window confident (default 4.0).")
    p.add_argument("--detect-min-consistency", type=float, default=0.7,
                   help="Detection minimum consistency 0..1 (default 0.7).")
    p.add_argument("--detect-run-len", type=int, default=3,
                   help="Detection run-length filter (default 3).")
    p.add_argument("--threads", type=int,
                   default=max(1, os.cpu_count() - 8),
                   help="Parallel worker processes for cadence detection "
                        "(default cpu_count-8, min 1).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print segments and the generated .vpy without rendering.")
    args = p.parse_args()

    if not args.input.is_file():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 1
    if args.output is None:
        ext = ".mp4" if args.codec == "h264" else ".mov"
        args.output = args.input.with_name(f"{args.input.stem} [unblended]{ext}")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Determine source FPS via ffprobe so we can convert start/duration.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate,nb_frames",
         "-show_entries", "format=duration",
         "-of", "json", str(args.input)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    fr = info["streams"][0]["r_frame_rate"]
    fps_num, fps_den = (int(x) for x in fr.split("/"))
    fps = fps_num / fps_den
    nb_frames = int(info["streams"][0].get("nb_frames") or 0)
    if nb_frames == 0:
        nb_frames = int(float(info["format"]["duration"]) * fps)

    start_sec = parse_timecode(args.start)
    duration_sec = parse_timecode(args.duration)
    start_frame = int(start_sec * fps) if start_sec is not None else 0
    end_frame = nb_frames
    if duration_sec is not None:
        end_frame = min(nb_frames, start_frame + int(duration_sec * fps))

    # Decide segments.
    if args.load_segments is not None:
        with open(args.load_segments, "r", encoding="utf-8") as f:
            segments = json.load(f)
        print(f"Loaded {len(segments)} segments from {args.load_segments}",
              file=sys.stderr)
    elif args.phase is not None:
        segments = [{"start_frame": start_frame, "end_frame": end_frame, "phase": args.phase}]
        print(f"Forcing global phase={args.phase} on frames "
              f"[{start_frame}, {end_frame}).", file=sys.stderr)
    else:
        segments = run_detector(
            args.input, start_frame, end_frame,
            window=args.detect_window, stride=args.detect_stride,
            min_gap=args.detect_min_gap,
            min_consistency=args.detect_min_consistency,
            run_len=args.detect_run_len,
            threads=args.threads,
        )
        # Clip detected segments to the requested trim window.
        clipped = []
        for s in segments:
            sf = max(s["start_frame"], start_frame)
            ef = min(s["end_frame"], end_frame)
            if ef > sf:
                clipped.append({"start_frame": sf, "end_frame": ef, "phase": s["phase"]})
        segments = clipped

    print(f"\nProcessing {len(segments)} segment(s):", file=sys.stderr)
    for s in segments[:30]:
        ts_start = s["start_frame"] / fps
        ts_end = s["end_frame"] / fps
        print(f"  [{ts_start:8.1f}s, {ts_end:8.1f}s)  phase={s['phase']}",
              file=sys.stderr)
    if len(segments) > 30:
        print(f"  ... ({len(segments) - 30} more)", file=sys.stderr)

    if args.save_segments:
        with open(args.save_segments, "w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2)
        print(f"Saved segments to {args.save_segments}", file=sys.stderr)

    vpy_text = build_vpy(args.input, segments)
    if args.dry_run:
        print("\n# Generated .vpy:\n")
        print(vpy_text)
        print("\n# ffmpeg args:")
        print(" ".join(shlex.quote(a) for a in ffmpeg_args_for_codec(args.codec, args.output)))
        return 0

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".vpy", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(vpy_text)
        vpy_path = tf.name

    # Estimate the total output frames so progress is a real percentage.
    # Each segment yields (usable_frames // 5) * 4 output frames.
    expected_out_frames = 0
    for seg in segments:
        seg_len = seg["end_frame"] - seg["start_frame"]
        seg_phase_skip = (seg["phase"] - seg["start_frame"]) % 5
        usable = max(0, seg_len - seg_phase_skip)
        expected_out_frames += (usable // 5) * 4

    vspipe = str(VENV_VSPIPE) if VENV_VSPIPE.is_file() else "vspipe"
    try:
        ff_args = ffmpeg_args_for_codec(args.codec, args.output)
        # -loglevel error: drop the chapter dump and stream banners.
        # -progress pipe:2: machine-readable progress key=value lines on stderr.
        # -nostats: drop ffmpeg's default frame=... line so we don't interleave with progress.
        ff_input_args = ["-y", "-loglevel", "error", "-nostats",
                         "-progress", "pipe:2",
                         "-f", "yuv4mpegpipe", "-i", "-"]
        if args.audio:
            audio_input = ["-i", str(args.input)]
            audio_map = ["-map", "0:v:0", "-map", "1:a:0?"]
            audio_extra = []
            if start_sec:
                audio_extra += ["-ss", str(start_sec)]
            if duration_sec:
                audio_extra += ["-t", str(duration_sec)]
            ff_input_args = (["-y", "-loglevel", "error", "-nostats",
                              "-progress", "pipe:2",
                              "-f", "yuv4mpegpipe", "-i", "-"]
                             + audio_extra + audio_input + audio_map)

        vspipe_cmd = [vspipe, "-c", "y4m", vpy_path, "-"]
        ffmpeg_cmd = ["ffmpeg", *ff_input_args, *ff_args]

        print(f"\nvspipe: {' '.join(shlex.quote(a) for a in vspipe_cmd)}", file=sys.stderr)
        print(f"ffmpeg: {' '.join(shlex.quote(a) for a in ffmpeg_cmd)}", file=sys.stderr)
        print(f"\nRendering {expected_out_frames} frames "
              f"({expected_out_frames / 23.976:.0f}s @ 23.976fps)...",
              file=sys.stderr)

        # vspipe -> ffmpeg pipeline. ffmpeg's stderr is captured for progress
        # parsing; vspipe's stderr (frame=N progress) goes straight to terminal.
        v = subprocess.Popen(vspipe_cmd, stdout=subprocess.PIPE)
        try:
            f = subprocess.Popen(ffmpeg_cmd, stdin=v.stdout,
                                 stderr=subprocess.PIPE,
                                 text=True, bufsize=1)
            v.stdout.close()
            _stream_progress(f, expected_out_frames)
            ff_rc = f.wait()
        finally:
            v.wait()
        if v.returncode != 0:
            print(f"vspipe exited {v.returncode}", file=sys.stderr)
            return v.returncode
        if ff_rc != 0:
            print(f"ffmpeg exited {ff_rc}", file=sys.stderr)
            return ff_rc
        print(f"Wrote {args.output}")
        return 0
    finally:
        try:
            os.unlink(vpy_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
