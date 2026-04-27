# Video Standardizer

A suite of Python tools for maintaining a video library on Windows. The centerpiece is **Video Standardizer** — a GUI/CLI utility that remuxes files into a consistent container, selects audio/subtitle tracks by language, normalizes metadata, renames files with structured episode tags, and appends resolution/encoding labels. Everything is a stream copy; audio and video are never re-encoded.

The repository also ships companion tools for **commercial removal** (Comskip + VideoReDo smart-render), **Plex thumbnail generation**, **TVDB metadata lookup**, and a collection of **library maintenance scripts** for finding, scanning, and cleaning media.

## Contents

- [Feature overview](#feature-overview)
- [Setup](#setup)
- [Video Standardizer](#video-standardizer-1) (root project)
  - [GUI](#gui)
  - [CLI](#cli)
  - [Options](#options)
  - [How it works](#how-it-works)
- Subprojects:
  - [`tvdb_lookup/`](#tvdb-lookup) — TVDB metadata enrichment
  - [`commercial_skip/`](#commercial-skip-comskip--videoredo) — Comskip + VideoReDo ad removal
  - [`plex_integration/`](#plex-integration) — Plex episode thumbnails
  - [`frame_dedupe/`](#frame-dedupe-telecine-restore) — 30→24fps telecine restore
- [Library Maintenance Scripts](#library-maintenance-scripts)
- [License](#license)

## Feature overview

| Tool | Entry point | GUI | CLI |
|------|-------------|-----|-----|
| Video Standardizer (remux + rename + tag) | [`video_standardizer.py`](video_standardizer.py) / [`video_standardizer.pyw`](video_standardizer.pyw) | ✅ | ✅ |
| TVDB metadata enrichment (year / SxxExx / title) | [`tvdb_lookup/tvdb_lookup.py`](tvdb_lookup/tvdb_lookup.py) | ✅ popup | via standardizer |
| Commercial detection + smart-render removal | [`commercial_skip/batch_comskip.py`](commercial_skip/batch_comskip.py) | ✅ as a Run option | ✅ |
| Plex episode thumbnails from frame at N% | [`plex_integration/plex_episode_thumbs.py`](plex_integration/plex_episode_thumbs.py) | ✅ menu → popup | ✅ |
| Reverse 30→24fps telecine (unblend or dedupe) | [`frame_dedupe/restore.py`](frame_dedupe/restore.py) | — | ✅ |
| Find files by extension | [`scripts/find_by_ext.py`](scripts/find_by_ext.py) | ✅ Find toolbar | ✅ |
| Find files by filename substring | [`scripts/find_by_name.py`](scripts/find_by_name.py) | ✅ Find toolbar | ✅ |
| Find malformed filenames (no `SxxExx` or no extension) | [`scripts/find_malformed.py`](scripts/find_malformed.py) | ✅ Find toolbar | ✅ |
| Scan for corrupt files | [`scripts/check_corrupt.py`](scripts/check_corrupt.py) | ✅ Find toolbar | ✅ |
| Scan for low-resolution files | [`scripts/check_resolution.py`](scripts/check_resolution.py) | ✅ Find toolbar | ✅ |
| Scan metadata for key/value matches | [`scripts/check_metadata.py`](scripts/check_metadata.py) | ✅ Find toolbar | ✅ |
| Recycle selected files | `send2trash` | ✅ Recycle button (with confirm) | via `--recycle` on find scripts |
| Remove empty directories | [`scripts/remove_empty_dirs.py`](scripts/remove_empty_dirs.py) | — | ✅ |

## Setup

### Core requirements

- **Python 3.8+**
- **[FFmpeg](https://ffmpeg.org/download.html)** — both `ffmpeg` and `ffprobe` on your `PATH`.

### Python dependencies

```
pip install tkinterdnd2 rich pywin32 send2trash numpy vapoursynth
```

The full list (`tkinterdnd2`, `rich`, `pywin32`, `send2trash`, `numpy`, `vapoursynth`) covers every subproject — drag-and-drop in the GUI, the Comskip Rich dashboard, the VideoReDo COM driver, recycle-bin deletes, and the cadence detector / pipeline in [frame_dedupe](frame_dedupe/). After this one command you can run any script in the repo directly.

### Per-subproject native dependencies

Each subproject has its own optional native deps. Missing items don't break the rest of the project — controls in the GUI that depend on them are automatically grayed out at startup, with a tooltip explaining why.

| Subproject | Native dependency | Notes |
|------------|-------------------|-------|
| [Video Standardizer](#video-standardizer) (root) | ffmpeg, ffprobe | Already covered above; the rest of the repo also depends on these. |
| [`frame_dedupe/`](frame_dedupe/) | [VapourSynth](https://www.vapoursynth.com/) R65+ with the **lsmas** plugin | Install the official Windows installer (ships with `vsrepo`), then `vsrepo.py install lsmas`. The repo's local `.venv\Scripts\vspipe.exe` is used automatically when present; otherwise `vspipe` is expected on `PATH`. |
| [`commercial_skip/`](commercial_skip/) | [Comskip](https://www.comskip.org/) executable + [VideoReDo TVSuite v6](https://www.videoredo.com/) | Comskip lives in [comskip_dst/](comskip_dst/) at the repo root (`comskip.exe` + `comskip.ini`). VideoReDo is reached via its `VideoReDo6.VideoReDoSilent` COM interface (Windows only). |
| [`plex_integration/`](plex_integration/) | Plex Media Server (local) | The script must run on a machine that can read the library files directly. |
| [`tvdb_lookup/`](tvdb_lookup/) | TVDB v4 API key | Free at [thetvdb.com](https://thetvdb.com) → **API Access**. Stored in `config.env` (see below). |

### Configuration (`config.env`)

All tools that need credentials read them from a single `config.env` file in the repo root. Create it by hand:

```
apikey=your_tvdb_api_key_here
plex_token=your_plex_token_here
plex_url=http://127.0.0.1:32400
```

| Key | Used by | Notes |
|-----|---------|-------|
| `apikey` | [`tvdb_lookup/`](tvdb_lookup/) (TVDB popup) | Free key from [thetvdb.com](https://thetvdb.com) under **API Access**. Responses are cached locally in `tvdb_cache.json` at the repo root. |
| `plex_token` | [`plex_integration/`](plex_integration/) | [How to find yours.](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) Can also be passed as `--token`. |
| `plex_url` | [`plex_integration/`](plex_integration/) | Optional; defaults to `http://127.0.0.1:32400`. |

Each tool runs fine without the keys it doesn't need.

## Video Standardizer

### GUI

Double-click [`video_standardizer.pyw`](video_standardizer.pyw) to open the GUI without a console window, or run `python video_standardizer.py` with no arguments. The input path defaults to the last folder you processed.

Layout:

- **Menu bar** — `Tools → Plex Thumbnails…` opens the [Plex thumbnail popup](#plex-episode-thumbnails).
- **Left panel** — mode (folder / single file), input path (drag-and-drop supported), optional output directory, container preference, audio language filter, and per-run options.
- **Right panel (top)** — file list with live status icons:
  - `⟳` running · `✓` done · `→` renamed · `—` skipped · `✗` failed
  - **Find toolbar** (above the list): scan the current input folder and *replace* the list with the matches. See [Find toolbar](#find-toolbar) below.
  - **Show Plan** — preview exactly what will change per file.
  - **TVDB Lookup** — open the enrichment popup (see below).
  - **Clear Selection** — deselect everything.
  - **🗑 Recycle…** — send the selected files (or all listed files if none selected) to the Recycle Bin after a confirmation prompt. Mirrors `--recycle` on the find scripts. Requires `send2trash`.
- **Right panel (bottom)** — per-file and batch progress bars, plus a scrolling output log with colour-coded plan diffs.
- **Stop** — terminates the active `ffmpeg` process immediately.
- A **batch summary** (remuxed / renamed / skipped / failed) is printed after every run.

#### Find toolbar

The row of buttons above the file list runs a scan and replaces the list with the matches. The scan root is the current Input path (its parent folder if Input points to a file). Use it to quickly assemble a working set before running the standardizer or recycling.

| Button | Action |
|--------|--------|
| **By Ext…** | Prompts for an extension (e.g. `.ts`) and lists every video under the scan root with that extension. |
| **By Name…** | Prompts for a filename substring (case-insensitive) and lists matching videos. |
| **Malformed** | Lists videos missing an `SxxExx` tag and files with no extension. |
| **Low-res…** | Prompts for a minimum vertical resolution and lists files below it (shells out to [`check_resolution.py`](scripts/check_resolution.py)). |
| **Corrupt…** | Samples each file under the scan root with `ffmpeg` and lists those that fail to decode (shells out to [`check_corrupt.py`](scripts/check_corrupt.py)). |
| **Metadata…** | Prompts for a substring and lists files whose `ffprobe` metadata contains it (shells out to [`check_metadata.py`](scripts/check_metadata.py)). |

Scans run in a background thread; the output log streams progress. While a scan is in progress, the Find buttons and Run are disabled.

### CLI

Process every video in a folder:

```
python video_standardizer.py -f "C:\Videos\Show Season 1"
```

Process a single file:

```
python video_standardizer.py -i "S01E01 - Pilot.mkv"
```

Run with no arguments to launch the GUI.

### Options

CLI and GUI expose equivalent settings. Flags marked GUI-only are surfaced as checkboxes in the main window.

| CLI flag | GUI equivalent | Description |
|----------|---------------|-------------|
| `-f PATH` / `--folder` | Mode: Folder + Input | Folder of video files to process |
| `-i FILE` / `--input` | Mode: Single File + Input | Process one file |
| `-e EXT` / `--extension` | **Container** (MKV / MP4) | Preferred output container. Auto-adjusted if a subtitle codec demands otherwise. |
| `--prefer-only` | **Prefer only** checkbox | Treat `--extension` as a *preference*: don't remux a file that's already in the other supported container (MKV ↔ MP4). Other containers still get remuxed. |
| `-l LANG …` / `--languages` | **Keep Audio Languages** | Space-separated ISO 639-2 codes (default: `eng jpn`). Best track per language is kept; others dropped. |
| `-r` / `--rename` | **Rename Only** | Rename without remuxing |
| `-n` / `--norename` | **Keep Original Filename** | Keep the filename as-is; only change container/tracks |
| — | **Keep Episode Suffix** | Preserve the existing episode title already in the filename |
| `-c` / `--convert-force` | **Force Re-process** | Re-process files that already have a `[HD …]` tag |
| `-o DIR` / `--output` | **Output Directory** | Write outputs to a different folder |
| `-d` / `--dry-run` | **Dry Run** | Preview only — no files are written |
| `-v` / `--verbose` | **Verbose Output** | Print the `ffmpeg` command and live progress stats |
| — | **Delete Original After Re-process** | Remove the source file after a successful remux |
| — | **Remove Ads First (Comskip + VideoReDo)** | For each file, run [Commercial Removal](#commercial-skip-comskip--videoredo) first and feed the cut result into the standardizer. The intermediate `_no_ads.mkv` is deleted after the standardizer writes its final output. Disabled during dry runs. Auto-grayed out if Comskip or VideoReDo is missing. The cut output is rejected and the original is kept whenever it falls below a minimum duration (default 20 min) or shrinks to under 40% of the source size — both are signs that Comskip misidentified a chunk of the show as commercials. Clicking **Stop** mid-run force-kills the running Comskip and VideoReDo processes and ends the batch. |

### How it works

1. **Probe** — `ffprobe` reads all stream metadata in a single call.
2. **Audio selection** — for each language in `--languages`, the track with the most channels is kept. Tracks tagged `und`/`unk` are reassigned to English if no English track exists. If filtering would eliminate all audio tracks, all are kept instead.
3. **Subtitle selection** — English and undefined subtitle tracks are kept; embedded EIA-608 captions are dropped in favour of proper subtitle streams.
4. **Container selection** — selected subtitle codecs are checked against the target container's compatibility list. If any would require re-encoding, the container is switched automatically.
5. **Filename building** — episode tags are normalised (`S01E01E02`, `S01E01-02`, `S01E01-E02`), TVDB changes are applied (if any), illegal characters are sanitized, and a metadata suffix is appended (`[HD 8Mbps HEVC]`).
6. **Rename or remux** — if the selected tracks already match the input and the container is unchanged, the file is simply renamed. Otherwise `ffmpeg` remuxes with stream copy. Chapters and global metadata are preserved (`-map_chapters 0 -map_metadata 0`).
7. **Error logging** — on `ffmpeg` failure, the filename, command, and stderr are appended to `conversion_errors.log`.

MKV supports a broad range of subtitle formats (SRT, ASS, PGS, VobSub, DVB, WebVTT, …). MP4 only natively carries text-based formats (`mov_text`, TTML). The container is picked automatically based on what the file actually contains.

## TVDB Lookup

The [`tvdb_lookup/`](tvdb_lookup/) subproject queries TheTVDB to enrich filenames. The **TVDB Lookup** button in the main window opens a popup that queries [TheTVDB](https://thetvdb.com) for all selected files at once. Three enrichment modes, independently toggleable:

- **Year** — inserts the series premiere year as `(YYYY)` before the `[HD …]` tag.
- **Episode ID** — fuzzy-matches the filename's episode title against TVDB to derive the correct `SxxExx`.
- **Episode Title** — for files that already have an `SxxExx` tag, applies the canonical TVDB episode title.

TVDB supports multiple ordering schemes per series (Aired, DVD, Absolute, …). A dropdown lets you switch orderings and see how episode assignments change before applying. Confidence scores are colour-coded green/yellow/red in the results table.

**Workflow:** select files → **TVDB Lookup** → pick modes and ordering → deselect unwanted rows → **Apply** → **Show Plan** or **Run**. Applied changes persist for the session.

**Native deps:** `apikey=` in `config.env`. The button is automatically grayed out at startup if the key is missing, with a tooltip explaining what to do.

Tests live alongside the module and run from the repo root:

```
python -m unittest tvdb_lookup.test_tvdb_lookup
```

## Commercial Removal (Comskip + VideoReDo)

The [`commercial_skip/`](commercial_skip/) subproject runs Comskip + VideoReDo to strip ads. The standardizer GUI exposes it as a per-run **Remove Ads First** checkbox (see [GUI Options](#options)) — cut once, standardize once, intermediate deleted automatically. The CLI entry point below is still the way to do bulk parallel ad-removal without running the standardizer afterward.

[`commercial_skip/batch_comskip.py`](commercial_skip/batch_comskip.py) walks a folder (or reads paths from stdin), runs Comskip on each video to detect ad regions, then hands the resulting `.VPrj` to a silent VideoReDo instance which **smart-renders** (no re-encode) the source minus the cut regions. Output is written next to the source as `<stem>_no_ads.mkv`.

A live Rich dashboard shows per-file phase, percent, and elapsed time.

[`commercial_skip/batch_vrd_save.py`](commercial_skip/batch_vrd_save.py) is the shared library: VideoReDo save pipeline, live dashboard, and video discovery helpers. Not a standalone entry point.

**Native deps:** Comskip executable in [`comskip_dst/`](comskip_dst/) at the repo root, VideoReDo TVSuite v6 with its COM interface registered (Windows only).

### Usage

Process every video in a folder:

```
python commercial_skip/batch_comskip.py "C:\Recorded TV"
```

Process a piped list of paths (one per line):

```
python scripts/find_by_ext.py "C:\Recorded TV" --ext .ts | python commercial_skip/batch_comskip.py -
```

Replace originals with the cleaned output (originals go to the recycle bin):

```
python commercial_skip/batch_comskip.py "C:\Recorded TV" --recycle
```

### Options

| Flag | Description |
|------|-------------|
| `directory` / `-` | Root folder to scan, or `-` to read newline-separated paths from stdin |
| `--threads N` | Parallel worker count (default: 10). Each worker runs Comskip and a VideoReDo instance in sequence. |
| `--recycle` | Send originals to the recycle bin and rename the `_no_ads` output into their place |
| `--comskip PATH` | Path to `comskip.exe` (default: `comskip_dst/comskip.exe`) |
| `--comskip-ini PATH` | Path to `comskip.ini` (default: `comskip_dst/comskip.ini`) |
| `--start-at N` | Resume from the Nth file (1-indexed) — useful after a crash |

Behaviour:

- Files already ending in `_no_ads` are skipped.
- Zero-cut files are reported "No ads" and left alone — not counted as errors.
- Comskip sidecars (`.VPrj`, `.edl`, `.log`, `.logo.txt`, `.txt`) are cleaned up after each file.
- Ctrl+C signals a graceful stop: active workers finish their current file; no new files start.
- **Sanity guards** (GUI only, today) reject cut outputs that look obviously wrong: shorter than 20 minutes, or less than 40% of the source file size. When a guard fires, the `_no_ads.mkv` is deleted and the original is kept.

## Plex Integration

The [`plex_integration/`](plex_integration/) subproject covers Plex-server-facing features.

### Episode Thumbnails

[`plex_integration/plex_episode_thumbs.py`](plex_integration/plex_episode_thumbs.py) grabs a frame from N% into every episode of a given show/season and uploads it as the Plex thumbnail. Useful when Plex has generated poor auto-thumbs (e.g. a title card or black frame).

From the GUI: **Tools → Plex Thumbnails…** opens a popup that queries your Plex server, populates a show dropdown and a season dropdown, and runs the same apply logic as the CLI. Results stream into the popup's log panel. Cancelable mid-run.

```
python plex_integration/plex_episode_thumbs.py "Firefly" 1
python plex_integration/plex_episode_thumbs.py "Arthur" --all --percent 15
python plex_integration/plex_episode_thumbs.py "Firefly" 1 --dry-run
```

| Argument / flag | Description |
|-----------------|-------------|
| `show` | Show title as it appears in your Plex library |
| `season` | Season number (omit when using `--all`) |
| `--all` | Process every season |
| `--percent` | Frame position as % of runtime (default: 10) |
| `--token` | Plex token (defaults to `plex_token` in `config.env`) |
| `--dry-run` | Print the plan without touching anything |

Requires `plex_token=` in `config.env`. Uses system `ffmpeg` to extract frames and posts the JPEG to `/library/metadata/<ratingKey>/posters`.

**Frame quality safeguards:**
- **Black-frame avoidance.** The chosen timestamp is probed with `blackdetect` over a short window. If it lands inside a black region (opening title, scene transition, post-ad fade), the script steps forward 5 seconds and re-probes, up to 5 attempts, until it finds a non-black frame.
- **Letterbox auto-crop.** `cropdetect` runs on the same probe window; if it reports a sub-frame region (e.g. 4:3 content inside a 16:9 source), the final extract applies a matching `crop` filter so the thumbnail is just the picture, not the bars.

## Frame Dedupe (telecine restore)

[`frame_dedupe/restore.py`](frame_dedupe/restore.py) reverses two specific 30→24fps telecine patterns found on poorly-converted DVD rips and broadcast captures. Both patterns are common when 24fps film is converted to 30fps for NTSC distribution. Each output is at 23.976fps with source audio passed through.

Two modes, picked via `--mode`:

### Mode 1: `unblend` — 5:4 weighted-blend pulldown

The source has 3 clean frames followed by 2 interlaced frames per cadence group of 5:

```
s[0]: F_a clean       (passthrough)
s[1]: F_b clean       (passthrough)
s[2]: F_c clean       (passthrough)
s[3]: 0.5*F_c + 0.5*F_d  (interlaced)
s[4]: 0.5*F_d + 0.5*F_e  (interlaced; F_e is the next group's F_a)
```

Standard inverse-telecine tools (`fieldmatch`, ffmpeg `decimate`) and motion-compensated deinterlacers (QTGMC) both fail on this pattern because the interlaced frames don't have clean integer-row field boundaries — they were resized vertically *after* the interlacing was applied. The fix is purely algebraic: `F_d = b1 + b2 − 0.5*F_c − 0.5*F_e`. Output: `[F_a, F_b, F_c, F_d_recovered]` per group. Three of the four output frames are bit-exact source pixels; the recovered F_d retains horizontal comb stripes from the staggered field sampling.

**Use case.** Preparing degraded sources for AI upscalers like **Topaz Video AI / Proteus**. The retained comb stripes are intentional — AI upscalers produce sharper output when fed slightly-flawed-but-crisp frames than when fed pre-deinterlaced softened frames. Let the upscaler's deinterlace settings clean up residual combs as part of the upscale pass.

### Mode 2: `dedupe` — duplicate-frame pulldown

The source has 4 unique frames plus 1 duplicate per group of 5 (no blending):

```
Phase 0: [A, A, B, C, D]  → keep [A, B, C, D]
Phase 1: [A, B, B, C, D]  → keep [A, B, C, D]
Phase 2: [A, B, C, C, D]  → keep [A, B, C, D]
Phase 3: [A, B, C, D, D]  → keep [A, B, C, D]
```

The fix is a simple SelectEvery that drops the second frame of each duplicate pair. Because compressed sources aren't pixel-perfect, duplicates are detected by the adjacent-pair position with consistently lowest mean-absolute-difference (MAD).

### Auto-detected per-segment phase

Scene cuts in edited sources reset the cadence to a different phase. By default, the tool auto-detects per-segment phase by sampling overlapping 50-frame windows across the entire file (using a parallel process pool). Each segment is then transformed with its own phase. For unedited sources with truly uniform cadence — rare in practice — pass `--phase N` to skip detection.

### Usage

Auto-detect and render to ProRes 422 next to the source:

```
python frame_dedupe/restore.py -i "movie.mp4" --mode unblend
python frame_dedupe/restore.py -i "movie.mp4" --mode dedupe
```

Quick H.264 preview at a specific timestamp:

```
python frame_dedupe/restore.py -i "movie.mp4" --mode unblend \
    --codec h264 --start 12:30 --duration 5:00
```

Force a single phase across the whole file (uniform cadence only):

```
python frame_dedupe/restore.py -i "movie.mp4" --mode dedupe --phase 3
```

### Options

| Flag | Description |
|------|-------------|
| `-i FILE` / `--input` | Source video file (required). |
| `--mode {unblend,dedupe}` | Cadence model to reverse (required). See descriptions above. |
| `-o FILE` / `--output` | Output file. Default: `<input_stem> [<mode>]<ext>` next to source (`.mov` for ProRes, `.mp4` for H.264). |
| `--phase {0..4}` | Force a single global phase (skips auto-detect). Use only when source cadence is uniform. |
| `--start TC` | Start time (`HH:MM:SS` / `MM:SS` / seconds). Default: file start. |
| `--duration TC` | Duration (`HH:MM:SS` / `MM:SS` / seconds). Default: rest of file. |
| `--codec {prores,prores_hq,h264}` | `prores` = ProRes 422 (default; recommended for AI upscaler input); `prores_hq` = higher bitrate ProRes 422 HQ; `h264` = libx264 CRF 18 for test renders. |
| `--threads N` | Parallel worker processes for detection. Default `cpu_count - 8`. Each worker opens its own decoder, so the speedup is real. |
| `--dry-run` | Print segments and the generated `.vpy` without rendering. |

Source audio is always passed through (with the trim offset applied if `--start` / `--duration` are set).

### Two stages of progress

1. `Detecting unblend cadence: frames [0, 175040), window=50, stride=25, threads=24` followed by per-worker progress — the detection pass (typically a few minutes for a 2-hour movie at default settings).
2. `Rendering 140032 frames (5840s @ 23.976fps)...` followed by per-percent render progress — the actual encode (much faster than detection).

### File size estimates (480p, full 2-hour movie)

| Codec | Size |
|-------|------|
| `h264` (CRF 18) | ~1–2 GB |
| `prores` (422 std) | ~25 GB |
| `prores_hq` (422 HQ) | ~40 GB |

### Standalone detector

[`frame_dedupe/detect.py`](frame_dedupe/detect.py) can be invoked directly (`--mode unblend\|dedupe`) to emit just the segments JSON without rendering. The renderer wraps this internally.

### Caveats

- **Phase calibration on auto-detect:** detection requires high-motion source content to produce reliable signal. Sources that are mostly static (a long take of a still scene) will fall back to the nearest neighbour's phase via the run-length filter. If output looks wrong on a specific scene, render that segment with `--phase` forced and compare.
- **Boundary loss:** the trailing partial cadence cycle of each segment is dropped (up to ~4 source frames per phase change). With ~50 segments per 2-hour movie this is well under 1 second of total content loss, but it does mean `output_duration < input_duration` by a small amount.

## Library Maintenance Scripts

Small utilities under [scripts/](scripts/) for housekeeping. Each one prints file paths to **stdout** and status/progress to **stderr**, so outputs pipe cleanly into other tools (e.g. `batch_comskip.py -`).

### `find_by_ext.py` — files by extension

```
python scripts/find_by_ext.py "D:\DVR" --ext .ts
```

| Flag | Description |
|------|-------------|
| `directory` | Root to scan |
| `--ext` | Extension to match (default: `.mpg`) |

### `find_by_name.py` — files by filename substring

```
python scripts/find_by_name.py "D:\DVR" --search MPEG2VIDEO
```

| Flag | Description |
|------|-------------|
| `directory` | Root to scan |
| `--search` | Case-insensitive substring to match in the filename (default: `MPEG2VIDEO`) |
| `--all-files` | Search all file types, not just known video extensions |

### `find_malformed.py` — files missing `SxxExx` or extension

```
python scripts/find_malformed.py "D:\TV" | python video_standardizer.py -
```

| Flag | Description |
|------|-------------|
| `directory` | Root to scan |
| `--all-files` | Consider all file types (files without any extension are always reported) |

### `check_corrupt.py` — decode-scan for broken files

Samples random seconds from each file with `ffmpeg -c copy -f null` and flags any that fail to decode.

```
python scripts/check_corrupt.py "D:\TV" --samples 15 --threshold 50
```

| Flag | Description |
|------|-------------|
| `directory` | Root to scan |
| `--ext` | Only check files with this extension |
| `--name` | Only check files with this substring in the filename |
| `--workers N` | Parallel decode jobs (default: min(8, CPUs)) |
| `--samples N` | Random sample points per file (default: 15) |
| `--sample-seconds` | Duration of each sample (default: 20.0) |
| `--threshold N` | Max acceptable decode errors per file (default: 50) |
| `--full` | Decode the entire file instead of sampling |
| `--timeout` | Per-file timeout in seconds (default: 120) |
| `--recycle` | Send flagged files to the recycle bin |
| `-v` / `--verbose` | Print ffmpeg stderr for matches |

### `check_resolution.py` — find low-resolution files

```
python scripts/check_resolution.py "D:\TV" --min-height 480
```

| Flag | Description |
|------|-------------|
| `directory` | Root to scan |
| `--min-height` | Files below this vertical resolution are flagged (default: 360) |
| `--workers N` | Parallel probes (default: min(8, CPUs)) |
| `--recycle` | Send flagged files to the recycle bin |

### `check_metadata.py` — find files with matching metadata

Uses `ffprobe` to match files by a metadata key/value pair.

```
python scripts/check_metadata.py "D:\TV" --key encoder --value Hulu
python scripts/check_metadata.py "D:\TV" --value Hulu      # search any key/value
```

| Flag | Description |
|------|-------------|
| `directory` | Root to scan |
| `--key` | Metadata key to check (if omitted, matches any key or value) |
| `--value` | Substring to match (default: `Hulu`) |
| `--workers N` | Parallel probes (default: min(8, CPUs)) |
| `--recycle` | Send flagged files to the recycle bin |

### `remove_empty_dirs.py` — clean up empty folders

```
python scripts/remove_empty_dirs.py "D:\TV"
```

Recursively sends empty subdirectories to the recycle bin.

## License

MIT
