# Video Standardizer — Audit Report
> Generated: 2026-04-30

## BUGS

### BUG-4 — `get_series_episodes` pagination termination heuristic is fragile
**File:** `tvdb_lookup/tvdb_lookup.py` — `get_series_episodes()`  
**Severity:** Data truncation (silent)  
**Description:**  
Pagination stops when "a page is shorter than the previous one." This breaks if TVDB returns the same page size for every page (e.g. 100 episodes / 100 per page = exactly 1 full page). The loop would never terminate naturally and only exits at the 50-page safety cap. Conversely, if the first page happens to return fewer episodes than a subsequent page could (e.g. the first query returns 50, next 100), it would stop too early.  
**Correct fix:** Stop when the current page returns 0 episodes *or* when the page count exhausts a known page size (TVDB uses 100). A reliable termination condition would be: stop when `len(page_eps) < 100` (if 100 is the confirmed page size), or better, check for a `links.next` field in the response if TVDB provides one.

---

### BUG-5 — `probe_optional_features` reads `apikey=` but main app reads `TVDB_API_KEY=`
**File:** `video_standardizer.py` — `probe_optional_features()` vs  
`tvdb_lookup/tvdb_lookup.py` — `_get_api_key()`  
**Severity:** Feature incorrectly disabled  
**Description:**  
`probe_optional_features()` checks for `apikey=...` (lowercase) in `config.env`.  
`tvdb_lookup._get_api_key()` also reads `apikey=...` (lowercase).  
The actual `config.env` in the repo uses lowercase `apikey=`, so both agree — **no current breakage**. However the copilot instructions doc says the format is `TVDB_API_KEY=...` (uppercase). If the env file is ever written in the uppercase format (e.g. following the README), the TVDB button would be grayed out in the GUI but lookups would still fail at runtime with "No TVDB API key found." Recommend aligning both readers to the same key name and documenting the format clearly.

---

### BUG-6 — `_build_orderings_for_series` multi-part title split uses mixed regex
**File:** `tvdb_lookup/tvdb_lookup.py` — `_build_orderings_for_series()`  
**Severity:** Minor — inconsistent multi-part detection  
**Description:**  
`match_episode_by_title` splits on `r'\s*[-–]\s+'` (requires at least one trailing space).  
`_build_orderings_for_series` splits on `r'\s*[-\u2013]\s+'` (same semantically) to detect multi-part.  
This is consistent *enough*, but the en-dash in the second regex is specified as a raw unicode escape (`\u2013`) rather than the literal character `–`. This is harmless but inconsistent with how `match_episode_by_title` uses the literal `–`. No behavioral difference, but worth standardizing.

---

### BUG-7 — `_recycle_files` can recycle files not in the listbox when nothing is selected
**File:** `video_standardizer.py` — `_recycle_files()` via `_get_selected_files()`  
**Severity:** Dangerous — silent over-deletion  
**Description:**  
`_get_selected_files()` returns **all files in the list** when nothing is highlighted. This is intentional for Run (process everything if nothing selected), but for a **destructive** action like Recycle it means clicking "Recycle" with nothing selected will silently propose to delete every file in the current list. The confirmation dialog does name the files, but a user conditioned to "nothing selected = no-op" may be surprised.  
**Suggested fix:** In `_recycle_files`, if `self.file_listbox.curselection()` is empty, show an informational message ("Select files to recycle first") rather than falling through to the full list.

---

### BUG-8 — `_on_path_change` does not clear `_tvdb_changes` when the input path changes
**File:** `video_standardizer.py` — `_on_path_change()`  
**Severity:** Stale data / wrong filenames  
**Description:**  
`self._tvdb_changes` is a `{filepath: changes_dict}` dict that accumulates TVDB overrides. When the user browses to a different folder, `_on_path_change` rebuilds `_file_paths` and the listbox but does **not** clear `_tvdb_changes`. If a file in the new folder happens to have the same full path as a file from the previous folder (unlikely but possible in edge cases), it would inherit stale TVDB changes. More practically, the dict grows unboundedly as the user browses around, which is a minor memory issue for large sessions.  
**Suggested fix:** Clear `self._tvdb_changes` in `_on_path_change` (or at least prune keys that are no longer in `self._file_paths`).

---

### BUG-9 — `get_series_episodes` pagination may not fetch last page when page size is exactly constant
**Already described under BUG-4 above.**

---

## GUI / UX ISSUES

### UX-1 — Find toolbar is commented out but the feature methods exist
**File:** `video_standardizer.py` (lines 1142–1162 commented out)  
The entire Find toolbar UI block is commented out while all the underlying `_find_*` methods are fully implemented. This is dead UI real estate — users have no way to access these features. Either un-comment the toolbar or remove the dead methods.

---

### UX-2 — No visual feedback that TVDB changes are pending for a file
After applying TVDB changes from the popup, the file listbox gives no indication that a file has TVDB overrides queued. The only evidence is in the output text log. A subtle indicator (e.g. a `[T]` tag or a different text color in the file list) would make it obvious which files have pending changes and prevent accidentally running without them if the user loses track.

---

### UX-3 — "Clear Selection" button clears listbox highlight but doesn't reset TVDB badges
Related to UX-2: if an indicator is added for TVDB changes (UX-2), a separate "Clear TVDB changes" button or tooltip note would be needed. Currently nothing clears queued changes short of restarting.

---

