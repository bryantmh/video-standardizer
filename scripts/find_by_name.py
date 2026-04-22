#!/usr/bin/env python3
"""
find_by_name.py — recursively find video files whose filename contains a substring.

File paths are written to stdout (one per line); progress/summary messages
go to stderr, so the output can be piped into other tools without stripping
preamble lines. Example:

    python scripts/find_by_name.py "D:\\DVR" | python batch_comskip.py -

Usage:
    python find_by_name.py <directory> [--search SUBSTRING]
"""

import argparse
import os
import sys

VIDEO_EXTS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.flv', '.webm', '.vob', '.divx', '.xvid', '.rmvb',
}


def main():
    parser = argparse.ArgumentParser(
        description='Find video files whose filename contains a substring.')
    parser.add_argument('directory', help='Root directory to scan')
    parser.add_argument('--search', default='MPEG2VIDEO',
                        help='Substring to search for in filenames (default: MPEG2VIDEO)')
    parser.add_argument('--all-files', action='store_true',
                        help='Search all file types, not just known video extensions')
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f'ERROR: not a directory: {args.directory}', file=sys.stderr)
        sys.exit(1)

    needle = args.search.lower()
    print(f'Scanning {args.directory!r} for files containing {args.search!r}...', file=sys.stderr)

    found = []
    for dirpath, _dirs, files in os.walk(args.directory):
        for f in files:
            if not args.all_files and os.path.splitext(f)[1].lower() not in VIDEO_EXTS:
                continue
            if needle in f.lower():
                found.append(os.path.join(dirpath, f))

    if not found:
        print(f'No files found matching {args.search!r}.', file=sys.stderr)
        return

    for path in sorted(found):
        print(path)

    print(f'{len(found)} file(s) found.', file=sys.stderr)


if __name__ == '__main__':
    main()
