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
import re
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


def list_shows(token):
    """Return [(title, ratingKey)] for every show across all TV library sections."""
    body = _req('/library/sections', token)
    root = ET.fromstring(body)
    shows = []
    for section in root.findall('Directory'):
        if section.get('type') != 'show':
            continue
        sid = section.get('key')
        results = _req(f'/library/sections/{sid}/all', token)
        for show in ET.fromstring(results).findall('Directory'):
            title = show.get('title', '')
            key = show.get('ratingKey')
            if title and key:
                shows.append((title, key))
    shows.sort(key=lambda x: x[0].lower())
    return shows


def list_seasons(token, show_key):
    """Return a sorted list of distinct season numbers present for this show."""
    eps = list_episodes(token, show_key)
    return sorted({s for _rk, s, _e, _t, _p, _d in eps})


def find_show(token, show_name):
    """Return (section_id, show_ratingKey) for a show by name (case-insensitive)."""
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


_CROPDETECT_RE = re.compile(
    r'crop=(\d+):(\d+):(\d+):(\d+)')
# blackdetect logs ranges: "black_start:<s> black_end:<s> black_duration:<s>"
_BLACKDETECT_RE = re.compile(
    r'black_start:([\d.]+)\s+black_end:([\d.]+)')


def _probe_window(video_path, start_s, window_s=5.0):
    """Run cropdetect + blackdetect over a short window. Returns
    (crop_rect_or_None, black_ranges). crop_rect is (w, h, x, y) or None
    if ffmpeg didn't emit one. black_ranges is a list of (start, end).
    """
    cmd = [
        FFMPEG, '-hide_banner', '-nostats',
        '-ss', f'{start_s:.3f}', '-t', f'{window_s:.3f}',
        '-i', video_path,
        '-vf', 'cropdetect=limit=24:round=2:reset=1,blackdetect=d=0.1:pic_th=0.98',
        '-an', '-f', 'null', '-',
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None, []
    stderr = res.stderr or ''
    # Use the last cropdetect emission in the window — it's the most
    # stable estimate after the filter's initial frames.
    crops = _CROPDETECT_RE.findall(stderr)
    crop = None
    if crops:
        w, h, x, y = (int(v) for v in crops[-1])
        crop = (w, h, x, y)
    blacks = [(float(a), float(b))
              for a, b in _BLACKDETECT_RE.findall(stderr)]
    return crop, blacks


def _is_in_black_range(absolute_s, black_ranges, window_start):
    """Check whether absolute_s falls inside any black range. The black
    range times are relative to the probe window start.
    """
    for a, b in black_ranges:
        if window_start + a <= absolute_s <= window_start + b:
            return True
    return False


def extract_frame(video_path, duration_ms, percent, out_path, *,
                  max_attempts=5, step_seconds=5.0):
    """Extract a frame at `percent` into the video, stepping forward if the
    chosen frame lands in a black region and auto-cropping any detected
    letterbox bars. Raises subprocess.CalledProcessError on extraction
    failure.
    """
    total_seconds = max(0.0, duration_ms / 1000.0)
    base_s = max(0.0, total_seconds * (percent / 100.0))

    # Cap the forward walk so we never wander past the episode's end.
    last_acceptable = max(base_s, total_seconds - step_seconds)
    chosen_s = base_s
    crop_rect = None

    for attempt in range(max_attempts):
        window_start = min(chosen_s, last_acceptable)
        crop_rect, black_ranges = _probe_window(video_path, window_start)
        if not _is_in_black_range(chosen_s, black_ranges, window_start):
            break
        # Step forward past the end of the black range we hit (if known),
        # else by the fixed step.
        next_s = chosen_s + step_seconds
        for a, b in black_ranges:
            if window_start + a <= chosen_s <= window_start + b:
                next_s = max(next_s, window_start + b + 0.5)
                break
        if next_s >= total_seconds:
            # Ran out of runway — extract whatever we had.
            break
        chosen_s = next_s

    vf_parts = []
    if crop_rect is not None:
        w, h, x, y = crop_rect
        # Skip obviously-bad cropdetect emissions (e.g. 0×0 from a black
        # frame). The final extract handles any remaining weirdness via
        # ffmpeg's own validation — we just avoid passing junk.
        if w >= 16 and h >= 16:
            vf_parts.append(f'crop={w}:{h}:{x}:{y}')

    cmd = [
        FFMPEG, '-hide_banner', '-loglevel', 'error', '-y',
        '-ss', f'{chosen_s:.3f}', '-i', video_path,
        '-frames:v', '1', '-q:v', '2',
    ]
    if vf_parts:
        cmd += ['-vf', ','.join(vf_parts)]
    cmd.append(out_path)
    subprocess.run(cmd, check=True)


def upload_thumb(token, rating_key, image_path):
    with open(image_path, 'rb') as f:
        data = f.read()
    _req(f'/library/metadata/{rating_key}/posters', token,
         method='POST', data=data, content_type='image/jpeg')


def apply_thumbnails(token, show_key, season, percent, *,
                     dry_run=False, log_fn=print, stop_flag=None):
    """Set frame-at-N% thumbnails on every matching episode.

    Returns (ok, fail, skipped). log_fn gets one line per episode. Pass a
    callable returning True via stop_flag to cancel mid-batch.
    """
    episodes = list_episodes(token, show_key, season)
    if not episodes:
        log_fn('error: no episodes matched')
        return 0, 0, 0
    log_fn(f'Found {len(episodes)} episode(s). Using frame at {percent}% of runtime.')

    ok = fail = skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        for rk, s, e, title, path, dur in episodes:
            if stop_flag and stop_flag():
                log_fn('  (stopped)')
                break
            label = f'S{s:02d}E{e:02d} {title}'
            if not path or not os.path.exists(path):
                log_fn(f'  [skip] {label} — file not accessible: {path}')
                skipped += 1
                continue
            if dur <= 0:
                log_fn(f'  [skip] {label} — no duration metadata')
                skipped += 1
                continue
            if dry_run:
                log_fn(f'  [dry] {label} @ {(dur/1000)*(percent/100):.1f}s')
                skipped += 1
                continue
            thumb = os.path.join(tmp, f'{rk}.jpg')
            try:
                extract_frame(path, dur, percent, thumb)
                upload_thumb(token, rk, thumb)
                log_fn(f'  [ok]   {label}')
                ok += 1
            except subprocess.CalledProcessError as ex:
                log_fn(f'  [fail] {label} — ffmpeg: {ex}')
                fail += 1
            except Exception as ex:
                log_fn(f'  [fail] {label} — {ex}')
                fail += 1
    return ok, fail, skipped


def build_plex_popup(parent):
    """Open the Plex Thumbnails popup. Standalone — no callback plumbing into
    the main app because thumbnail setting doesn't touch filenames.
    """
    import tkinter as tk
    from tkinter import ttk
    import threading

    _BG = '#1e1e1e'
    _BG2 = '#252526'
    _FG = '#cccccc'
    _FG_DIM = '#888888'
    _SEL = '#0078d4'
    _ENT = '#3c3c3c'

    popup = tk.Toplevel(parent)
    popup.title("Plex Episode Thumbnails")
    popup.geometry("720x520")
    popup.minsize(560, 380)
    popup.configure(bg=_BG)
    popup.transient(parent)

    try:
        import ctypes
        popup.update()
        hwnd = ctypes.windll.user32.GetAncestor(popup.winfo_id(), 2) or popup.winfo_id()
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass

    # Dark theme for ttk widgets inside this popup (Combobox, Entry, Button,
    # Scrollbar). Styles set on the default style registry apply globally, but
    # re-setting them is idempotent — safe when the main window already did it.
    style = ttk.Style(popup)
    try:
        style.theme_use('clam')
    except Exception:
        pass
    style.configure('TCombobox',
        fieldbackground=_ENT, background=_ENT, foreground=_FG,
        selectbackground=_SEL, selectforeground='#ffffff',
        arrowcolor=_FG, bordercolor='#555555')
    style.map('TCombobox',
        fieldbackground=[('readonly', _ENT), ('disabled', _BG2)],
        background=[('readonly', _ENT)],
        foreground=[('readonly', _FG), ('disabled', _FG_DIM)])
    style.configure('TEntry',
        fieldbackground=_ENT, foreground=_FG, insertcolor=_FG,
        bordercolor='#555555', selectbackground=_SEL,
        selectforeground='#ffffff')
    style.configure('TButton',
        background=_ENT, foreground=_FG, bordercolor='#555555',
        relief='flat', padding=4)
    style.map('TButton',
        background=[('active', '#4c4c4c'), ('pressed', _SEL),
                    ('disabled', '#3a3a3a')],
        foreground=[('disabled', '#777777')])
    style.configure('Vertical.TScrollbar',
        troughcolor=_BG2, background=_ENT,
        bordercolor='#555555', arrowcolor=_FG,
        lightcolor=_ENT, darkcolor=_ENT)
    style.map('Vertical.TScrollbar',
        background=[('active', '#4c4c4c'), ('pressed', _SEL)])

    # The dropdown list that appears when a Combobox is opened is a classic
    # tk Listbox, not ttk — so its colours come from Tk's option database,
    # which we set here so the dropdown matches the rest of the popup.
    popup.option_add('*TCombobox*Listbox.background', _BG2)
    popup.option_add('*TCombobox*Listbox.foreground', _FG)
    popup.option_add('*TCombobox*Listbox.selectBackground', _SEL)
    popup.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    popup.option_add('*TCombobox*Listbox.borderWidth', 0)

    token_val = _CONFIG.get('plex_token', '')

    state = {
        'shows': [],           # list of (title, key)
        'show_key': None,
        'seasons': [],         # list of int
        'stop': False,
        'running': False,
    }

    # ── Top controls ────────────────────────────────────────────
    top = tk.Frame(popup, bg=_BG)
    top.pack(fill=tk.X, padx=10, pady=(10, 4))

    tk.Label(top, text="Show:", bg=_BG, fg=_FG,
             font=("Consolas", 10, "bold")).pack(side=tk.LEFT, padx=(0, 6))
    show_combo = ttk.Combobox(top, state='readonly', width=36, values=[])
    show_combo.pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(top, text="Season:", bg=_BG, fg=_FG,
             font=("Consolas", 10, "bold")).pack(side=tk.LEFT, padx=(0, 6))
    season_combo = ttk.Combobox(top, state='readonly', width=14, values=[])
    season_combo.pack(side=tk.LEFT, padx=(0, 10))

    tk.Label(top, text="%:", bg=_BG, fg=_FG).pack(side=tk.LEFT, padx=(0, 4))
    pct_var = tk.StringVar(value="10")
    ttk.Entry(top, textvariable=pct_var, width=6).pack(side=tk.LEFT, padx=(0, 10))

    dry_var = tk.BooleanVar(value=False)
    tk.Checkbutton(top, text="Dry run", variable=dry_var,
                   bg=_BG, fg=_FG, selectcolor=_ENT,
                   activebackground=_BG, activeforeground=_FG).pack(
        side=tk.LEFT)

    # ── Log / status ────────────────────────────────────────────
    log_frame = tk.Frame(popup, bg=_BG)
    log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(2, 4))

    text = tk.Text(log_frame, bg=_BG2, fg=_FG, font=("Consolas", 9),
                   state=tk.DISABLED, wrap=tk.WORD, relief=tk.FLAT,
                   insertbackground=_FG)
    scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL,
                              command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def log_line(msg):
        def _do():
            text.configure(state=tk.NORMAL)
            text.insert(tk.END, str(msg) + '\n')
            text.see(tk.END)
            text.configure(state=tk.DISABLED)
        popup.after(0, _do)

    # ── Bottom bar ──────────────────────────────────────────────
    bot = tk.Frame(popup, bg=_BG)
    bot.pack(fill=tk.X, padx=10, pady=(4, 10))

    status_var = tk.StringVar(value="Loading Plex library…")
    tk.Label(bot, textvariable=status_var, bg=_BG, fg=_FG_DIM,
             font=("Consolas", 9)).pack(side=tk.LEFT)

    run_btn = ttk.Button(bot, text="Apply")
    run_btn.pack(side=tk.RIGHT)
    stop_btn = ttk.Button(bot, text="Stop", state=tk.DISABLED)
    stop_btn.pack(side=tk.RIGHT, padx=(0, 6))
    close_btn = ttk.Button(bot, text="Close", command=popup.destroy)
    close_btn.pack(side=tk.RIGHT, padx=(0, 6))

    # ── Plumbing ────────────────────────────────────────────────

    def _on_show_selected(_e=None):
        sel_idx = show_combo.current()
        if sel_idx < 0:
            return
        _title, key = state['shows'][sel_idx]
        state['show_key'] = key
        season_combo.configure(state='disabled')
        season_combo.set('Loading…')
        status_var.set(f"Fetching seasons for: {_title}")

        def worker():
            try:
                seasons = list_seasons(token_val, key)
            except Exception as e:
                popup.after(0, lambda: status_var.set(f"Error: {e}"))
                return
            def finish():
                state['seasons'] = seasons
                labels = ['All'] + [f'Season {s}' for s in seasons]
                season_combo['values'] = labels
                season_combo.configure(state='readonly')
                season_combo.current(0)
                status_var.set(f"{_title}: {len(seasons)} season(s)")
            popup.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    show_combo.bind('<<ComboboxSelected>>', _on_show_selected)

    def _load_shows():
        if not token_val:
            status_var.set("No Plex token — set plex_token in config.env.")
            log_line("error: plex_token not found in config.env")
            run_btn.configure(state=tk.DISABLED)
            return

        def worker():
            try:
                shows = list_shows(token_val)
            except Exception as e:
                popup.after(0, lambda: status_var.set(f"Error loading library: {e}"))
                popup.after(0, lambda: log_line(f"error: {e}"))
                return
            def finish():
                state['shows'] = shows
                show_combo['values'] = [t for t, _k in shows]
                if shows:
                    show_combo.current(0)
                    _on_show_selected()
                status_var.set(f"{len(shows)} show(s) loaded.")
            popup.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _run():
        if state['running']:
            return
        if not state.get('show_key'):
            log_line("error: pick a show first")
            return
        try:
            pct = float(pct_var.get())
        except ValueError:
            log_line("error: percent must be a number")
            return
        if season_combo.current() <= 0:
            season = None
        else:
            season = state['seasons'][season_combo.current() - 1]
        dry = dry_var.get()
        state['running'] = True
        state['stop'] = False
        run_btn.configure(state=tk.DISABLED)
        stop_btn.configure(state=tk.NORMAL)
        status_var.set("Running…")

        def worker():
            try:
                ok, fail, skipped = apply_thumbnails(
                    token_val, state['show_key'], season, pct,
                    dry_run=dry, log_fn=log_line,
                    stop_flag=lambda: state['stop'],
                )
                log_line(f"\nDone — {ok} ok · {fail} failed · {skipped} skipped")
            except Exception as e:
                log_line(f"error: {e}")
            finally:
                def finish():
                    state['running'] = False
                    run_btn.configure(state=tk.NORMAL)
                    stop_btn.configure(state=tk.DISABLED)
                    status_var.set("Idle.")
                popup.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    run_btn.configure(command=_run)
    stop_btn.configure(command=lambda: state.update(stop=True))

    _load_shows()
    return popup


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
    apply_thumbnails(
        args.token, show_key, season, args.percent,
        dry_run=args.dry_run, log_fn=print,
    )


if __name__ == '__main__':
    main()