### UX-4 — "Rename Only" and "Keep Original Filename" can be checked simultaneously with no conflict warning
**File:** `video_standardizer.py` (options panel)  
Checking both `rename_var` (Rename Only) and `norename_var` (Keep Original Filename) is logically contradictory — "rename only but also don't rename." The process logic handles `norename` first (returns early with the original name + new extension), so `rename` is silently ignored. The GUI should either:
- Mutually exclude the two checkboxes (checking one unchecks the other), or  
- Show a tooltip/warning noting the conflict.

---

### UX-5 — "Delete Original After Re-process" does not warn when no output directory is set
When `output_dir` is `None` (same directory), `process_file` writes the output to the same folder, then the worker deletes the original. If the output filename equals the input filename (e.g. file is already properly named, remux writes to same path), `out_file != f` check prevents self-deletion — but only for that case. If the original was renamed during remux and both live in the same folder, the original is deleted without any extra confirmation beyond the checkbox. A confirmation dialog at run-start (when delete_original is checked) would reduce accidental data loss.

---

### UX-6 — Progress bars don't reset to 0 between files during a batch run
**File:** `video_standardizer.py` — worker loop  
The file progress bar starts updating from wherever the previous file left it (100%) until the first `progress_fn(pct)` call from ffmpeg arrives. For small/fast files, this means the bar briefly shows 100% from the prior file. A `progress_fn(0)` call at the start of each iteration (before `process_file`) would give a cleaner visual.

---

### UX-7 — TVDB popup series combo only reflects the first file's matches
**File:** `tvdb_lookup/tvdb_lookup.py` — `_populate_series_combo()`  
The "Series:" combobox at the top of the TVDB popup is populated with matches from `row_data_list[0]` (the first file). When the user changes series, `_recompute_series_for_row` is called for *all* rows using that same series index. This is fine for a single-show batch, but for a mixed batch (e.g. two different TV shows in the same folder), all files get re-looked-up against the first file's series matches. There is no per-row series picker for TV mode (unlike the per-row movie picker in movie mode). A per-row series picker, similar to the movie-mode picker, would handle mixed batches correctly.

---

### UX-8 — `Keep Episode Suffix` checkbox has no tooltip explaining what it does
The checkbox label is terse. New users won't know that "suffix" means the episode title portion extracted from the filename (as opposed to the TVDB title). A tooltip (`_attach_tooltip`) with a one-sentence explanation would help.

---

### UX-9 — Output directory entry has no drag-and-drop support
The input path entry accepts drag-and-drop (registered as a DND target), but the Output Directory entry does not. Dragging a folder onto the output field does nothing. Registering the output entry as a drop target (accepting folders only) would be consistent.

---

### UX-10 — Batch complete summary omits "dry run" count when dry_run is False
**File:** `video_standardizer.py` — worker summary block  
The summary at the end of a batch prints `Dry Run: N` only when `dry_run` is True. This is correct behavior, but if some files returned `status='dry_run'` for another reason (shouldn't happen under current logic, but defensively), they'd be silently omitted. Low priority, just an observation.

---

### UX-11 — TVDB popup "Re-Lookup" button discards all user-made series/movie selections
Clicking "Re-Lookup" calls `_lookup_all()` which calls `tree.delete(*)`, clears `row_data_list`, and re-fetches everything from scratch. Any per-row series or movie index choices the user made are lost. A smarter re-lookup would preserve `selected_series_idx` / `selected_movie_idx` per row and restore them after the fresh fetch.

---

### UX-12 — No keyboard shortcut for "Show Plan" or "Run"
The two most common actions ("Show Plan" and "Run") have no keyboard accelerators. Adding `<Return>` to trigger "Show Plan" when the file list has focus, and `Ctrl+R` for "Run", would improve keyboard-driven workflows.

---

## CODE QUALITY / MINOR OVERSIGHTS

### CODE-1 — `_done_pat` regex compiled fresh in four separate places
`re.compile(r'\[\w+ [\d.]+Mbps \w+\]\.\w+$')` appears identically in `_on_path_change`, `_on_drop`, `_populate_from_results`, and `_recycle_files`. Should be a class constant.

---

### CODE-2 — `extract_episode_info` uses `\d{1,2}` for episode numbers (max 99)
**File:** `video_standardizer.py` — `extract_episode_info()`  
`parse_filename` in `tvdb_lookup.py` already uses `\d{1,3}` (up to 999 episodes). `extract_episode_info` in the main file caps at `\d{1,2}` (99 episodes). Shows with 100+ episodes aired order would silently produce wrong tags. The fix is a one-character change: `{1,2}` → `{1,3}` in all four patterns.

---

### CODE-4 — `probe_optional_features` imports `vapoursynth` at module load time
If VapourSynth is installed but has a broken plugin, the probe call raises an unhandled exception from a third-party library during module import. The `except Exception` handler catches it, but an exception from a broken install of an optional dependency can produce confusing startup errors in some environments. Low priority.

---

## Additional User Added Issues
1: Checkboxes ugly and unclear with bad defaults
2: Find Toolbar doesn't integrate well with something like drag and drop. Should be able to drag and drop a folder, and then filter. Should be able to re-filter with a different filter and chain filters
3: Drag and drop needs an actual drop zone.
4: Add Frame Dedupe as tool
5: We functionally have checkboxes and the row below the file dialog that do similar things. UX should be merged and simplified
6: Rich functionality doesn't work in built-in terminal.
7: Should have option to not include new suffix
