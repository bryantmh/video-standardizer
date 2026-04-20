#!/usr/bin/env python3
"""
check_resolution.py — scan a directory tree for video files below a minimum vertical resolution.

Usage:
    python check_resolution.py <directory> [--min-height N] [--workers N] [--recycle]
"""

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from send2trash import send2trash

VIDEO_EXTS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.flv', '.webm', '.vob', '.divx', '.xvid', '.rmvb',
}


def find_videos(root: str) -> list[str]:
    paths = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                paths.append(os.path.join(dirpath, f))
    return paths


def check_file(path: str, min_height: int) -> tuple[str, bool, str]:
    """Returns (path, is_low_res, detail_msg)."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'default=noprint_wrappers=1',
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            err = result.stderr.strip() or f'exit code {result.returncode}'
            return path, False, f'[probe failed] {err}'

        info = {}
        for line in result.stdout.splitlines():
            if '=' in line:
                k, _, v = line.partition('=')
                info[k.strip()] = v.strip()

        width_str = info.get('width', '')
        height_str = info.get('height', '')

        if not height_str or not height_str.isdigit():
            return path, False, '[no height info]'

        height = int(height_str)
        width = int(width_str) if width_str.isdigit() else 0

        if height < min_height:
            return path, True, f'{width}x{height}'
        return path, False, ''
    except subprocess.TimeoutExpired:
        return path, False, '[timed out after 30s]'
    except FileNotFoundError:
        print('ERROR: ffprobe not found. Install ffmpeg and ensure it is on PATH.', file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Find video files below a minimum vertical resolution.')
    parser.add_argument('directory', help='Root directory to scan')
    parser.add_argument('--min-height', type=int, default=360,
                        help='Minimum vertical resolution in pixels (default: 360)')
    parser.add_argument('--workers', type=int, default=min(8, (os.cpu_count() or 4)),
                        help='Parallel workers (default: min(8, cpu_count))')
    parser.add_argument('--recycle', action='store_true',
                        help='Move low-resolution files to the recycle bin')
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f'ERROR: not a directory: {args.directory}', file=sys.stderr)
        sys.exit(1)

    print(f'Scanning {args.directory!r} ...')
    videos = find_videos(args.directory)
    total = len(videos)
    if total == 0:
        print('No video files found.')
        return

    print(f'Found {total} video file(s). Checking with {args.workers} worker(s) (min height: {args.min_height}px)...\n')

    low_res_count = 0
    checked = 0

    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {pool.submit(check_file, p, args.min_height): p for p in videos}
    try:
        for fut in as_completed(futures):
            path, is_low_res, msg = fut.result()
            checked += 1
            if is_low_res:
                low_res_count += 1
                print(f'\r  [LOW] {path}  ({msg})' + ' ' * 10)
                if args.recycle:
                    try:
                        send2trash(path)
                        print(f'        -> moved to recycle bin')
                    except Exception as e:
                        print(f'        -> recycle failed: {e}')
            print(f'\r  {checked}/{total}', end='', flush=True)
    except KeyboardInterrupt:
        print(f'\r  Cancelled after {checked}/{total} file(s).' + ' ' * 10)
        pool.shutdown(wait=False, cancel_futures=True)
        if low_res_count:
            action = 'recycled' if args.recycle else 'found'
            print(f'{low_res_count} low-resolution file(s) {action} before cancellation.')
        os._exit(130)

    pool.shutdown(wait=False)
    print()  # newline after progress

    if low_res_count:
        action = 'recycled' if args.recycle else 'found'
        print(f'\n{low_res_count} low-resolution file(s) {action} (below {args.min_height}px height).')
    else:
        print(f'\nAll files are at or above {args.min_height}px height.')


if __name__ == '__main__':
    main()
