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
    square-bracket metadata, and release group names. Callers are expected to
    strip the file extension themselves — we can't do it safely here because
    a trailing ".Eyes" or ".Way" in a title fragment is indistinguishable from
    a short extension.
    """
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


def search_movies(name, year=None):
    """Search TVDB for a movie by name (and optionally a year hint)."""
    if not name:
        return []
    params = {'query': name, 'type': 'movie'}
    if year:
        params['year'] = str(year)
    data = _api_get('/search', params)
    return data or []


def get_series_extended(series_id):
    """Get extended series info including season types."""
    return _api_get(f'/series/{series_id}/extended', {'short': 'true'})


def get_series_episodes(series_id, season_type='default', page=0):
    """Get all episodes for a series under a given season type.
    
    Fetches all pages automatically.
    """
    # v2: previous cache builder had a pagination bug that could truncate
    # long episode lists. Bumping the key invalidates those stale entries.
    cache_key = f'/series/{series_id}/episodes/{season_type}?page=all&v=2'
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    all_episodes = []
    current_page = 0
    prev_page_size = None
    while True:
        data = _api_get(f'/series/{series_id}/episodes/{season_type}',
                        {'page': current_page})
        if not data or 'episodes' not in data:
            break
        page_eps = data['episodes']
        if not page_eps:
            break
        all_episodes.extend(page_eps)
        # Stop when a page is shorter than the previous one — TVDB's page size
        # varies by endpoint/season type, so we can't hardcode it.
        if prev_page_size is not None and len(page_eps) < prev_page_size:
            break
        prev_page_size = len(page_eps)
        current_page += 1
        # Hard safety cap to avoid runaway paging on a misbehaving API.
        if current_page > 50:
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


def find_best_movie(title, year_hint=None):
    """Search TVDB for a movie and rank results by title similarity.

    If year_hint is provided, results whose year matches get a small boost.
    Returns list of (movie_id, name, year, score) sorted by match quality.
    """
    results = search_movies(title, year=year_hint)
    # If a year-constrained query returns nothing, retry unconstrained.
    if not results and year_hint:
        results = search_movies(title)
    scored = []
    for r in results[:15]:
        name = r.get('name', '')
        year = r.get('year', '')
        tvdb_id = r.get('tvdb_id', r.get('id', ''))
        sim = _similarity(title, name)
        if year_hint and year and str(year) == str(year_hint):
            sim = min(1.0, sim + 0.1)
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


# ── Orderings builder ────────────────────────────────────────────────────

def _build_orderings_for_series(series_id, season_types, title_query,
                                 season_hint, has_sxxexx, season,
                                 episode_numbers):
    """Build ep_id and ep_title orderings for a series.

    Returns (ep_id_orderings, ep_title_orderings) dicts.
    Called by lookup_all and by the GUI series-change handler.
    """
    ep_id_orderings = {}
    ep_title_orderings = {}

    for st in season_types:
        st_type = st.get('type', 'default')
        st_name = st.get('name', st_type)
        try:
            episodes = get_series_episodes(series_id, st_type)
        except Exception:
            continue
        if not episodes:
            continue

        # ep_id: match by title
        if title_query:
            matches = match_episode_by_title(episodes, title_query, season_hint)
            if matches:
                title_parts = re.split(r'\s*[-\u2013]\s+', title_query)
                title_parts = [p.strip() for p in title_parts if p.strip()]
                is_multi = len(title_parts) > 1

                all_eps = [
                    (ep.get('seasonNumber', 0), ep.get('number', 0),
                     ep.get('name', ''), sc)
                    for ep, sc in matches
                ]
                tag_eps = all_eps if is_multi else all_eps[:1]

                if tag_eps:
                    ep_season = tag_eps[0][0]
                    tag = f'S{ep_season:02d}' + ''.join(f'E{e[1]:02d}' for e in tag_eps)
                    avg_score = sum(e[3] for e in tag_eps) / len(tag_eps)
                else:
                    tag = ''
                    avg_score = 0

                ep_id_orderings[st_name] = {
                    'type': st_type,
                    'episodes': all_eps,
                    'tag_episodes': tag_eps,
                    'tag': tag,
                    'match_score': avg_score,
                }

        # ep_title: match by SxxExx
        if has_sxxexx and season is not None and episode_numbers:
            matched = match_episodes_for_ordering(episodes, season, episode_numbers)
            if matched:
                ep_title_orderings[st_name] = [
                    (ep.get('seasonNumber', 0), ep.get('number', 0), ep.get('name', ''))
                    for ep in matched
                ]

    return ep_id_orderings, ep_title_orderings


# ── Movie lookup ─────────────────────────────────────────────────────────

def parse_movie_filename(filepath):
    """Extract a clean movie title + optional year from a filename.

    Returns dict: {'title': str or None, 'year': str or None}
    """
    basename = os.path.basename(filepath)
    name_no_ext = os.path.splitext(basename)[0]

    # Pull a (YYYY) or .YYYY. year, if any, before _clean_title strips it.
    year = None
    m = re.search(r'\((\d{4})\)', name_no_ext)
    if m:
        year = m.group(1)
    else:
        m = re.search(r'[.\s_\-](19\d{2}|20\d{2})(?:[.\s_\-]|$)', name_no_ext)
        if m:
            year = m.group(1)

    title = _clean_title(name_no_ext)

    # If the year was embedded mid-string without surrounding delimiters that
    # _clean_title would match (e.g. trailing "... 1994"), strip it off.
    if year and title.endswith(year):
        title = title[:-len(year)].rstrip(' -.')

    return {'title': title or None, 'year': year}


def lookup_movie(filepath):
    """Search TVDB for a movie matching `filepath`.

    Returns dict with:
      title: canonical movie title (or None)
      year: str or None
      score: float 0..1
      matches: list of (movie_id, name, year, score)  (alternates, up to 5)
      query_title: the cleaned filename stem used as the query
    """
    parsed = parse_movie_filename(filepath)
    result = {
        'title': None, 'year': None, 'score': 0,
        'matches': [], 'query_title': parsed['title'],
    }
    if not parsed['title']:
        return result

    matches = find_best_movie(parsed['title'], year_hint=parsed['year'])
    if not matches:
        return result

    _movie_id, name, year, score = matches[0]
    result['matches'] = matches[:5]
    result['score'] = score
    if score >= 0.5:
        result['title'] = name
        if year:
            result['year'] = str(year)
    return result


# ── Combined lookup (year + ep_id + ep_title in one pass) ───────────────

def lookup_all(filepath):
    """Combined year + episode-ID + episode-title lookup in a single API pass.

    Fetches series data once (one search, one extended-info, one episode-list
    per ordering type) and computes all three results without redundant calls.

    Returns dict with:
      year: str or None
      year_confidence: float
      ep_id_orderings: dict (same structure as lookup_episode_id 'orderings')
      ep_title_orderings: dict (same structure as lookup_episode_title 'orderings')
      show_name: str or None
      series_id: str or None
      series_matches: list
      query_title: str or None
    """
    parsed = parse_filename(filepath)
    folder_name = guess_show_name(filepath)

    # Show-name logic from lookup_episode_id (handles SxxExx-less filenames)
    show_name = parsed['show_name']
    if not show_name:
        show_name = folder_name
    elif not parsed['has_sxxexx'] and folder_name:
        show_name = folder_name

    result = {
        'year': None, 'year_confidence': 0,
        'ep_id_orderings': {}, 'ep_title_orderings': {},
        'show_name': None, 'series_id': None,
        'series_matches': [], 'query_title': None,
    }

    if not show_name:
        return result

    # ── One series search shared across all three features ──────────────
    series_matches = find_best_series(show_name)
    if not series_matches:
        return result

    best_id, best_name, best_year, score = series_matches[0]
    result['show_name'] = best_name
    result['series_id'] = best_id
    result['series_matches'] = series_matches[:5]
    result['year_confidence'] = score

    # Year comes straight from the search result — no extra API call
    if score >= 0.5 and best_year:
        result['year'] = str(best_year)

    # ── One extended-info call for season types ─────────────────────────
    season_types = get_season_types(best_id)
    if not season_types:
        season_types = [{'type': 'default', 'name': 'Default'}]

    # Build ep_id title query (same logic as lookup_episode_id)
    title_query = parsed.get('episode_title')
    if not title_query:
        raw = _clean_title(os.path.splitext(os.path.basename(filepath))[0])
        if show_name and raw:
            norm_show = _normalize(show_name)
            norm_raw = _normalize(raw)
            if norm_raw.startswith(norm_show):
                leftover = raw[len(show_name):].strip(' -.')
                leftover = _clean_title(leftover) if leftover else ''
                if leftover:
                    title_query = leftover
        if not title_query:
            title_query = raw
    result['query_title'] = title_query

    season_hint = parsed.get('season')
    has_sxxexx = parsed.get('has_sxxexx', False)
    season = parsed.get('season')
    episode_numbers = parsed.get('episodes')

    result['ep_id_orderings'], result['ep_title_orderings'] = \
        _build_orderings_for_series(
            best_id, season_types, title_query,
            season_hint, has_sxxexx, season, episode_numbers,
        )

    return result


# ── GUI Integration ─────────────────────────────────────────────────────

def build_tvdb_popup(parent, filepaths, apply_callback, on_apply_done=None):
    """Create the TVDB Lookup popup window with compact table view.

    parent: tk root window
    filepaths: list of file paths to look up
    apply_callback: function(filepath, changes_dict) called once per file the
        user selected to apply. changes_dict keys: 'year', 'sxxexx',
        'episode_title', 'movie_title'.
    on_apply_done: optional function(applied_filepaths) called after all
        per-file apply_callback calls finish, before the popup closes. Lets the
        caller sync its own UI selection to the popup's selection.
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
    global_mode = tk.StringVar(value='ep_id')  # 'ep_id' or 'ep_title' (TV only)
    global_ordering = tk.StringVar(value='')   # '' = best auto
    content_type = tk.StringVar(value='tv')    # 'tv' or 'movie'
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
        if ord_name:
            # User picked a specific ordering — honor it exactly. If this row
            # has no data under that ordering, show nothing rather than
            # silently falling back to a different ordering.
            if ord_name not in orderings:
                return '', 0, '', None
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
        if ord_name:
            # User picked a specific ordering — honor it exactly. No silent
            # fallback to a different ordering for rows that lack data here.
            if ord_name not in orderings:
                return '', None, None, ''
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
        if content_type.get() == 'movie':
            parts.append("Movie Title")
        else:
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

        if content_type.get() == 'movie':
            title = r.get('movie_title')
            score = r.get('movie_score', 0)
            result_text = title or '—'
            score_text = f"{score:.0%}" if title else ''
            detail_text = r.get('query_title', '') or ''
            if title:
                row_tag = ('score_green' if score >= 0.8
                           else 'score_yellow' if score >= 0.6
                           else 'score_red')
            else:
                row_tag = 'dim'
        else:
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
            'series_matches': [],
            'selected_series_idx': 0,
            'query_title': None,
            # Movie-mode fields
            'movie_title': None,
            'movie_score': 0,
            'movie_matches': [],
            'selected_movie_idx': 0,
        }
        try:
            if content_type.get() == 'movie':
                mres = lookup_movie(fp)
                data['movie_title'] = mres.get('title')
                data['movie_score'] = mres.get('score', 0)
                data['movie_matches'] = mres.get('matches', [])
                data['query_title'] = mres.get('query_title')
                if mres.get('year'):
                    data['year'] = mres['year']
                    data['year_confidence'] = mres.get('score', 0)
            else:
                result = lookup_all(fp)
                data['year'] = result.get('year')
                data['year_confidence'] = result.get('year_confidence', 0)
                data['ep_id_orderings'] = result.get('ep_id_orderings', {})
                data['ep_title_orderings'] = result.get('ep_title_orderings', {})
                data['series_matches'] = result.get('series_matches', [])
                data['query_title'] = result.get('query_title')
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
        series_combo.set('')
        series_combo.configure(state='disabled')
        _lookup_file_at(0)

    def _lookup_file_at(idx):
        if idx >= len(filepaths):
            _populate_ordering_combo()
            _refresh_results()
            if content_type.get() == 'movie':
                n_found = sum(1 for r in row_data_list if r.get('movie_title'))
                status_var.set(
                    f"Done — {len(row_data_list)} files  ·  "
                    f"{n_found} with Movie match")
            else:
                n_id = sum(1 for r in row_data_list if r.get('ep_id_orderings'))
                n_ti = sum(1 for r in row_data_list if r.get('ep_title_orderings'))
                status_var.set(
                    f"Done — {len(row_data_list)} files  ·  "
                    f"{n_id} with Episode ID  ·  {n_ti} with Title")
            _populate_series_combo()
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
        is_movie = content_type.get() == 'movie'
        mode = global_mode.get()
        applied_paths = []
        for r in row_data_list:
            iid = r.get('iid')
            if not row_selected.get(iid, True):
                continue
            changes = {
                'year': None, 'sxxexx': None,
                'episode_title': None, 'movie_title': None,
            }
            if global_year.get() and r.get('year'):
                changes['year'] = r['year']
            if is_movie:
                if r.get('movie_title'):
                    changes['movie_title'] = r['movie_title']
            elif mode == 'ep_id':
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
                applied_paths.append(r['filepath'])
        if on_apply_done is not None:
            try:
                on_apply_done(applied_paths)
            except Exception:
                pass
        popup.destroy()

    # ── Layout: Global Controls ──────────────────────────────────
    ctrl_frame = tk.Frame(popup, bg=_BG)
    ctrl_frame.pack(fill=tk.X, padx=10, pady=(8, 2))

    tk.Label(ctrl_frame, text="Type:", font=("Consolas", 10, "bold"),
             bg=_BG, fg=_FG).pack(side=tk.LEFT, padx=(0, 8))

    # Content-type toggle (TV / Movie) — driven by a callback defined later.
    type_tv_btn = tk.Radiobutton(ctrl_frame, text="TV Show",
                   variable=content_type, value='tv',
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG)
    type_tv_btn.pack(side=tk.LEFT, padx=(0, 4))
    type_movie_btn = tk.Radiobutton(ctrl_frame, text="Movie",
                   variable=content_type, value='movie',
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG)
    type_movie_btn.pack(side=tk.LEFT, padx=(0, 12))

    ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(
        side=tk.LEFT, fill=tk.Y, padx=(0, 12), pady=2)

    tk.Label(ctrl_frame, text="Apply:", font=("Consolas", 10, "bold"),
             bg=_BG, fg=_FG).pack(side=tk.LEFT, padx=(0, 8))

    tk.Checkbutton(ctrl_frame, text="Year", variable=global_year,
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG,
                   command=_update_status).pack(side=tk.LEFT, padx=(0, 12))

    # TV-only controls live in their own frame so we can hide/show them
    # as a unit when the user toggles between TV and Movie mode.
    tv_only_frame = tk.Frame(ctrl_frame, bg=_BG)
    tv_only_frame.pack(side=tk.LEFT)

    tv_sep1 = ttk.Separator(tv_only_frame, orient=tk.VERTICAL)
    tv_sep1.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12), pady=2)

    tk.Radiobutton(tv_only_frame, text="Episode ID (S00E00)",
                   variable=global_mode, value='ep_id',
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG,
                   command=_refresh_results).pack(side=tk.LEFT, padx=(0, 10))

    tk.Radiobutton(tv_only_frame, text="Episode Title",
                   variable=global_mode, value='ep_title',
                   font=("Consolas", 10), bg=_BG, fg=_FG,
                   selectcolor=_ENT, activebackground=_BG, activeforeground=_FG,
                   command=_refresh_results).pack(side=tk.LEFT, padx=(0, 16))

    tv_sep2 = ttk.Separator(tv_only_frame, orient=tk.VERTICAL)
    tv_sep2.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12), pady=2)

    tk.Label(tv_only_frame, text="Ordering:", font=("Consolas", 10),
             bg=_BG, fg=_FG).pack(side=tk.LEFT, padx=(0, 4))
    ordering_combo = ttk.Combobox(tv_only_frame, values=['Best (Auto)'],
                                   width=22, state='readonly',
                                   font=("Consolas", 10))
    ordering_combo.set('Best (Auto)')
    ordering_combo.pack(side=tk.LEFT)
    ordering_combo.bind('<<ComboboxSelected>>', _on_ordering_selected)

    match_sep = ttk.Separator(ctrl_frame, orient=tk.VERTICAL)
    match_sep.pack(side=tk.LEFT, fill=tk.Y, padx=(12, 12), pady=2)

    match_label = tk.Label(ctrl_frame, text="Series:", font=("Consolas", 10),
             bg=_BG, fg=_FG)
    match_label.pack(side=tk.LEFT, padx=(0, 4))
    series_combo = ttk.Combobox(ctrl_frame, values=[], width=34,
                                 state='disabled', font=("Consolas", 10))
    series_combo.pack(side=tk.LEFT)
    # series_combo event binding added below after _on_series_selected is defined

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

    # Per-row movie picker — a floating Combobox positioned over the Result
    # cell of the clicked row. Only appears in movie mode.
    _row_picker = {'combo': None, 'iid': None}

    def _close_row_picker(event=None):
        combo = _row_picker.get('combo')
        if combo is not None:
            try:
                combo.destroy()
            except Exception:
                pass
        _row_picker['combo'] = None
        _row_picker['iid'] = None

    def _open_movie_row_picker(iid):
        _close_row_picker()
        r = next((rd for rd in row_data_list if rd.get('iid') == iid), None)
        if r is None:
            return
        matches_list = r.get('movie_matches', [])
        if not matches_list:
            return
        bbox = tree.bbox(iid, column='result')
        if not bbox:
            return
        x, y, w, h = bbox
        choices = _match_choices(matches_list)
        combo = ttk.Combobox(tree, values=choices, state='readonly',
                             font=("Consolas", 9))
        cur_idx = r.get('selected_movie_idx', 0)
        combo.set(choices[cur_idx] if cur_idx < len(choices) else choices[0])
        combo.place(x=x, y=y, width=max(w, 260), height=h)
        _row_picker['combo'] = combo
        _row_picker['iid'] = iid

        def _on_pick(event=None):
            val = combo.get()
            if val in choices:
                new_idx = choices.index(val)
                if new_idx != r.get('selected_movie_idx', 0):
                    _recompute_movie_for_row(r, new_idx)
                    _update_status()
            _close_row_picker()

        combo.bind('<<ComboboxSelected>>', _on_pick)
        combo.bind('<Escape>', lambda e: _close_row_picker())
        combo.bind('<FocusOut>', lambda e: _close_row_picker())
        # Open the dropdown immediately so the user doesn't need a second click
        combo.focus_set()
        combo.event_generate('<Button-1>')

    def _on_tree_click(event):
        if tree.identify_region(event.x, event.y) != 'cell':
            _close_row_picker()
            return
        iid = tree.identify_row(event.y)
        if not iid:
            return
        col = tree.identify_column(event.x)
        if col == '#1':
            _close_row_picker()
            _toggle_row(iid)
        elif col == '#4' and content_type.get() == 'movie':
            _open_movie_row_picker(iid)
        else:
            _close_row_picker()

    tree.bind('<ButtonRelease-1>', _on_tree_click)

    # Scroll wheel: close any open per-row picker, otherwise it'd hover over
    # the wrong row once the underlying cell moves.
    def _on_tree_wheel(e):
        _close_row_picker()
        tree.yview_scroll(int(-1 * (e.delta / 120)), 'units')

    tree.bind('<MouseWheel>', _on_tree_wheel)

    # ── Series selector (global) ───────────────────────────────

    def _recompute_series_for_row(r, series_idx):
        """Re-run orderings for one row using a different series match."""
        matches_list = r.get('series_matches', [])
        if not matches_list or series_idx >= len(matches_list):
            return
        series_id, series_name, series_year, series_score = matches_list[series_idx]
        r['selected_series_idx'] = series_idx
        r['year'] = str(series_year) if (series_score >= 0.5 and series_year) else None
        r['year_confidence'] = series_score
        try:
            season_types = get_season_types(series_id)
            if not season_types:
                season_types = [{'type': 'default', 'name': 'Default'}]
        except Exception:
            season_types = [{'type': 'default', 'name': 'Default'}]
        parsed = parse_filename(r['filepath'])
        r['ep_id_orderings'], r['ep_title_orderings'] = _build_orderings_for_series(
            series_id, season_types, r.get('query_title'),
            parsed.get('season'), parsed.get('has_sxxexx', False),
            parsed.get('season'), parsed.get('episodes'),
        )
        _redraw_row(r['iid'])

    def _recompute_movie_for_row(r, movie_idx):
        """Switch this row to a different movie match from its candidate list."""
        matches_list = r.get('movie_matches', [])
        if not matches_list or movie_idx >= len(matches_list):
            return
        _movie_id, name, year, score = matches_list[movie_idx]
        r['selected_movie_idx'] = movie_idx
        r['movie_title'] = name if score >= 0.5 else None
        r['movie_score'] = score
        r['year'] = str(year) if (score >= 0.5 and year) else None
        r['year_confidence'] = score
        _redraw_row(r['iid'])

    def _match_choices(matches_list):
        return [
            f"{name} ({year})  {score:.0%}" if year else f"{name}  {score:.0%}"
            for _, name, year, score in matches_list
        ]

    def _populate_series_combo():
        """Populate the top-bar series combo from the first row's matches.

        Only used in TV mode — movies get a per-row dropdown instead.
        """
        if content_type.get() == 'movie' or not row_data_list:
            series_combo['values'] = []
            series_combo.set('')
            series_combo.configure(state='disabled')
            return
        matches_list = row_data_list[0].get('series_matches', [])
        if not matches_list:
            series_combo['values'] = []
            series_combo.set('No matches')
            series_combo.configure(state='disabled')
            return
        choices = _match_choices(matches_list)
        series_combo['values'] = choices
        cur_idx = row_data_list[0].get('selected_series_idx', 0)
        series_combo.set(choices[cur_idx] if cur_idx < len(choices) else choices[0])
        series_combo.configure(state='readonly')

    def _on_series_selected(event=None):
        if not row_data_list or content_type.get() == 'movie':
            return
        matches_list = row_data_list[0].get('series_matches', [])
        choices = _match_choices(matches_list)
        val = series_combo.get()
        if val not in choices:
            return
        new_idx = choices.index(val)
        if new_idx == row_data_list[0].get('selected_series_idx', 0):
            return
        status_var.set("Re-looking up with new match...")
        popup.update_idletasks()
        for r in row_data_list:
            _recompute_series_for_row(r, new_idx)
        _populate_ordering_combo()
        _refresh_results()
        _update_status()

    series_combo.bind('<<ComboboxSelected>>', _on_series_selected)

    def _on_type_changed():
        """Show/hide TV-only controls and re-run the lookup in the new mode."""
        if content_type.get() == 'movie':
            tv_only_frame.pack_forget()
            # Movies get a per-row dropdown; hide the top-bar match combo.
            match_sep.pack_forget()
            match_label.pack_forget()
            series_combo.pack_forget()
        else:
            # Re-insert tv_only_frame and the match combo in their original
            # slots (just before their trailing siblings in ctrl_frame).
            tv_only_frame.pack(side=tk.LEFT, before=match_sep)
            match_sep.pack(side=tk.LEFT, fill=tk.Y, padx=(12, 12), pady=2)
            match_label.pack(side=tk.LEFT, padx=(0, 4))
            series_combo.pack(side=tk.LEFT)
            match_label.configure(text="Series:")
        _lookup_all()

    type_tv_btn.configure(command=_on_type_changed)
    type_movie_btn.configure(command=_on_type_changed)

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
