# Copilot Instructions

## CRITICAL RULES — READ FIRST, EVERY TIME

1. **The user's code is ALWAYS up to date.** Never assume the running GUI or script is outdated or out of sync with the file on disk. Never suggest restarting or reloading as a fix.
2. **ALWAYS run the actual code to confirm the bug before reasoning about it.** Use `run_in_terminal` to reproduce the exact output, then fix it. Do NOT theorize for more than one step without a terminal command to prove it.
3. **Do not stop working until the output of a terminal run confirms the fix is correct.**

---

## Project Overview

**Video Standardizer** — a Python utility for processing and standardizing a video file library.
- Location: `Z:\Other\Tools\`
- Python 3.14, venv at `.venv\`
- Run tests: `python -m pytest test_tvdb_lookup.py -q`
- Syntax check: `python -c "import py_compile; py_compile.compile('video_standardizer.py', doraise=True); print('OK')"`
- GUI launch: `python video_standardizer.pyw` (no console) or `python video_standardizer.py` (with console)

### Files

| File | Purpose |
|------|---------|
| `video_standardizer.py` | Main app — CLI entry point, all processing logic, Tkinter GUI |
| `video_standardizer.pyw` | Identical launcher, no console window |
| `tvdb_lookup.py` | TVDB API module — year, episode ID, episode title lookups + popup GUI |
| `test_tvdb_lookup.py` | 125 pytest tests for tvdb_lookup.py |
| `tvdb_cache.json` | Disk cache for TVDB API responses |
| `config.env` | TVDB API key (`TVDB_API_KEY=...`) |
| `video_standardizer_config.json` | Persisted GUI state (last folder, etc.) |
| `conversion_errors.log` | ffmpeg failure details |

### Core Architecture

**`video_standardizer.py` key functions:**
- `process_file(file, ...)` — probes file, selects streams, runs ffmpeg remux or os.rename
- `build_file_plan(file, ...)` — returns human-readable plan string (shown before processing)
- `build_output_filename(file, extension, streams, format_info, ..., tvdb_changes)` — constructs output path
- `extract_episode_info(filename)` — parses `SxxExx` tags from filenames
- `select_audio_tracks(streams, keep_languages)` — picks best track per language
- `select_subtitle_tracks(streams)` — picks English/compatible subtitle tracks
- `launch_gui()` — starts Tkinter GUI (`VideoStandardizerGUI` class)

**`tvdb_lookup.py` key functions:**
- `lookup_year(filepath)` → `(year, confidence, series_matches)`
- `lookup_episode_id(filepath)` → orderings dict with `tag`, `match_score`, `episodes`, `tag_episodes`
- `lookup_episode_title(filepath)` → orderings dict with `(season, ep_num, title)` tuples
- `build_tvdb_popup(parent, filepaths, apply_callback)` — Treeview-based popup; calls `apply_callback(filepath, changes_dict)` on Apply
- `parse_filename(filepath)` → `{show_name, season, episodes, episode_title, has_sxxexx}`
- `guess_show_name(filepath)` — derives show name from parent folder(s)

### `tvdb_changes` Dict

Passed from TVDB popup → `build_output_filename` / `process_file`:
```python
{
    'year': '2006',           # str or None — inserted as (year) before [HD...] tag
    'sxxexx': 'S01E01',      # str matching ^[Ss]\d{1,2}([Ee]\d{1,3})+$ or None
    'episode_title': ' - From Scratch',  # str with leading ' - ' or None
}
```
- Both `sxxexx` and `episode_title` are set together in ep_id and ep_title modes
- `sxxexx` is validated with regex before use — never trust it raw

### Rename vs Remux Logic

```python
same_container = input_ext == actual_extension.lower()
will_rename = same_container and (rename or (same_audio and same_subs and no_external_subs))
```
- `rename=True` flag: rename even if streams would differ — but ONLY within same container
- Container mismatch always forces remux regardless of rename flag
- `convert_force=True` only bypasses the "already processed" skip — does NOT force remux

### Output Filename

Format: `SxxExx - Title (Year) [Resolution Bitrate CODEC].ext`
- Existing `[HD xMbps CODEC]` bracket is stripped before appending new one (prevents doubling on re-process)
- Illegal filename characters (`/ \ : * ? " < > |`) are replaced with ` -`
- Already-processed files (`[HD xMbps CODEC].ext` pattern) are skipped UNLESS `convert_force=True` OR `tvdb_changes` has at least one non-None value

### GUI Options

| Variable | Meaning |
|----------|---------|
| `dry_run_var` | Preview only — no file operations |
| `rename_var` | Rename Only (no re-encode) |
| `norename_var` | Keep original filename |
| `keep_suffix_var` | Keep existing episode title suffix from filename |
| `force_var` | Force re-process (skip "already done" check) |
| `delete_original_var` | Delete input file after successful remux |
| `verbose_var` | Print ffmpeg command + progress |

### Dark Theme Colors

```python
_BG  = '#1e1e1e'
_BG2 = '#252526'
_FG  = '#cccccc'
_SEL = '#0078d4'
_ENT = '#3c3c3c'
_BOR = '#555555'
_ACC = '#0e639c'  # accent button
```

### TVDB API

- Base URL: `https://api4.thetvdb.com/v4`
- Key stored in `config.env` as `TVDB_API_KEY=...`
- All responses cached in `tvdb_cache.json` with TTL
- Season types: Aired Order, DVD Order, Absolute, etc. — user selects via combobox in popup

### Known Patterns / Past Bugs

- TVDB episode titles containing `/` were treated as path separators → fixed by sanitizing illegal chars in `build_output_filename`
- `[HD xMbps CODEC]` pattern in filename causes early-return `None` from `build_output_filename` — bypassed when `tvdb_changes` has a non-None value
- `sxxexx` field in `tvdb_changes` can contain garbage (episode title text) if lookup goes wrong — validated with regex `^[Ss]\d{1,2}([Ee]\d{1,3})+$` before use
- Container line in plan (`MKV → MP4`) was comparing user preference vs actual, not input vs actual — fixed to compare `input_ext` vs `actual_extension`
- Plan stream lines show `→ copy / → DROP` only for REMUX action; rename-only shows plain informational lines
