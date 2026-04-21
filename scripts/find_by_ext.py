#!/usr/bin/env python3
"""
find_by_ext.py — recursively find video files with a specific extension.

File paths are written to stdout (one per line); progress/summary messages
go to stderr, so the output can be piped into other tools without having to
strip preamble lines. Example:

    python scripts/find_by_ext.py "D:\\DVR" --ext .ts | python batch_comskip.py -

Usage:
    python find_by_ext.py <directory> [--ext .mpg]
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description='Find video files by extension.')
    parser.add_argument('directory', help='Root directory to scan')
    parser.add_argument('--ext', default='.mpg',
                        help='File extension to search for (default: .mpg)')
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f'ERROR: not a directory: {args.directory}', file=sys.stderr)
        sys.exit(1)

    ext = args.ext if args.ext.startswith('.') else f'.{args.ext}'
    ext = ext.lower()

    print(f'Scanning {args.directory!r} for *{ext} files...', file=sys.stderr)

    found = []
    for dirpath, _dirs, files in os.walk(args.directory):
        for f in files:
            if os.path.splitext(f)[1].lower() == ext:
                found.append(os.path.join(dirpath, f))

    if not found:
        print(f'No {ext} files found.', file=sys.stderr)
        return

    for path in sorted(found):
        print(path)

    print(f'{len(found)} file(s) found.', file=sys.stderr)


if __name__ == '__main__':
    main()
