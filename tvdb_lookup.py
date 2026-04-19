"""
TVDB Lookup Module for Video Standardizer.

Provides three features:
  1. Year lookup — attach (year) before the [HD] metadata tag
  2. Episode ID lookup — generate S00E00 from episode title, with multiple ordering support
  3. Episode title lookup — get proper episode title from S00E00
"""

import os
import re
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from difflib import SequenceMatcher

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_FILE = os.path.join(_SCRIPT_DIR, 'tvdb_cache.json')
_CONFIG_ENV = os.path.join(_SCRIPT_DIR, 'config.env')
_BASE_URL = 'https://api4.thetvdb.com/v4'

# ── Cache ────────────────────────────────────────────────────────────────

_cache = None


def _load_cache():
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(_CACHE_FILE):
        try:
            with open(_CACHE_FILE, 'r', encoding='utf-8') as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache():
    global _cache
    if _cache is None:
        return
    try:
        with open(_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, indent=1, ensure_ascii=False)
    except Exception:
        pass


def _cache_get(key):
    c = _load_cache()
    entry = c.get(key)
    if entry is None:
        return None
    # Cache entries expire after 30 days
    if time.time() - entry.get('_ts', 0) > 30 * 86400:
        return None
    return entry.get('data')


def _cache_set(key, data):
    c = _load_cache()
    c[key] = {'data': data, '_ts': time.time()}
    _save_cache()


# ── API helpers ──────────────────────────────────────────────────────────

_token = None
_token_time = 0


