# Possible Future Features

A running list of potential improvements and additions. Not prioritized — just ideas to consider.

---

## Processing & Conversion

- **Video re-encoding option** — optionally transcode video to H.265/HEVC or AV1 when the source is an inefficient codec (e.g. MPEG-2, old XviD). Could be gated behind a quality/CRF setting and only triggered when the source bitrate is unusually high for its resolution.

- **Audio normalization** — run loudnorm or dynaudnorm via ffmpeg on audio tracks to bring inconsistent volumes to a standard level. Useful for library-wide consistency.

- **Surround → stereo downmix track** — when keeping a 5.1/7.1 track, optionally add a stereo downmix as a second audio track for compatibility with devices that don't handle surround.

- **Chapter preservation check** — warn or report when source files contain chapters that are being dropped (some containers strip chapters on remux).

- **Attachment stripping** — MKV files sometimes carry embedded fonts or cover art as attachments that bloat file size. Option to strip these.

- **Multiple subtitle language support** — mirror the audio language logic for subtitles: let the user specify a list of subtitle languages to keep rather than only English/undefined.

- **Forced subtitle detection** — identify and flag "forced" subtitle tracks (typically signs/songs in foreign-language content) and keep them regardless of language.

---

## Filename & Metadata

- **Series name extraction** — attempt to extract the series name from the filename or parent folder and include it in the output name (e.g. `Show Name S01E01 [HD 8Mbps HEVC].mkv`).

- **Movie naming mode** — for non-episode files, attempt to detect year and normalize to `Title (Year) [4K 25Mbps HEVC].mkv` format.

- **Rename preview/confirmation step** — before renaming a batch, show the full before/after list and ask for confirmation. Useful when processing a large folder.

- **Undo log** — write a reverse-rename log so that a batch rename can be rolled back without manually tracking filenames.

- **Embedded title metadata** — write the cleaned episode/movie title into the container's `title` metadata tag via ffmpeg `-metadata title=...`.

---

## GUI Improvements

- **Progress bar** — ✓ *Implemented* (per-file % via ffmpeg `-progress pipe:1` + overall batch count bar).

- **File list preview** — before running, display the list of files that will be processed with their detected episode tags and expected output names.

- **Drag-and-drop input** — ✓ *Implemented* (requires `pip install tkinterdnd2`; degrades gracefully without it).

- **Dark/light theme toggle** — the output panel is already dark; a proper theme toggle would make the whole window consistent.

- **Settings persistence** — save all option states (container, languages, checkboxes) to config so they survive between launches.

- **Open output folder button** — after processing completes, show a button to open the output directory in Explorer.

- **Per-file status indicators** — ✓ *Implemented* (⟳ running / ✓ done / → renamed / — skipped / ✗ failed).

---

## Batch & Workflow

- **Recursive folder mode** — optionally descend into subdirectories, useful for processing an entire series at once.

- **Watch folder mode** — monitor a folder and automatically process new files as they appear. Useful as a post-download hook.

- **Queue file** — accept a plain text file listing paths to process, one per line.

- **Parallel processing** — process multiple files concurrently using a worker pool (bounded by CPU count). Would significantly speed up large batches where ffmpeg is I/O bound.

- **Skip list** — maintain a record of already-processed files so re-running on a folder skips them without relying purely on the filename pattern check.

---

## Reporting & Diagnostics

- **Summary report** — ✓ *Implemented* (shown after each batch: remuxed / renamed / skipped / failed counts).

- **Error log viewer in GUI** — add a tab or button to view `conversion_errors.log` directly inside the app.

- **Stream info display** — ✓ *Implemented* (double-click or "Stream Info" button in the file list shows a popup with codec, channels, language, duration, and bitrate).

- **Dry-run diff view** — ✓ *Implemented* (plan/diff shown before every file in all modes; per-file "Show Plan" button also available).

---

## Integration

- **Context menu integration (Windows)** — register the script as a Windows Shell right-click action on folders ("Standardize with Video Standardizer").

- **Sonarr/Radarr post-processing script** — provide a thin wrapper that makes this compatible with Sonarr/Radarr's custom post-processing script interface.

- **Config profiles** — named profiles (e.g. "Anime", "Movies", "Archive") with preset language lists, container choice, and rename style that can be switched quickly in the GUI.
