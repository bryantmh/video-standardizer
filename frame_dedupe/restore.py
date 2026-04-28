"""Restore 30fps -> 24fps telecine sources to 24fps progressive.

Two cadence patterns are supported via ``--mode``:

  * ``unblend`` -- weighted-blend pulldown (3 clean + 2 interlaced per cycle of 5):
        s[0..2] are clean F_a/F_b/F_c
        s[3] = 0.5*F_c + 0.5*F_d   (interlaced)
        s[4] = 0.5*F_d + 0.5*F_e   (interlaced)
    F_d is reconstructed algebraically: b1 + b2 - 0.5*F_c - 0.5*F_e.
    Output: [F_a, F_b, F_c, F_d_recovered] per cycle.

  * ``dedupe`` -- duplicate-frame pulldown (4 unique + 1 dup per cycle of 5):
        Phase 0: [A, A, B, C, D]
        Phase 1: [A, B, B, C, D]
        Phase 2: [A, B, C, C, D]
        Phase 3: [A, B, C, D, D]
    Output: dropping the second frame of each duplicate pair.

Phase tracking:
    Scene cuts in edited sources reset the cadence to a different phase.
    By default the script auto-detects per-segment cadence by sampling the
    field-vs-clean (unblend) or pair-MAD (dedupe) signature throughout the
    file. Pass --phase N to force a single global phase if the source has
    uniform cadence.

Audio: source audio is always passed through (with --start/--duration
offset applied if those are set). The output preserves the comb stripes
on reconstructed F_d frames in unblend mode; this is intentional, AI
upscalers like Topaz Video AI / Proteus produce noticeably better results
when fed sharp, slightly-flawed frames vs pre-deinterlaced softened frames.

Requires VapourSynth >= R65 with the lsmas plugin, plus ffmpeg in PATH.
Dependencies (in venv): numpy.

Usage:
    python restore.py -i movie.mp4 --mode unblend
    python restore.py -i movie.mp4 --mode dedupe --codec h264 \\
        --start 12:30 --duration 5:00
    python restore.py -i movie.mp4 --mode unblend --phase 2  # uniform cadence
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markup import escape as rich_escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

_SCRIPT_DIR = Path(__file__).resolve().parent
_TOOLS_DIR = _SCRIPT_DIR.parent
VENV_VSPIPE = _TOOLS_DIR / ".venv" / "Scripts" / "vspipe.exe"
VENV_PYTHON = _TOOLS_DIR / ".venv" / "Scripts" / "python.exe"
DETECT_SCRIPT = _SCRIPT_DIR / "detect.py"

console = Console(stderr=True)


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


# Detector progress emission patterns we recognise.
#   "  detect: 23/180 windows (13%)"          -- sequential mode
#   "  detect [37%] W1:idx  W2:80%  W3:done"  -- parallel mode w/ monitor thread
_DETECT_PCT_RE = re.compile(r"detect[\s:\[]+.*?(\d+)\s*%")
_DETECT_NM_RE = re.compile(r"detect:\s*(\d+)/(\d+)")


def run_detector(
    src: Path, *, mode: str,
    start_frame: int, end_frame: int,
    threads: int,
    progress: Progress,
    task_id,
) -> list[dict]:
    """Invoke detect.py in a subprocess; stream its stderr into the Rich
    progress bar; return the segments JSON.

    `progress` is the parent's Rich Progress and `task_id` the detect task
    we're filling. The detector may emit either the sequential
    ``detect: N/M`` form or the parallel ``detect [PCT%]`` form; we parse
    both and update the bar accordingly. Non-progress lines pass through
    to the console.
    """
    py = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        json_path = tf.name
    try:
        cmd = [py, str(DETECT_SCRIPT), str(src),
               "--mode", mode,
               "--start", str(start_frame),
               "--end", str(end_frame),
               "--threads", str(threads),
               "--save", json_path]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        progress.update(task_id, total=100, completed=0)
        for line in proc.stderr:
            line = line.rstrip()
            if not line:
                continue
            m_nm = _DETECT_NM_RE.search(line)
            if m_nm:
                done, total = int(m_nm.group(1)), int(m_nm.group(2))
                progress.update(task_id, total=total, completed=done)
                continue
            m_pct = _DETECT_PCT_RE.search(line)
            if m_pct:
                pct = int(m_pct.group(1))
                progress.update(task_id, total=100, completed=pct)
                continue
            # Non-progress line: surface it (errors, segment list, etc.).
            console.print(line, highlight=False, markup=False)
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"detect.py exited {rc}")
        # Snap the bar to its declared total in case the last progress line
        # lagged behind the actual completion.
        for t in progress.tasks:
            if t.id == task_id and t.total is not None:
                progress.update(task_id, completed=t.total)
                break
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        try:
            os.unlink(json_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# VapourSynth template. Mode-specific transform is filled in by build_vpy.
# ---------------------------------------------------------------------------

VPY_TEMPLATE = r'''import vapoursynth as vs
from vapoursynth import core

SRC = r"{src}"
# [start_frame, end_frame, phase] where phase is the absolute source-frame
# residue (mod 5) of the cadence anchor: F_a in unblend mode, dup1 in dedupe.
SEGMENTS = {segments_json}

clip = core.lsmas.LWLibavSource(source=SRC)
total = clip.num_frames


def transform_segment(seg, skip):
    """Apply the per-cycle transform after skipping `skip` frames so seg[skip]
    is the cadence anchor.

    Tail frames (0-4 per segment) that don't complete a full cycle are handled
    with round(tail * 4/5) passthrough — i.e. [0,1,2,2,3] frames kept for
    tail sizes [0,1,2,3,4].  This is the same 4:5 ratio as the main transform,
    so the total output frame count matches the expected 4/5 of the source and
    A/V sync is maintained over the full film.
    """
    if skip:
        seg = seg[skip:]
    n = seg.num_frames
    usable = (n // 5) * 5
    tail_count = n - usable
    tail_keep = [0, 1, 2, 2, 3][tail_count]
    if usable < 5:
        return seg[:tail_keep] if tail_keep > 0 else None
    full_seg = seg
    seg = seg[:usable]
{transform_body}
    if tail_keep > 0:
        result = result + full_seg[usable:usable + tail_keep]
    return result


parts = []
for seg_start, seg_end, phase in SEGMENTS:
    seg_start = max(0, seg_start)
    seg_end = min(total, seg_end)
    if seg_end <= seg_start:
        continue
    seg = clip[seg_start:seg_end]
    skip = (phase - seg_start) % 5
    out = transform_segment(seg, skip)
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

# Mode-specific transform bodies. Indented to match the template's
# "transform_segment" function body.
TRANSFORM_BODY = {
    # 5:4 weighted-blend pulldown -> recover F_d algebraically.
    "unblend": """\
    nc = seg.num_frames // 5
    Fa = core.std.SelectEvery(seg, cycle=5, offsets=[0])
    Fb = core.std.SelectEvery(seg, cycle=5, offsets=[1])
    Fc = core.std.SelectEvery(seg, cycle=5, offsets=[2])
    b1 = core.std.SelectEvery(seg, cycle=5, offsets=[3])
    b2 = core.std.SelectEvery(seg, cycle=5, offsets=[4])
    # For all cycles except the last, Fe = F_a of the next cycle (exact).
    # For the last cycle there is no following cycle within this segment,
    # so use b2 as a stand-in for Fe (introduces a small error on the
    # reconstructed F_d of the last cycle, but avoids losing the cycle
    # entirely as the old code did).
    Fe_last = b2[nc - 1:nc]
    if nc >= 2:
        Fe_main = core.std.SelectEvery(seg[5:], cycle=5, offsets=[0])[:nc - 1]
        Fe = Fe_main + Fe_last
    else:
        Fe = Fe_last
    Fd = core.std.Expr(
        clips=[b1, b2, Fc, Fe],
        expr="x y + z 0.5 * - a 0.5 * -",
    )
    result = core.std.Interleave([Fa, Fb, Fc, Fd])""",

    # Duplicate-frame pulldown -> drop dup2 from each cycle.
    # After skip alignment the cycle is [dup1, dup2, uniq1, uniq2, uniq3].
    # Keep [dup1, uniq1, uniq2, uniq3] = offsets [0, 2, 3, 4].
    "dedupe": """\
    result = core.std.SelectEvery(seg, cycle=5, offsets=[0, 2, 3, 4])""",
}


def build_vpy(src: Path, segments: list, mode: str) -> str:
    seg_list = [[s["start_frame"], s["end_frame"], s["phase"]] for s in segments]
    return VPY_TEMPLATE.format(
        src=str(src),
        segments_json=json.dumps(seg_list),
        transform_body=TRANSFORM_BODY[mode],
    )


def ffmpeg_args_for_codec(codec: str, output: Path) -> list[str]:
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "2", "-vendor", "apl0",
                "-pix_fmt", "yuv422p10le", "-c:a", "copy", str(output)]
    if codec == "prores_hq":
        return ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0",
                "-pix_fmt", "yuv422p10le", "-c:a", "copy", str(output)]
    if codec == "h264":
        return ["-c:v", "libx264", "-crf", "18", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "copy", str(output)]
    raise ValueError(f"Unknown codec: {codec}")


_FFMPEG_KV_RE = re.compile(r"^(\w+)=(.*)$")


def _stream_render_progress(ffmpeg_proc, total_frames: int,
                            progress: Progress, task_id) -> None:
    """Stream ffmpeg ``-progress pipe:2`` output into a Rich progress bar.

    ffmpeg emits key=value lines in batches separated by a final
    ``progress=continue`` (or ``progress=end``). We accumulate state
    and push the latest frame count into the bar each time we see
    a ``progress`` key. Non-key=value lines (errors / warnings)
    pass through to the console.
    """
    state: dict[str, str] = {}
    progress.update(task_id, total=total_frames, completed=0)
    while True:
        line = ffmpeg_proc.stderr.readline()
        if not line:
            break
        line = line.rstrip()
        if not line:
            continue
        m = _FFMPEG_KV_RE.match(line)
        if not m:
            console.print(line, highlight=False, markup=False)
            continue
        key, val = m.group(1), m.group(2)
        state[key] = val
        if key == "progress":
            try:
                frame = int(state.get("frame", "0"))
            except ValueError:
                frame = 0
            speed = state.get("speed", "?")
            progress.update(
                task_id,
                completed=min(frame, total_frames),
                speed=speed,
            )


def main() -> int:
    p = argparse.ArgumentParser(
        description="Restore 30fps -> 24fps telecine sources (unblend or dedupe).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-i", "--input", required=True, type=Path, help="Source video file")
    p.add_argument("-o", "--output", default=None, type=Path,
                   help="Output file. Default: <stem> [<mode>]<ext> next to source "
                        "(.mov for ProRes, .mp4 for H.264).")
    p.add_argument("--mode", choices=list(TRANSFORM_BODY), required=True,
                   help="Cadence model to reverse (unblend or dedupe).")
    p.add_argument("--start", default=None,
                   help="Start time (HH:MM:SS / MM:SS / seconds). Default: file start.")
    p.add_argument("--duration", default=None,
                   help="Duration (HH:MM:SS / MM:SS / seconds). Default: rest of file.")
    p.add_argument("--phase", type=int, default=None, choices=range(5),
                   help="Force a single global phase 0..4 (skips auto-detect). "
                        "Use only for sources with uniform cadence (no edits).")
    p.add_argument("--codec", default="prores",
                   choices=["prores", "prores_hq", "h264"],
                   help="Output codec (default: prores).")
    p.add_argument("--threads", type=int,
                   default=max(1, (os.cpu_count() or 16) - 8),
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
        args.output = args.input.with_name(f"{args.input.stem} [{args.mode}]{ext}")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Probe source FPS and frame count for timecode conversion.
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

    # Build a single Rich Progress with two stacked tasks: detect + render.
    # Both stay visible at the bottom while logs scroll above.
    progress = Progress(
        TextColumn("[bold]{task.description}", justify="right"),
        BarColumn(bar_width=None),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        MofNCompleteColumn(),
        TextColumn("speed={task.fields[speed]}", justify="right"),
        TimeElapsedColumn(),
        TextColumn("ETA"),
        TimeRemainingColumn(),
        console=console,
        expand=True,
    )
    detect_task = progress.add_task("Detect", total=100, speed="-",
                                    visible=(args.phase is None))
    render_task = progress.add_task("Render", total=1, speed="-")

    with Live(progress, console=console, refresh_per_second=8, transient=False):
        if args.phase is not None:
            segments = [{"start_frame": start_frame, "end_frame": end_frame,
                         "phase": args.phase}]
            console.print(f"Forcing global phase={args.phase} on frames "
                          f"[{start_frame}, {end_frame}).",
                          markup=False)
        else:
            segments = run_detector(
                args.input, mode=args.mode,
                start_frame=start_frame, end_frame=end_frame,
                threads=args.threads,
                progress=progress, task_id=detect_task,
            )
            # Clip detected segments to the requested trim window.
            clipped = []
            for s in segments:
                sf = max(s["start_frame"], start_frame)
                ef = min(s["end_frame"], end_frame)
                if ef > sf:
                    clipped.append({"start_frame": sf, "end_frame": ef, "phase": s["phase"]})
            segments = clipped

        console.print(f"Processing {len(segments)} segment(s):", markup=False)
        for s in segments[:30]:
            ts_start = s["start_frame"] / fps
            ts_end = s["end_frame"] / fps
            console.print(f"  [{ts_start:8.1f}s, {ts_end:8.1f}s)  phase={s['phase']}",
                          markup=False)
        if len(segments) > 30:
            console.print(f"  ... ({len(segments) - 30} more)", markup=False)

        # Estimate output frame count for progress percentage:
        # each segment yields (usable // 5) * 4 frames.
        expected_out_frames = 0
        for seg in segments:
            seg_len = seg["end_frame"] - seg["start_frame"]
            seg_phase_skip = (seg["phase"] - seg["start_frame"]) % 5
            usable = max(0, seg_len - seg_phase_skip)
            expected_out_frames += (usable // 5) * 4

        vpy_text = build_vpy(args.input, segments, args.mode)
        if args.dry_run:
            console.print("\n# Generated .vpy:\n", markup=False)
            console.print(vpy_text, markup=False)
            console.print("\n# ffmpeg args:", markup=False)
            console.print(" ".join(shlex.quote(a)
                                   for a in ffmpeg_args_for_codec(args.codec, args.output)),
                          markup=False)
            return 0

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".vpy", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(vpy_text)
            vpy_path = tf.name

        vspipe = str(VENV_VSPIPE) if VENV_VSPIPE.is_file() else "vspipe"
        try:
            ff_args = ffmpeg_args_for_codec(args.codec, args.output)
            # Source audio is always muxed in (with trim offsets applied if given).
            # The `-map 1:a:0?` form is optional, so files without audio just
            # produce a video-only output.
            audio_extra = []
            if start_sec:
                audio_extra += ["-ss", str(start_sec)]
            if duration_sec:
                audio_extra += ["-t", str(duration_sec)]
            ff_input_args = (
                ["-y", "-loglevel", "error", "-nostats",
                 "-progress", "pipe:2",
                 "-f", "yuv4mpegpipe", "-i", "-"]
                + audio_extra + ["-i", str(args.input)]
                + ["-map", "0:v:0", "-map", "1:a:0?"]
            )

            vspipe_cmd = [vspipe, "-c", "y4m", vpy_path, "-"]
            ffmpeg_cmd = ["ffmpeg", *ff_input_args, *ff_args]
            console.print(f"Rendering {expected_out_frames} frames "
                          f"({expected_out_frames / 23.976:.0f}s @ 23.976fps)...",
                          markup=False)

            v = subprocess.Popen(vspipe_cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
            try:
                f = subprocess.Popen(ffmpeg_cmd, stdin=v.stdout,
                                     stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
                v.stdout.close()
                _stream_render_progress(f, expected_out_frames,
                                        progress, render_task)
                ff_rc = f.wait()
            finally:
                v.wait()
            if v.returncode != 0:
                console.print(f"[red]vspipe exited {v.returncode}[/red]")
                return v.returncode
            if ff_rc != 0:
                console.print(f"[red]ffmpeg exited {ff_rc}[/red]")
                return ff_rc
            # Mark render fully complete in case the last progress update lagged.
            progress.update(render_task, completed=expected_out_frames)
            console.print(f"[green]Wrote {rich_escape(str(args.output))}[/green]")
            return 0
        finally:
            try:
                os.unlink(vpy_path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
