#!/usr/bin/env python3
"""
check_corrupt.py — scan a directory tree for videos whose decoded stream is
badly damaged (lots of ac-tex errors, invalid MB types, corrupt frames, etc).

Strategy: decode N short sample windows (default 15 x 20s, evenly spaced
through the file), without -xerror so each window runs to completion.  Count
corruption-matching stderr lines across all samples.  Flag the file only if
the total count meets --threshold.  Isolated glitches, which most decoders
(and VideoReDo) conceal silently, are below threshold and ignored.

Pass "-" as the directory to read a newline-separated list of file paths
from stdin instead of walking a directory, e.g.:

    python scripts/find_by_ext.py "D:\\Movies" --ext .mkv | python scripts/check_corrupt.py -

Usage:
    python check_corrupt.py <directory | -> [--workers N] [--samples N]
                                            [--sample-seconds N] [--threshold N]
                                            [--full] [--recycle] [--verbose]
"""

import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from send2trash import send2trash

VIDEO_EXTS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.flv', '.webm', '.vob', '.divx', '.xvid', '.rmvb',
}

# ffmpeg stderr lines that indicate stream damage.  Each match contributes 1
# to the file's error count; the file is flagged only if the total >=
# --threshold.  Tune by running with --verbose to see counts per file.
CORRUPTION_PATTERNS = (
    re.compile(r'ac-tex damaged'),
    re.compile(r'Invalid mb type'),
    re.compile(r'corrupt decoded frame'),
    re.compile(r'Invalid data found when processing input'),
    re.compile(r'error while decoding MB', re.IGNORECASE),
    re.compile(r'concealing \d+ DC, \d+ AC'),
    re.compile(r'damaged at \d+ \d+'),
    re.compile(r'non-existing [A-Z]+ referenced'),
    re.compile(r'slice header damaged'),
    re.compile(r'missing picture in access unit'),
    re.compile(r'No start code is found'),
    re.compile(r'end mismatch'),
    re.compile(r'Invalid NAL unit size'),
)

# Benign / noisy lines we explicitly ignore.  Seen on many healthy captures.
BENIGN_PATTERNS = (
    re.compile(r'Invalid frame dimensions 0x0'),
    re.compile(r'Last message repeated'),
    re.compile(r'Warning MVs not available'),
    re.compile(r'00 motion_type'),
    re.compile(r'left block unavailable'),
    re.compile(r'top block unavailable'),
)


def find_videos(root: str) -> list[str]:
    paths = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                paths.append(os.path.join(dirpath, f))
    return paths


