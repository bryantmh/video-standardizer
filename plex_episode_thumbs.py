"""
Set Plex episode thumbnails from a frame 10% into each episode.

Usage:
    python plex_episode_thumbs.py "Show Name" <season>
    python plex_episode_thumbs.py "Show Name" <season> --percent 15
    python plex_episode_thumbs.py "Show Name" --all

Requires a Plex token. Add `plex_token=...` to config.env next to this script.
Find yours at: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/
"""

import argparse
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_ENV = os.path.join(_SCRIPT_DIR, 'config.env')

FFMPEG = 'ffmpeg'


def _load_config():
    cfg = {}
    if os.path.exists(_CONFIG_ENV):
        with open(_CONFIG_ENV, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                cfg[k.strip()] = v.strip()
    return cfg


_CONFIG = _load_config()
PLEX_URL = _CONFIG.get('plex_url', 'http://127.0.0.1:32400')


def _req(path, token, method='GET', data=None, content_type=None):
    url = f'{PLEX_URL}{path}'
    sep = '&' if '?' in path else '?'
    url += f'{sep}X-Plex-Token={urllib.parse.quote(token)}'
    headers = {'Accept': 'application/xml'}
    if content_type:
        headers['Content-Type'] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def find_show(token, show_name):
    """Return (section_id, show_ratingKey) for a show by name."""
    body = _req('/library/sections', token)
    root = ET.fromstring(body)
    for section in root.findall('Directory'):
        if section.get('type') != 'show':
            continue
        sid = section.get('key')
        results = _req(f'/library/sections/{sid}/all', token)
        for show in ET.fromstring(results).findall('Directory'):
            if show.get('title', '').lower() == show_name.lower():
                return sid, show.get('ratingKey')
    return None, None


def list_episodes(token, show_key, season=None):
    """Return list of (ratingKey, season, episode, title, file_path, duration_ms)."""
    body = _req(f'/library/metadata/{show_key}/allLeaves', token)
    out = []
    for ep in ET.fromstring(body).findall('Video'):
        s = int(ep.get('parentIndex', 0))
        if season is not None and s != season:
            continue
        media = ep.find('Media')
        part = media.find('Part') if media is not None else None
        if part is None:
            continue
        out.append((
            ep.get('ratingKey'),
            s,
            int(ep.get('index', 0)),
            ep.get('title', ''),
            part.get('file'),
            int(media.get('duration', 0)) if media is not None else 0,
        ))
    out.sort(key=lambda x: (x[1], x[2]))
    return out


def extract_frame(video_path, duration_ms, percent, out_path):
    """Extract a frame at `percent` into the video using ffmpeg."""
    seconds = max(0, (duration_ms / 1000.0) * (percent / 100.0))
    cmd = [
        FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
        '-ss', f'{seconds:.3f}', '-i', video_path,
        '-frames:v', '1', '-q:v', '2', out_path,
    ]
    subprocess.run(cmd, check=True)


def upload_thumb(token, rating_key, image_path):
    with open(image_path, 'rb') as f:
        data = f.read()
    _req(f'/library/metadata/{rating_key}/posters', token,
         method='POST', data=data, content_type='image/jpeg')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('show')
    ap.add_argument('season', nargs='?', type=int,
                    help='Season number (omit with --all)')
    ap.add_argument('--all', action='store_true',
                    help='Process every season')
    ap.add_argument('--percent', type=float, default=10.0,
                    help='Frame position as %% of runtime (default: 10)')
    ap.add_argument('--token', default=_CONFIG.get('plex_token'),
                    help='Plex token (defaults to plex_token in config.env)')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not args.token:
        sys.exit('error: Plex token required (set plex_token in config.env or pass --token)')
    if args.season is None and not args.all:
        sys.exit('error: specify a season number or use --all')

    print(f'Looking up show: {args.show}')
    _sid, show_key = find_show(args.token, args.show)
    if not show_key:
        sys.exit(f'error: show not found: {args.show}')

    season = None if args.all else args.season
    episodes = list_episodes(args.token, show_key, season)
    if not episodes:
        sys.exit('error: no episodes matched')

    print(f'Found {len(episodes)} episode(s). Using frame at {args.percent}% of runtime.')

    with tempfile.TemporaryDirectory() as tmp:
        for rk, s, e, title, path, dur in episodes:
            label = f'S{s:02d}E{e:02d} {title}'
            if not path or not os.path.exists(path):
                print(f'  [skip] {label} — file not accessible: {path}')
                continue
            if dur <= 0:
                print(f'  [skip] {label} — no duration metadata')
                continue
            if args.dry_run:
                print(f'  [dry] {label} @ {(dur/1000)*(args.percent/100):.1f}s')
                continue
            thumb = os.path.join(tmp, f'{rk}.jpg')
            try:
                extract_frame(path, dur, args.percent, thumb)
                upload_thumb(args.token, rk, thumb)
                print(f'  [ok]   {label}')
            except subprocess.CalledProcessError as ex:
                print(f'  [fail] {label} — ffmpeg: {ex}')
            except Exception as ex:
                print(f'  [fail] {label} — {ex}')


if __name__ == '__main__':
    main()
