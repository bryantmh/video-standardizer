#!/usr/bin/env python3
"""
find_malformed.py — recursively find video files whose name is missing an SxxExx
tag, or files that have no extension at all.

File paths are written to stdout (one per line); progress/summary messages
go to stderr, so the output can be piped into other tools. Example:

    python scripts/find_malformed.py "D:\\DVR" | python batch_comskip.py -

Usage:
    python find_malformed.py <directory> [--all-files]
"""

import argparse
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

VIDEO_EXTS = {
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts',
    '.mpg', '.mpeg', '.flv', '.webm', '.vob', '.divx', '.xvid', '.rmvb',
}

SXXEXX_RE = re.compile(r's\d{1,3}e\d{1,3}', re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser(
        description='Find video files without SxxExx in the name, or files with no extension.')
    parser.add_argument('directory', help='Root directory to scan')
    parser.add_argument('--all-files', action='store_true',
                        help='Consider all file types, not just known video extensions '
                             '(files without an extension are always reported)')
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f'ERROR: not a directory: {args.directory}', file=sys.stderr)
        sys.exit(1)

    print(f'Scanning {args.directory!r} for malformed names...', file=sys.stderr)

    found = []
    for dirpath, _dirs, files in os.walk(args.directory):
        for f in files:
            stem, ext = os.path.splitext(f)
            ext_lower = ext.lower()

            if not ext:
                found.append(os.path.join(dirpath, f))
                continue

            if not args.all_files and ext_lower not in VIDEO_EXTS:
                continue

            if not SXXEXX_RE.search(stem):
                found.append(os.path.join(dirpath, f))

    if not found:
        print('No malformed files found.', file=sys.stderr)
        return

    for path in sorted(found):
        print(path)

    print(f'{len(found)} file(s) found.', file=sys.stderr)


if __name__ == '__main__':
    main()
