#!/usr/bin/env python3
"""
check_corrupt.py — scan a directory tree for corrupted video files using ffprobe.

Usage:
    python check_corrupt.py <directory> [--workers N]
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


IGNORED_ERRORS: dict[str, list[str]] = {
    '.mpg': ['Invalid frame dimensions 0x0'],
    '.mpeg': ['Invalid frame dimensions 0x0'],
}


def check_file(path: str) -> tuple[str, bool, str]:
    """Returns (path, is_corrupt, error_msg)."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_type',
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
        ext = os.path.splitext(path)[1].lower()
        ignored = IGNORED_ERRORS.get(ext, [])
        stderr_lines = [
            line for line in result.stderr.splitlines()
            if not any(pat in line for pat in ignored)
        ]
        stderr = '\n'.join(stderr_lines).strip()
        if result.returncode != 0 or stderr:
            return path, True, stderr or f'exit code {result.returncode}'
        return path, False, ''
    except subprocess.TimeoutExpired:
        return path, True, 'timed out after 30s'
    except FileNotFoundError:
        print('ERROR: ffprobe not found. Install ffmpeg and ensure it is on PATH.', file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Find corrupted video files.')
    parser.add_argument('directory', help='Root directory to scan')
    parser.add_argument('--workers', type=int, default=min(8, (os.cpu_count() or 4)),
                        help='Parallel workers (default: min(8, cpu_count))')
    parser.add_argument('--recycle', action='store_true',
                        help='Move corrupted files to the recycle bin')
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

    print(f'Found {total} video file(s). Checking with {args.workers} worker(s)...\n')

    corrupt_count = 0
    checked = 0

    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {pool.submit(check_file, p): p for p in videos}
    try:
        for fut in as_completed(futures):
            path, is_corrupt, msg = fut.result()
            checked += 1
            if is_corrupt:
                corrupt_count += 1
                # Clear progress line, print the bad file, then resume progress
                print(f'\r  [BAD] {path}' + ' ' * 10)
                if msg:
                    print(f'        {msg}')
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
        if corrupt_count:
            action = 'recycled' if args.recycle else 'found'
            print(f'{corrupt_count} corrupted file(s) {action} before cancellation.')
        os._exit(130)  # force-kill worker threads immediately

    pool.shutdown(wait=False)
    print()  # newline after progress

    if corrupt_count:
        action = 'recycled' if args.recycle else 'found'
        print(f'\n{corrupt_count} corrupted file(s) {action}.')
    else:
        print('\nAll files appear OK.')


if __name__ == '__main__':
    main()