def _get_api_key():
    if os.path.exists(_CONFIG_ENV):
        with open(_CONFIG_ENV, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('apikey='):
                    return line.split('=', 1)[1].strip()
    return None


def _get_token():
    global _token, _token_time
    # Reuse token for 23 hours
    if _token and (time.time() - _token_time) < 23 * 3600:
        return _token
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("No TVDB API key found in config.env")
    body = json.dumps({"apikey": api_key}).encode('utf-8')
    req = urllib.request.Request(
        f'{_BASE_URL}/login',
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    _token = data['data']['token']
    _token_time = time.time()
    return _token


def _api_get(path, params=None):
    """GET request to TVDB API with caching."""
    cache_key = path + ('?' + urllib.parse.urlencode(params) if params else '')
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    token = _get_token()
    url = f'{_BASE_URL}{path}'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    data = result.get('data')
    _cache_set(cache_key, data)
    return data


# ── Filename parsing ─────────────────────────────────────────────────────

def _clean_title(raw):
    """Convert a messy filename fragment to a clean title for matching.

    Handles: periods as spaces, trailing garbage (codec, resolution, group tags),
    square-bracket metadata, and release group names.
    """
    # Remove file extension if present
    raw = re.sub(r'\.\w{2,4}$', '', raw)
    # Remove square-bracket tags like [SD 1Mbps MPEG4], [HD 10Mbps HEVC], etc.
    raw = re.sub(r'\[.*?\]', '', raw)
    # Remove parenthesized year/quality tags like (2009), (1080p)
    raw = re.sub(r'\(\d{4}\)', '', raw)
    raw = re.sub(r'\(\d{3,4}[pi]\)', '', raw)
    # Remove common junk suffixes (codec, resolution, source) from FIRST match onward
    raw = re.sub(
        r'[\.\s\-]*(1080[pi]|720[pi]|480[pi]|2160[pi]|4[Kk]|'
        r'[Hh]\.?26[45]|[Xx]\.?26[45]|HEVC|AVC|'
        r'NF|AMZN|DSNP|HULU|WEB[\-\.]?DL|WEB[\-\.]?Rip|BluRay|BDRip|DVDRip|HDTV|'
        r'AAC\d*\.?\d*|DDP?\d*\.?\d*|AC3|FLAC|'
        r'REPACK|PROPER|iNTERNAL'
        r').*', '', raw, flags=re.IGNORECASE)
    # Replace dots/underscores with spaces
    raw = re.sub(r'[._]', ' ', raw)
    # Collapse multiple spaces
    raw = re.sub(r'\s{2,}', ' ', raw)
    # Remove leading/trailing junk
    raw = raw.strip(' -')
    return raw


def parse_filename(filepath):
    """Extract show name, season/episode info, and episode title from a filename.

    Returns dict with keys:
      show_name: str or None
      season: int or None
      episodes: list[int] or None
      episode_title: str or None
      has_sxxexx: bool
    """
    basename = os.path.basename(filepath)
    name_no_ext = os.path.splitext(basename)[0]

    result = {
        'show_name': None,
        'season': None,
        'episodes': None,
        'episode_title': None,
        'has_sxxexx': False,
    }

    # Try to find SxxExx pattern
    sxxexx = re.search(
        r'[Ss](\d{1,2})((?:[Ee]\d{1,3})+)',
        name_no_ext
    )

    if sxxexx:
        result['has_sxxexx'] = True
        result['season'] = int(sxxexx.group(1))
        eps = re.findall(r'[Ee](\d{1,3})', sxxexx.group(2))
        result['episodes'] = [int(e) for e in eps]

        # Show name is everything before the SxxExx
        show_part = name_no_ext[:sxxexx.start()]
        cleaned_show = _clean_title(show_part)
        if cleaned_show:
            result['show_name'] = cleaned_show

        # Episode title is after the SxxExx
        after = name_no_ext[sxxexx.end():]
        if after:
            title = _clean_title(after)
            if title:
                result['episode_title'] = title
    else:
        # No SxxExx — the whole name is the episode title, not the show name.
        # Show name should come from the folder via guess_show_name().
        cleaned = _clean_title(name_no_ext)
        if cleaned:
            result['episode_title'] = cleaned

    return result


def guess_show_name(filepath):
    """Guess show name from parent folder(s).

    Uses parent folder, or parent's parent if in a Season folder.
    Cleans release-group junk and season indicators from folder names.
    """
    parent = os.path.basename(os.path.dirname(filepath))
    grandparent = os.path.basename(os.path.dirname(os.path.dirname(filepath)))

    # If parent looks like "Season X" or "Series X", go up one more
    if re.match(r'^(season|series|s)\s*\d+$', parent, re.IGNORECASE):
        folder = grandparent if grandparent else parent
    else:
        folder = parent if parent else None

    if not folder:
        return None

    # Clean the folder name (remove dots, codec junk, etc.)
    cleaned = _clean_title(folder)
    # Strip trailing season indicators like "S01", "Season 1"
    cleaned = re.sub(r'\s+[Ss]\d{1,2}\s*$', '', cleaned)
    cleaned = re.sub(r'\s+Season\s+\d+\s*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or None


# ── TVDB lookups ─────────────────────────────────────────────────────────

def search_series(name):
    """Search TVDB for a series by name. Returns list of search results."""
    if not name:
        return []
    data = _api_get('/search', {'query': name, 'type': 'series'})
    return data or []


def get_series_extended(series_id):
    """Get extended series info including season types."""
    return _api_get(f'/series/{series_id}/extended', {'short': 'true'})


def get_series_episodes(series_id, season_type='default', page=0):
    """Get all episodes for a series under a given season type.
    
    Fetches all pages automatically.
    """
    cache_key = f'/series/{series_id}/episodes/{season_type}?page=all'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    all_episodes = []
    current_page = 0
    while True:
        data = _api_get(f'/series/{series_id}/episodes/{season_type}',
                        {'page': current_page})
        if data and 'episodes' in data:
            all_episodes.extend(data['episodes'])
            # If less than a full page, we're done
            if len(data['episodes']) < 500:
                break
            current_page += 1
        else:
            break

    _cache_set(cache_key, all_episodes)
    return all_episodes


def get_season_types(series_id):
    """Return available season types for a series."""
    ext = get_series_extended(series_id)
    if not ext:
        return []
    return ext.get('seasonTypes') or []


# ── Matching logic ───────────────────────────────────────────────────────

def _normalize(s):
    """Lowercase, strip punctuation, collapse whitespace."""
    s = s.lower()
    s = re.sub(r'[^a-z0-9\s]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _similarity(a, b):
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def find_best_series(show_name):
    """Search TVDB and return best-matching series.
    
    Returns list of (series_id, name, year, score) sorted by match quality.
    """
    results = search_series(show_name)
    scored = []
    for r in results[:15]:
        name = r.get('name', '')
        year = r.get('year', '')
        tvdb_id = r.get('tvdb_id', r.get('id', ''))
        sim = _similarity(show_name, name)
        scored.append((str(tvdb_id), name, year, sim))
    scored.sort(key=lambda x: -x[3])
    return scored


def match_episode_by_title(episodes, title_query, season_hint=None):
    """Find episodes matching a title query. Handles multi-segment titles.

    For multi-part queries (e.g. "Title A - Title B"), matches each part
    individually to find the best TVDB episode for each segment.

    Returns list of (episode, score) sorted by episode number.
    """
    if not title_query or not episodes:
        return []

    # Filter out specials unless specifically looking for them
    filtered = episodes
    if season_hint is not None and season_hint != 0:
        filtered = [ep for ep in episodes if ep.get('seasonNumber', 0) != 0]
    if not filtered:
        filtered = episodes

    # Split multi-segment title: "Title A - Title B"
    # Require at least one space after the dash to avoid splitting on hyphens
    # within words like "Buck-Tooth" or "Kitty-tastrophe"
    parts = re.split(r'\s*[-–]\s+', title_query)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) > 1:
        return _match_multi_part(filtered, parts, season_hint)
    else:
        return _match_single_part(filtered, title_query, season_hint)


def _match_single_part(episodes, title, season_hint):
    """Match a single title against episodes."""
    scored = []
    for ep in episodes:
        ep_name = ep.get('name', '') or ''
        if not ep_name:
            continue
        sim = _similarity(title, ep_name)
        if season_hint is not None and ep.get('seasonNumber') == season_hint:
            sim = min(1.0, sim + 0.05)
        if sim > 0.3:
            scored.append((ep, sim))
    scored.sort(key=lambda x: (-x[1], x[0].get('seasonNumber', 0), x[0].get('number', 0)))
    return scored[:20]


def _match_multi_part(episodes, parts, season_hint):
    """Match a multi-segment title (e.g. 'Title A - Title B') to individual episodes.

    Finds the best matching episode for each part separately, preventing
    the same episode from matching multiple parts.
    Returns matched episodes ordered by season/episode number.
    """
    used_eps = set()
    matched = []

    for part in parts:
        best_ep = None
        best_score = 0
        for ep in episodes:
            ep_name = ep.get('name', '') or ''
            if not ep_name:
                continue
            ep_key = (ep.get('seasonNumber', 0), ep.get('number', 0))
            if ep_key in used_eps:
                continue
            sim = _similarity(part, ep_name)
            if season_hint is not None and ep.get('seasonNumber') == season_hint:
                sim = min(1.0, sim + 0.05)
            if sim > best_score:
                best_score = sim
                best_ep = ep
        if best_ep and best_score > 0.3:
            used_eps.add((best_ep.get('seasonNumber', 0), best_ep.get('number', 0)))
            matched.append((best_ep, best_score))

    # Sort by season/episode number
    matched.sort(key=lambda x: (x[0].get('seasonNumber', 0), x[0].get('number', 0)))
    return matched


def match_episodes_for_ordering(episodes, season, episode_numbers):
    """For a given season and episode number(s), return the matching episodes.
    
    Returns list of episode records.
    """
    matched = []
    for ep in episodes:
        if ep.get('seasonNumber') == season and ep.get('number') in episode_numbers:
            matched.append(ep)
    matched.sort(key=lambda e: e.get('number', 0))
    return matched


# ── High-level feature functions ─────────────────────────────────────────

def lookup_year(filepath):
    """Feature 1: Look up the year for the series.

    Returns (year_str, confidence, series_name) or (None, 0, None).
    """
    parsed = parse_filename(filepath)
    show_name = parsed['show_name'] or guess_show_name(filepath)
    if not show_name:
        return None, 0, None

    matches = find_best_series(show_name)
    if not matches:
        return None, 0, None

    best_id, best_name, best_year, score = matches[0]
    if score < 0.5:
        return None, score, best_name
    return best_year, score, best_name


def lookup_episode_id(filepath):
    """Feature 2: Look up S00E00 from episode title.

    Returns dict with:
      orderings: dict of {season_type: {
          'episodes': [(season, episode, title, score)...],
          'tag': 'S01E07E08',
          'match_score': float
      }}
      show_name: str
      series_id: str
      query_title: str
    """
    parsed = parse_filename(filepath)
    folder_name = guess_show_name(filepath)

    # Determine show name: prefer folder name when no SxxExx or parsed show is empty
    show_name = parsed['show_name']
    if not show_name:
        show_name = folder_name
    elif not parsed['has_sxxexx'] and folder_name:
        # No SxxExx means parsed show_name is None (episode_title has the filename)
        show_name = folder_name

    if not show_name:
        return None

    series_matches = find_best_series(show_name)
    if not series_matches:
        return None

    # Use best series match
    series_id = series_matches[0][0]
    series_name = series_matches[0][1]

    # Get available season types
    season_types = get_season_types(series_id)
    if not season_types:
        season_types = [{'type': 'default', 'name': 'Default'}]

    # Determine what we're searching for
    title_query = parsed.get('episode_title')
    if not title_query:
        # Try the whole filename as the title
        raw = _clean_title(os.path.splitext(os.path.basename(filepath))[0])
        # Remove show name from the beginning
        if show_name and raw:
            norm_show = _normalize(show_name)
            norm_raw = _normalize(raw)
            if norm_raw.startswith(norm_show):
                leftover = raw[len(show_name):].strip(' -.')
                leftover = _clean_title(leftover) if leftover else ''
                if leftover:
                    title_query = leftover
        if not title_query:
            title_query = raw  # last resort

    season_hint = parsed.get('season')

    orderings = {}
    for st in season_types:
        st_type = st.get('type', 'default')
        st_name = st.get('name', st_type)
        try:
            episodes = get_series_episodes(series_id, st_type)
        except Exception:
            continue
        if not episodes:
            continue

        matches = match_episode_by_title(episodes, title_query, season_hint)
        if not matches:
            continue

        # For single-part titles, only use the best match for the tag.
        # For multi-part titles (A - B), match_episode_by_title already
        # returns one episode per part.
        title_parts = re.split(r'\s*[-–]\s+', title_query)
        title_parts = [p.strip() for p in title_parts if p.strip()]
        is_multi = len(title_parts) > 1

        # Build episode list: all matches for display, limited for tag
        all_eps = []
        for ep, score in matches:
            all_eps.append((
                ep.get('seasonNumber', 0),
                ep.get('number', 0),
                ep.get('name', ''),
                score
            ))

        # For the tag, use all matches if multi-part, else just the best
        tag_eps = all_eps if is_multi else all_eps[:1]

        # Build SxxExx tag
        if tag_eps:
            season = tag_eps[0][0]
            tag = f'S{season:02d}' + ''.join(f'E{e[1]:02d}' for e in tag_eps)
            avg_score = sum(e[3] for e in tag_eps) / len(tag_eps)
        else:
            tag = ''
            avg_score = 0

        orderings[st_name] = {
            'type': st_type,
            'episodes': all_eps,
            'tag_episodes': tag_eps,
            'tag': tag,
            'match_score': avg_score,
        }

    return {
        'orderings': orderings,
        'show_name': series_name,
        'series_id': series_id,
        'series_matches': series_matches[:5],
        'query_title': title_query,
    }


def lookup_episode_title(filepath):
    """Feature 3: Look up episode title from S00E00.

    Returns dict with:
      orderings: dict of {season_type_name: [(season, ep_num, title)...]}
      show_name: str
      series_id: str
    """
    parsed = parse_filename(filepath)
    if not parsed['has_sxxexx'] or not parsed['episodes']:
        return None

    show_name = parsed['show_name'] or guess_show_name(filepath)
    if not show_name:
        return None

    series_matches = find_best_series(show_name)
    if not series_matches:
        return None

    series_id = series_matches[0][0]
    series_name = series_matches[0][1]

    season_types = get_season_types(series_id)
    if not season_types:
        season_types = [{'type': 'default', 'name': 'Default'}]

    season = parsed['season']
    episode_numbers = parsed['episodes']

    orderings = {}
    for st in season_types:
        st_type = st.get('type', 'default')
        st_name = st.get('name', st_type)
        try:
            episodes = get_series_episodes(series_id, st_type)
        except Exception:
            continue
        if not episodes:
            continue

        matched = match_episodes_for_ordering(episodes, season, episode_numbers)
        if matched:
            orderings[st_name] = [
                (ep.get('seasonNumber', 0), ep.get('number', 0), ep.get('name', ''))
                for ep in matched
            ]

    return {
        'orderings': orderings,
        'show_name': series_name,
        'series_id': series_id,
        'series_matches': series_matches[:5],
        'season': season,
        'episodes': episode_numbers,
    }


# ── GUI Integration ─────────────────────────────────────────────────────

def build_tvdb_popup(parent, filepaths, apply_callback):
    """Create the TVDB Lookup popup window with compact table view.

    parent: tk root window
    filepaths: list of file paths to look up
    apply_callback: function(filepath, changes_dict) called when user applies changes.
        changes_dict has keys: 'year', 'sxxexx', 'episode_title'
    """
    import tkinter as tk
    from tkinter import ttk

    _BG = '#1e1e1e'
    _BG2 = '#252526'
    _BG3 = '#2d2d2d'
    _FG = '#cccccc'
    _FG_DIM = '#606060'
    _SEL = '#0078d4'
    _ENT = '#3c3c3c'
    _GREEN = '#4ec994'
    _YELLOW = '#dcdcaa'
    _RED = '#f14c4c'

    popup = tk.Toplevel(parent)
    popup.title("TVDB Lookup")
    popup.geometry("1200x700")
    popup.minsize(900, 400)
    popup.configure(bg=_BG)
    popup.transient(parent)

    # Dark title bar (Windows)
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

    # ── State ────────────────────────────────────────────────────
    global_year = tk.BooleanVar(value=False)   # Year OFF by default
    global_mode = tk.StringVar(value='ep_id')  # 'ep_id' or 'ep_title'
    global_ordering = tk.StringVar(value='')   # '' = best auto
    row_data_list = []       # list of dicts (one per file)
    row_selected = {}        # iid -> bool

    # ── ttk Style ────────────────────────────────────────────────
    style = ttk.Style(popup)
    style.theme_use('clam')
    style.configure('TVDB.Treeview',
        background=_BG2, foreground=_FG, fieldbackground=_BG2,
        rowheight=22, font=('Consolas', 9),
        borderwidth=0, relief='flat')
    style.configure('TVDB.Treeview.Heading',
        background=_BG3, foreground=_FG,
        font=('Consolas', 9, 'bold'),
        relief='flat', borderwidth=1)
    style.map('TVDB.Treeview',
        background=[('selected', _SEL)],
        foreground=[('selected', '#ffffff')])
    style.map('TVDB.Treeview.Heading',
        background=[('active', _ENT)])
    style.configure('TCombobox',
        fieldbackground=_ENT, background=_ENT,
        foreground=_FG, selectbackground=_SEL,
        selectforeground='#ffffff', arrowcolor=_FG)
    style.map('TCombobox',
        fieldbackground=[('readonly', _ENT)],
        background=[('readonly', _ENT)],
        foreground=[('readonly', _FG)])

    # ── Per-row data helpers ──────────────────────────────────────

    def _get_ep_id_for_row(r):
        """Return (ep_tag, score, detail, title_suffix) using current ordering."""
        ord_name = global_ordering.get()
        orderings = r.get('ep_id_orderings', {})
        if not orderings:
            return '', 0, '', None
        if ord_name and ord_name in orderings:
            best = orderings[ord_name]
        else:
            best = max(orderings.values(), key=lambda x: x['match_score'])
        ep_tag = best.get('tag', '')
        score = best.get('match_score', 0)
        tag_eps = best.get('tag_episodes') or best.get('episodes', [])[:1]
        titles = [t for _, _, t, _ in tag_eps if t]
        detail = ' / '.join(titles)
        suffix = (' - ' + ' - '.join(titles)) if titles else None
        return ep_tag, score, detail, suffix

    def _get_ep_title_for_row(r):
        """Return (display_text, apply_text, sxxexx_tag, ord_name) using current ordering."""
        ord_name = global_ordering.get()
        orderings = r.get('ep_title_orderings', {})
        if not orderings:
            return '', None, None, ''
        if ord_name and ord_name in orderings:
            chosen_name = ord_name
            eps = orderings[ord_name]
        else:
            chosen_name, eps = next(
                ((k, v) for k, v in orderings.items() if any(t for _, _, t in v)),
                (None, None))
            if not chosen_name:
                return '', None, None, ''
        titles = [t for _, _, t in eps if t]
        if not titles:
            return '', None, None, chosen_name
        combined = ' - '.join(titles)
        # Build SxxExx tag from the matched episodes
        if eps:
            season = eps[0][0]
            sxxexx = f'S{season:02d}' + ''.join(f'E{e[1]:02d}' for e in eps)
        else:
            sxxexx = None
        return combined, f' - {combined}', sxxexx, chosen_name

    # ── UI update helpers ─────────────────────────────────────────

    def _update_status():
        n_sel = sum(1 for v in row_selected.values() if v)
        parts = []
        if global_year.get():
            parts.append("Year")
        mode = global_mode.get()
        if mode == 'ep_id':
            parts.append("Episode ID")
        elif mode == 'ep_title':
            parts.append("Episode Title")
        what = ', '.join(parts) if parts else 'Nothing'
        status_var.set(f"{n_sel}/{len(row_data_list)} selected  ·  Applying: {what}")

    def _redraw_row(iid):
        r = next((rd for rd in row_data_list if rd.get('iid') == iid), None)
        if r is None:
            return
        sel = row_selected.get(iid, True)
        sel_text = '☑' if sel else '☐'
        year_text = f"({r['year']})" if r.get('year') else ''

        mode = global_mode.get()
        if mode == 'ep_id':
            ep_tag, score, detail, _ = _get_ep_id_for_row(r)
            result_text = ep_tag or '—'
            score_text = f"{score:.0%}" if ep_tag else ''
            detail_text = detail
            if ep_tag:
                row_tag = ('score_green' if score >= 0.8
                           else 'score_yellow' if score >= 0.6
                           else 'score_red')
            else:
                row_tag = 'dim'
        else:
            display, _, _, ord_name = _get_ep_title_for_row(r)
            result_text = display or '—'
            score_text = ''
            detail_text = f'[{ord_name}]' if ord_name else ''
            row_tag = 'ep_title' if display else 'dim'

        if not sel:
            row_tag = 'unsel'

        tree.item(iid,
            values=(sel_text, r['basename'], year_text,
                    result_text, score_text, detail_text),
            tags=(row_tag,))

    def _refresh_results():
        for iid in tree.get_children():
            _redraw_row(iid)
        _update_status()

    def _toggle_row(iid):
        row_selected[iid] = not row_selected.get(iid, True)
        _redraw_row(iid)
        _update_status()

    def _toggle_all():
        # If all selected → deselect all, else select all
        want = not all(row_selected.get(iid, True) for iid in row_selected)
        for iid in row_selected:
            row_selected[iid] = want
        for iid in tree.get_children():
            _redraw_row(iid)
        _update_status()

    def _populate_ordering_combo():
        """Collect all ordering names from all rows, populate the combobox."""
        names = set()
        for r in row_data_list:
            names.update(r.get('ep_id_orderings', {}).keys())
            names.update(r.get('ep_title_orderings', {}).keys())
        choices = ['Best (Auto)'] + sorted(names)
        ordering_combo['values'] = choices
        if ordering_combo.get() not in choices:
            ordering_combo.set('Best (Auto)')

    def _on_ordering_selected(event=None):
        val = ordering_combo.get()
        global_ordering.set('' if val == 'Best (Auto)' else val)
        _refresh_results()

    # ── Lookup + data processing ──────────────────────────────────

    def _process_file(fp):
        """Run all lookups for one file. Returns a data dict."""
        data = {
            'filepath': fp,
            'basename': os.path.basename(fp),
            'year': None, 'year_confidence': 0,
            'ep_id_orderings': {},
            'ep_title_orderings': {},
        }
        try:
            year, conf, _ = lookup_year(fp)
            if year:
                data['year'] = str(year)
                data['year_confidence'] = conf
        except Exception:
            pass
        try:
            ep_result = lookup_episode_id(fp)
            if ep_result and ep_result.get('orderings'):
                data['ep_id_orderings'] = ep_result['orderings']
        except Exception:
            pass
        try:
            title_result = lookup_episode_title(fp)
            if title_result and title_result.get('orderings'):
                data['ep_title_orderings'] = title_result['orderings']
        except Exception:
            pass
        return data

    def _add_row(data):
        iid = tree.insert('', tk.END,
            values=('☑', data['basename'], '', '', '', ''),
            tags=('dim',))
        data['iid'] = iid
        row_data_list.append(data)
        row_selected[iid] = True

    def _lookup_all():
        tree.delete(*tree.get_children())
        row_data_list.clear()
        row_selected.clear()
        ordering_combo.set('Best (Auto)')
        global_ordering.set('')
        _lookup_file_at(0)

    def _lookup_file_at(idx):
        if idx >= len(filepaths):
            _populate_ordering_combo()
            _refresh_results()
            n_id = sum(1 for r in row_data_list if r.get('ep_id_orderings'))
            n_ti = sum(1 for r in row_data_list if r.get('ep_title_orderings'))
            status_var.set(
                f"Done — {len(row_data_list)} files  ·  "
                f"{n_id} with Episode ID  ·  {n_ti} with Title")
            _update_status()
            return
        fp = filepaths[idx]
        status_var.set(
            f"Looking up {idx + 1}/{len(filepaths)}: "
            f"{os.path.basename(fp)}...")
        popup.update_idletasks()
        data = _process_file(fp)
        _add_row(data)
        _redraw_row(data['iid'])
        popup.update_idletasks()
        popup.after(1, lambda: _lookup_file_at(idx + 1))

    def _apply():
        mode = global_mode.get()
        for r in row_data_list:
            iid = r.get('iid')
            if not row_selected.get(iid, True):
                continue
            changes = {'year': None, 'sxxexx': None, 'episode_title': None}
            if global_year.get() and r.get('year'):
                changes['year'] = r['year']
            if mode == 'ep_id':
                ep_tag, _, _, suffix = _get_ep_id_for_row(r)
                if ep_tag:
                    changes['sxxexx'] = ep_tag
                    if suffix:
                        changes['episode_title'] = suffix
            elif mode == 'ep_title':
                _, text, sxxexx_tag, _ = _get_ep_title_for_row(r)
                if text:
                    changes['episode_title'] = text
                if sxxexx_tag:
                    changes['sxxexx'] = sxxexx_tag
            if any(v is not None for v in changes.values()):
                apply_callback(r['filepath'], changes)
        popup.destroy()

    # ── Layout: Global Controls ──────────────────────────────────
    ctrl_frame = tk.Frame(popup, bg=_BG)
    ctrl_frame.pack(fill=tk.X, padx=10, pady=(8, 2))

    tk.Label(ctrl_frame, text="Apply:", font=("Consolas", 10, "bold"),
             bg=_BG, fg=_FG).pack(side=tk.LEFT, padx=(0, 8))

    tk.Checkbutton(ctrl_frame, text="Year", variable=global_year,
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG,
                   command=_update_status).pack(side=tk.LEFT, padx=(0, 12))

    ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, 12), pady=2)

    tk.Radiobutton(ctrl_frame, text="Episode ID (S00E00)",
                   variable=global_mode, value='ep_id',
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG,
                   command=_refresh_results).pack(side=tk.LEFT, padx=(0, 10))

    tk.Radiobutton(ctrl_frame, text="Episode Title",
                   variable=global_mode, value='ep_title',
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG,
                   command=_refresh_results).pack(side=tk.LEFT, padx=(0, 16))

    ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, 12), pady=2)

    tk.Label(ctrl_frame, text="Ordering:", font=("Consolas", 10),
             bg=_BG, fg=_FG).pack(side=tk.LEFT, padx=(0, 4))
    ordering_combo = ttk.Combobox(ctrl_frame, values=['Best (Auto)'],
                                   width=22, state='readonly',
                                   font=("Consolas", 10))
    ordering_combo.set('Best (Auto)')
    ordering_combo.pack(side=tk.LEFT)
    ordering_combo.bind('<<ComboboxSelected>>', _on_ordering_selected)

    ttk.Separator(popup, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=4)

    # ── Layout: Treeview Table ────────────────────────────────────
    table_container = tk.Frame(popup, bg=_BG)
    table_container.pack(fill=tk.BOTH, expand=True, padx=8)

    tree_scroll = ttk.Scrollbar(table_container, orient=tk.VERTICAL)
    tree = ttk.Treeview(
        table_container,
        style='TVDB.Treeview',
        columns=('sel', 'file', 'year', 'result', 'score', 'detail'),
        show='headings',
        yscrollcommand=tree_scroll.set,
        selectmode='none',
    )
    tree_scroll.configure(command=tree.yview)
    tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # Column headings & widths
    tree.heading('sel',    text='✓', anchor='center', command=_toggle_all)
    tree.heading('file',   text='File',   anchor='w')
    tree.heading('year',   text='Year',   anchor='center')
    tree.heading('result', text='Result', anchor='w')
    tree.heading('score',  text='Score',  anchor='center')
    tree.heading('detail', text='Detail', anchor='w')

    tree.column('sel',    width=30,  minwidth=30,  stretch=False, anchor='center')
    tree.column('file',   width=420, minwidth=120, stretch=True,  anchor='w')
    tree.column('year',   width=70,  minwidth=50,  stretch=False, anchor='center')
    tree.column('result', width=130, minwidth=80,  stretch=False, anchor='w')
    tree.column('score',  width=55,  minwidth=45,  stretch=False, anchor='center')
    tree.column('detail', width=340, minwidth=100, stretch=True,  anchor='w')

    # Row colour tags
    tree.tag_configure('score_green',  foreground=_GREEN)
    tree.tag_configure('score_yellow', foreground=_YELLOW)
    tree.tag_configure('score_red',    foreground=_RED)
    tree.tag_configure('ep_title',     foreground=_GREEN)
    tree.tag_configure('dim',          foreground=_FG_DIM)
    tree.tag_configure('unsel',        foreground=_FG_DIM)

    def _on_tree_click(event):
        if tree.identify_region(event.x, event.y) == 'cell':
            if tree.identify_column(event.x) == '#1':
                iid = tree.identify_row(event.y)
                if iid:
                    _toggle_row(iid)

    tree.bind('<ButtonRelease-1>', _on_tree_click)
    tree.bind('<MouseWheel>',
              lambda e: tree.yview_scroll(int(-1 * (e.delta / 120)), 'units'))

    # ── Layout: Bottom Bar ───────────────────────────────────────
    bottom_frame = tk.Frame(popup, bg=_BG)
    bottom_frame.pack(fill=tk.X, padx=10, pady=(4, 8))

    status_var = tk.StringVar(value="Starting lookup...")
    tk.Label(bottom_frame, textvariable=status_var, font=("Consolas", 9),
             bg=_BG, fg=_FG_DIM).pack(side=tk.LEFT)

    ttk.Button(bottom_frame, text="Cancel",
               command=popup.destroy).pack(side=tk.RIGHT)
    ttk.Button(bottom_frame, text="Apply Selected",
               command=_apply).pack(side=tk.RIGHT, padx=(0, 5))
    ttk.Button(bottom_frame, text="Re-Lookup",
               command=_lookup_all).pack(side=tk.RIGHT, padx=(0, 5))

    # Auto-start
    popup.after(100, _lookup_all)

    return popup
