#!/usr/bin/env python3
"""
remove_empty_dirs.py — recursively find and move empty folders to the recycle bin.

Usage:
    python remove_empty_dirs.py <directory>
"""

import argparse
import os
import sys

from send2trash import send2trash


def remove_empty_dirs(root: str) -> int:
    removed = 0
    # Walk bottom-up so children are processed before parents
    for dirpath, dirs, files in os.walk(root, topdown=False):
        if dirpath == root:
            continue
        try:
            entries = os.listdir(dirpath)
        except PermissionError as e:
            print(f'  [SKIP] {dirpath}  ({e})')
            continue
        if not entries:
            print(f'  [REMOVE] {dirpath}')
            try:
                send2trash(dirpath)
                removed += 1
            except Exception as e:
                print(f'           -> recycle failed: {e}')
    return removed


def main():
    parser = argparse.ArgumentParser(description='Move empty folders to the recycle bin.')
    parser.add_argument('directory', help='Root directory to scan')
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f'ERROR: not a directory: {args.directory}', file=sys.stderr)
        sys.exit(1)

    print(f'Scanning {args.directory!r} for empty folders...\n')
    try:
        removed = remove_empty_dirs(args.directory)
    except KeyboardInterrupt:
        print('\nCancelled.')
        sys.exit(130)

    print(f'\n{removed} empty folder(s) recycled.' if removed else '\nNo empty folders found.')


if __name__ == '__main__':
    main()