def _read_paths_from_stdin() -> list[str]:
    """Read newline-separated paths from stdin, skipping blanks and non-videos."""
    paths: list[str] = []
    for raw in sys.stdin:
        p = raw.strip().strip('"').strip("'")
        if not p:
            continue
        if os.path.splitext(p)[1].lower() not in VIDEO_EXTS:
            print(f'  skip (not a video): {p}', file=sys.stderr)
            continue
        if not os.path.isfile(p):
            print(f'  skip (not found): {p}', file=sys.stderr)
            continue
        paths.append(os.path.abspath(p))
    seen = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _probe_duration(path: str) -> float:
    """Return container duration in seconds, or 0.0 if unavailable."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return float(result.stdout.strip() or 0.0)
    except (subprocess.TimeoutExpired, ValueError):
        return 0.0


def _count_errors(stderr: str) -> tuple[int, str]:
    """Count corruption-matching stderr lines.  Returns (count, sample_line)."""
    count = 0
    first_match = ''
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(p.search(line) for p in CORRUPTION_PATTERNS):
            count += 1
            if not first_match:
                first_match = line
    return count, first_match


def _decode_range(path: str, start: float | None, duration: float | None,
                  timeout: float) -> tuple[int, str, bool]:
    """
    Decode video-stream-only for the requested range, without -xerror so the
    whole window runs and every error is logged.

    Returns (error_count, sample_error_line, timed_out).
    """
    cmd = ['ffmpeg', '-v', 'error']
    if start is not None and start > 0:
        cmd += ['-ss', f'{start:.3f}']
    cmd += ['-i', path, '-map', '0:v:0']
    if duration is not None:
        cmd += ['-t', f'{duration:.3f}']
    cmd += ['-f', 'null', '-']

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 0, f'timed out after {timeout:.0f}s', True
    except FileNotFoundError:
        print('ERROR: ffmpeg not found. Install ffmpeg and ensure it is on PATH.',
              file=sys.stderr)
        sys.exit(1)

    count, sample = _count_errors(result.stderr)
    return count, sample, False


def check_file(path: str, *, samples: int, sample_seconds: float,
               threshold: int, full: bool,
               timeout: float) -> tuple[str, bool, int, str]:
    """Returns (path, is_corrupt, error_count, sample_error_line)."""
    if full:
        count, sample, timed_out = _decode_range(path, None, None, timeout)
        if timed_out:
            return path, True, count, sample
        return path, count >= threshold, count, sample

    duration = _probe_duration(path)

    # Files shorter than the total sampled window: decode the whole thing
    # instead of sampling.
    if duration <= samples * sample_seconds:
        count, sample, timed_out = _decode_range(path, None, None, timeout)
        if timed_out:
            return path, True, count, sample
        return path, count >= threshold, count, sample

    if samples == 1:
        offsets = [0.0]
    else:
        last_start = max(0.0, duration - sample_seconds)
        step = last_start / (samples - 1)
        offsets = [i * step for i in range(samples)]

    total_errors = 0
    first_sample = ''
    for off in offsets:
        count, sample, timed_out = _decode_range(path, off, sample_seconds, timeout)
        if timed_out:
            return path, True, total_errors, sample
        total_errors += count
        if sample and not first_sample:
            first_sample = sample
        # Short-circuit: once we exceed the threshold there's no reason to
        # keep decoding this file.
        if total_errors >= threshold:
            break

    return path, total_errors >= threshold, total_errors, first_sample


def main():
    parser = argparse.ArgumentParser(
        description='Find corrupted video files.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('directory',
                        help='Root directory to scan, or "-" to read '
                             'newline-separated file paths from stdin')
    parser.add_argument('--workers', type=int, default=min(8, (os.cpu_count() or 4)),
                        help='Parallel workers (default: min(8, cpu_count))')
    parser.add_argument('--samples', type=int, default=15,
                        help='Number of decode samples per file (default: 15)')
    parser.add_argument('--sample-seconds', type=float, default=20.0,
                        help='Seconds of video to decode per sample (default: 20)')
    parser.add_argument('--threshold', type=int, default=50,
                        help='Minimum error-line count to flag a file (default: 50). '
                             'Lower = stricter, higher = only catches heavily damaged files.')
    parser.add_argument('--full', action='store_true',
                        help='Decode the whole file instead of sampling (slow, thorough)')
    parser.add_argument('--timeout', type=float, default=120.0,
                        help='Per-decode-call timeout in seconds (default: 120)')
    parser.add_argument('--recycle', action='store_true',
                        help='Move corrupted files to the recycle bin')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Print error counts for every file (useful for tuning --threshold)')
    args = parser.parse_args()

    if args.directory == '-':
        if sys.stdin.isatty():
            print('ERROR: "-" was passed but stdin is a terminal. '
                  'Pipe a list of paths in, e.g.:\n'
                  '  python scripts/find_by_ext.py DIR --ext .mkv | '
                  'python scripts/check_corrupt.py -', file=sys.stderr)
            sys.exit(1)
        videos = _read_paths_from_stdin()
        source_desc = '<stdin>'
    else:
        if not os.path.isdir(args.directory):
            print(f'ERROR: not a directory: {args.directory}', file=sys.stderr)
            sys.exit(1)
        videos = find_videos(args.directory)
        source_desc = args.directory

    print(f'Scanning {source_desc!r} ...')
    total = len(videos)
    if total == 0:
        print('No video files found.')
        return

    mode = 'full decode' if args.full else (
        f'{args.samples}x{args.sample_seconds:.0f}s samples, threshold={args.threshold}'
    )
    print(f'Found {total} video file(s). Checking with {args.workers} worker(s) ({mode})...\n')

    corrupt_count = 0
    checked = 0

    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {
        pool.submit(
            check_file, p,
            samples=args.samples,
            sample_seconds=args.sample_seconds,
            threshold=args.threshold,
            full=args.full,
            timeout=args.timeout,
        ): p for p in videos
    }
    try:
        for fut in as_completed(futures):
            path, is_corrupt, count, sample = fut.result()
            checked += 1
            if is_corrupt:
                corrupt_count += 1
                print(f'\r  [BAD] {path}  (errors={count})' + ' ' * 10)
                if sample:
                    print(f'        {sample}')
                if args.recycle:
                    try:
                        send2trash(path)
                        print(f'        -> moved to recycle bin')
                    except Exception as e:
                        print(f'        -> recycle failed: {e}')
            elif args.verbose and count > 0:
                print(f'\r  [ok]  {path}  (errors={count})' + ' ' * 10)
            print(f'\r  {checked}/{total}', end='', flush=True)
    except KeyboardInterrupt:
        print(f'\r  Cancelled after {checked}/{total} file(s).' + ' ' * 10)
        pool.shutdown(wait=False, cancel_futures=True)
        if corrupt_count:
            action = 'recycled' if args.recycle else 'found'
            print(f'{corrupt_count} corrupted file(s) {action} before cancellation.')
        os._exit(130)

    pool.shutdown(wait=False)
    print()

    if corrupt_count:
        action = 'recycled' if args.recycle else 'found'
        print(f'\n{corrupt_count} corrupted file(s) {action}.')
    else:
        print('\nAll files appear OK.')


if __name__ == '__main__':
    main()
