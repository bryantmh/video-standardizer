#!/usr/bin/env python3
"""
check_metadata.py — scan a directory tree for video files whose metadata contains a given key=value pair.

Usage:
    python check_metadata.py <directory> [--key KEY] [--value VALUE] [--workers N] [--recycle]
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


def check_file(path: str, key: str | None, value: str) -> tuple[str, bool, str]:
    """Returns (path, matches, detail_msg).

    If key is given, match files where that key's value contains `value` (case-insensitive substring).
    If key is None, match files where any key OR any value contains `value` (case-insensitive substring).
    """
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format_tags',
        '-of', 'default=noprint_wrappers=1',
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip() or f'exit code {result.returncode}'
            return path, False, f'[probe failed] {err}'

        stdout = result.stdout.decode('utf-8', errors='replace') if result.stdout else ''

        tags = {}
        for line in stdout.splitlines():
            if '=' not in line:
                continue
            k, _, v = line.partition('=')
            k = k.strip()
            if k.startswith('TAG:'):
                k = k[4:]
            tags[k] = v.strip()

        needle = value.lower()

        if key is None:
            for k, v in tags.items():
                if needle in k.lower() or needle in v.lower():
                    return path, True, f'{k}={v}'
            return path, False, ''

        key_ci = key.lower()
        for k, v in tags.items():
            if k.lower() == key_ci:
                if needle in v.lower():
                    return path, True, f'{k}={v}'
                return path, False, ''
        return path, False, ''
    except subprocess.TimeoutExpired:
        return path, False, '[timed out after 30s]'
    except FileNotFoundError:
        print('ERROR: ffprobe not found. Install ffmpeg and ensure it is on PATH.', file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Find video files whose metadata contains a given key=value pair.')
    parser.add_argument('directory', help='Root directory to scan')
    parser.add_argument('--key', default=None,
                        help='Metadata key to check. If omitted, fuzzy-match value against all keys and values.')
    parser.add_argument('--value', default='Hulu',
                        help='Substring to match (case-insensitive). Default: Hulu')
    parser.add_argument('--workers', type=int, default=min(8, (os.cpu_count() or 4)),
                        help='Parallel workers (default: min(8, cpu_count))')
    parser.add_argument('--recycle', action='store_true',
                        help='Move matching files to the recycle bin')
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

    match_desc = f'{args.key} contains {args.value!r}' if args.key else f'any key/value contains {args.value!r}'
    print(f'Found {total} video file(s). Checking with {args.workers} worker(s) '
          f'(match: {match_desc})...\n')

    match_count = 0
    checked = 0

    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {pool.submit(check_file, p, args.key, args.value): p for p in videos}
    try:
        for fut in as_completed(futures):
            path, matches, msg = fut.result()
            checked += 1
            if matches:
                match_count += 1
                print(f'\r  [MATCH] {path}  ({msg})' + ' ' * 10)
                if args.recycle:
                    try:
                        send2trash(path)
                        print(f'          -> moved to recycle bin')
                    except Exception as e:
                        print(f'          -> recycle failed: {e}')
            elif msg.startswith('[probe failed]') or msg.startswith('[timed out'):
                print(f'\r  {msg} {path}' + ' ' * 10)
            print(f'\r  {checked}/{total}', end='', flush=True)
    except KeyboardInterrupt:
        print(f'\r  Cancelled after {checked}/{total} file(s).' + ' ' * 10)
        pool.shutdown(wait=False, cancel_futures=True)
        if match_count:
            action = 'recycled' if args.recycle else 'found'
            print(f'{match_count} matching file(s) {action} before cancellation.')
        os._exit(130)

    pool.shutdown(wait=False)
    print()  # newline after progress

    if match_count:
        action = 'recycled' if args.recycle else 'found'
        print(f'\n{match_count} file(s) {action} matching {match_desc}.')
    else:
        print(f'\nNo files matched {match_desc}.')


if __name__ == '__main__':
    main()
