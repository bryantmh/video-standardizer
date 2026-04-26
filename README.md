# Video Standardizer

A suite of Python tools for maintaining a video library on Windows. The centerpiece is **Video Standardizer** — a GUI/CLI utility that remuxes files into a consistent container, selects audio/subtitle tracks by language, normalizes metadata, renames files with structured episode tags, and appends resolution/encoding labels. Everything is a stream copy; audio and video are never re-encoded.

The repository also ships companion tools for **commercial removal** (Comskip + VideoReDo smart-render), **Plex thumbnail generation**, **TVDB metadata lookup**, and a collection of **library maintenance scripts** for finding, scanning, and cleaning media.

## Contents

- [Feature overview](#feature-overview)
- [Requirements](#requirements)
- [Configuration (`config.env`)](#configuration-configenv)
- [Video Standardizer](#video-standardizer-1)
  - [GUI](#gui)
  - [CLI](#cli)
  - [Options](#options)
  - [How it works](#how-it-works)
- [TVDB Lookup](#tvdb-lookup)
- [Commercial Removal](#commercial-removal-comskip--videoredo)
- [Plex Episode Thumbnails](#plex-episode-thumbnails)
- [Unblend Telecine](#unblend-telecine)
- [Library Maintenance Scripts](#library-maintenance-scripts)
- [License](#license)

## Feature overview

| Tool | Entry point | GUI | CLI |
|------|-------------|-----|-----|
| Video Standardizer (remux + rename + tag) | [`video_standardizer.py`](video_standardizer.py) / [`video_standardizer.pyw`](video_standardizer.pyw) | ✅ | ✅ |
| TVDB metadata enrichment (year / SxxExx / title) | [`tvdb_lookup.py`](tvdb_lookup.py) | ✅ popup | via standardizer |
| Commercial detection + smart-render removal | [`batch_comskip.py`](batch_comskip.py) | ✅ as a Run option | ✅ |
| Plex episode thumbnails from frame at N% | [`plex_episode_thumbs.py`](plex_episode_thumbs.py) | ✅ menu → popup | ✅ |
| Reverse 5:4 weighted-blend pulldown (30→24fps) | [`unblend_telecine.py`](unblend_telecine.py) | — | ✅ |
| Find files by extension | [`scripts/find_by_ext.py`](scripts/find_by_ext.py) | ✅ Find toolbar | ✅ |
| Find files by filename substring | [`scripts/find_by_name.py`](scripts/find_by_name.py) | ✅ Find toolbar | ✅ |
| Find malformed filenames (no `SxxExx` or no extension) | [`scripts/find_malformed.py`](scripts/find_malformed.py) | ✅ Find toolbar | ✅ |
| Scan for corrupt files | [`scripts/check_corrupt.py`](scripts/check_corrupt.py) | ✅ Find toolbar | ✅ |
| Scan for low-resolution files | [`scripts/check_resolution.py`](scripts/check_resolution.py) | ✅ Find toolbar | ✅ |
| Scan metadata for key/value matches | [`scripts/check_metadata.py`](scripts/check_metadata.py) | ✅ Find toolbar | ✅ |
| Recycle selected files | `send2trash` | ✅ Recycle button (with confirm) | via `--recycle` on find scripts |
| Remove empty directories | [`scripts/remove_empty_dirs.py`](scripts/remove_empty_dirs.py) | — | ✅ |

## Requirements

- **Python 3.8+**
- **[FFmpeg](https://ffmpeg.org/download.html)** — both `ffmpeg` and `ffprobe` on your `PATH`. Required by the standardizer, Plex thumbnails, and most scan scripts.
- **[Comskip](https://www.comskip.org/)** — required by `batch_comskip.py`. A `comskip.exe` and `comskip.ini` are expected in [comskip_dst/](comskip_dst/) by default (override with `--comskip` / `--comskip-ini`).
- **[VideoReDo TVSuite v6](https://www.videoredo.com/)** — required by `batch_comskip.py`. The driver talks to its `VideoReDo6.VideoReDoSilent` COM interface (Windows only).
- **Plex Media Server** (local) — required by `plex_episode_thumbs.py`. The script must run on a machine that can read the library files directly.
- **[VapourSynth](https://www.vapoursynth.com/)** R65+ with the **lsmas** plugin — required by `unblend_telecine.py`. Install the official Windows installer (it ships with `vsrepo`), then `vsrepo.py install lsmas`. The repo's local `.venv\Scripts\vspipe.exe` is used automatically when present; otherwise `vspipe` is expected on `PATH`.

### Python dependencies

| Package | Purpose | Required by |
|---------|---------|-------------|
| [`tkinterdnd2`](https://pypi.org/project/tkinterdnd2/) | Drag-and-drop files/folders onto the standardizer GUI input | Standardizer GUI |
| [`rich`](https://pypi.org/project/rich/) | Live batch dashboard | `batch_comskip.py` |
| [`pywin32`](https://pypi.org/project/pywin32/) | Drives the VideoReDo silent COM interface | `batch_comskip.py` |
| [`send2trash`](https://pypi.org/project/Send2Trash/) | Recycle-bin deletes | `batch_comskip.py` (with `--recycle`), `scripts/check_corrupt.py`, `scripts/check_metadata.py`, `scripts/check_resolution.py`, `scripts/remove_empty_dirs.py` |

```
pip install tkinterdnd2 rich pywin32 send2trash
```

No installation step beyond that — clone the repo and run the scripts directly.

## Configuration (`config.env`)

All tools that need credentials read them from a single `config.env` file in the repo root. Create it by hand:

```
apikey=your_tvdb_api_key_here
plex_token=your_plex_token_here
plex_url=http://127.0.0.1:32400
```

| Key | Used by | Notes |
|-----|---------|-------|
| `apikey` | `tvdb_lookup.py` (TVDB popup) | Free key from [thetvdb.com](https://thetvdb.com) under **API Access**. Responses are cached locally in `tvdb_cache.json`. |
| `plex_token` | `plex_episode_thumbs.py` | [How to find yours.](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/) Can also be passed as `--token`. |
| `plex_url` | `plex_episode_thumbs.py` | Optional; defaults to `http://127.0.0.1:32400`. |

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
| — | **Remove Ads First (Comskip + VideoReDo)** | For each file, run [Commercial Removal](#commercial-removal-comskip--videoredo) first and feed the cut result into the standardizer. The intermediate `_no_ads.mkv` is deleted after the standardizer writes its final output. Disabled during dry runs. Requires Comskip, VideoReDo, and `pywin32`. The cut output is rejected and the original is kept whenever it falls below a minimum duration (default 20 min) or shrinks to under 40% of the source size — both are signs that Comskip misidentified a chunk of the show as commercials. Clicking **Stop** mid-run force-kills the running Comskip and VideoReDo processes and ends the batch. |

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

The **TVDB Lookup** button in the main window opens a popup that queries [TheTVDB](https://thetvdb.com) for all selected files at once. Three enrichment modes, independently toggleable:

- **Year** — inserts the series premiere year as `(YYYY)` before the `[HD …]` tag.
- **Episode ID** — fuzzy-matches the filename's episode title against TVDB to derive the correct `SxxExx`.
- **Episode Title** — for files that already have an `SxxExx` tag, applies the canonical TVDB episode title.

TVDB supports multiple ordering schemes per series (Aired, DVD, Absolute, …). A dropdown lets you switch orderings and see how episode assignments change before applying. Confidence scores are colour-coded green/yellow/red in the results table.

**Workflow:** select files → **TVDB Lookup** → pick modes and ordering → deselect unwanted rows → **Apply** → **Show Plan** or **Run**. Applied changes persist for the session.

Requires `apikey=` in `config.env`.

## Commercial Removal (Comskip + VideoReDo)

The standardizer GUI exposes this as a per-run **Remove Ads First** checkbox (see [GUI Options](#options)) — cut once, standardize once, intermediate deleted automatically. The CLI entry point below is still the way to do bulk parallel ad-removal without running the standardizer afterward.

[`batch_comskip.py`](batch_comskip.py) walks a folder (or reads paths from stdin), runs Comskip on each video to detect ad regions, then hands the resulting `.VPrj` to a silent VideoReDo instance which **smart-renders** (no re-encode) the source minus the cut regions. Output is written next to the source as `<stem>_no_ads.mkv`.

A live Rich dashboard shows per-file phase, percent, and elapsed time.

[`batch_vrd_save.py`](batch_vrd_save.py) is the shared library: VideoReDo save pipeline, live dashboard, and video discovery helpers. Not a standalone entry point.

### Usage

Process every video in a folder:

```
python batch_comskip.py "C:\Recorded TV"
```

Process a piped list of paths (one per line):

```
python scripts/find_by_ext.py "C:\Recorded TV" --ext .ts | python batch_comskip.py -
```

Replace originals with the cleaned output (originals go to the recycle bin):

```
python batch_comskip.py "C:\Recorded TV" --recycle
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

## Plex Episode Thumbnails

[`plex_episode_thumbs.py`](plex_episode_thumbs.py) grabs a frame from N% into every episode of a given show/season and uploads it as the Plex thumbnail. Useful when Plex has generated poor auto-thumbs (e.g. a title card or black frame).

From the GUI: **Tools → Plex Thumbnails…** opens a popup that queries your Plex server, populates a show dropdown and a season dropdown, and runs the same apply logic as the CLI. Results stream into the popup's log panel. Cancelable mid-run.

```
python plex_episode_thumbs.py "Firefly" 1
python plex_episode_thumbs.py "Arthur" --all --percent 15
python plex_episode_thumbs.py "Firefly" 1 --dry-run
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

## Unblend Telecine

[`unblend_telecine.py`](unblend_telecine.py) reverses a particular kind of bad NTSC pulldown found on some old DVD rips: a 24fps film source was upconverted to 30fps using a **5:4 weighted-blend pulldown** that produces three clean frames followed by two interlaced frames per cadence group. Standard inverse-telecine tools (`fieldmatch`, ffmpeg `decimate`) and motion-compensated deinterlacers (QTGMC) both fail on this pattern because the interlaced frames don't have clean integer-row field boundaries — they were resized vertically *after* the interlacing was applied.

The fix is purely algebraic. For each 5-frame cadence group:

```
s[0]: F_a clean       (passthrough)
s[1]: F_b clean       (passthrough)
s[2]: F_c clean       (passthrough)
s[3]: 0.5*F_c + 0.5*F_d  (interlaced)
s[4]: 0.5*F_d + 0.5*F_e  (interlaced; F_e is the next group's F_a)
```

The missing F_d is reconstructed as `F_d = b1 + b2 − 0.5*F_c − 0.5*F_e`. Output: `[F_a, F_b, F_c, F_d_recovered]` per group at 23.976fps. Three of the four output frames are bit-exact source pixels.

**Use case.** This tool is targeted at preparing degraded sources for AI upscalers like **Topaz Video AI / Proteus**. The reconstructed F_d frames will retain horizontal comb stripes from the staggered field sampling — this is intentional. AI upscalers trained on broad degradation produce sharper, more accurate output when fed slightly-flawed-but-crisp frames than when fed pre-deinterlaced softened frames. Let the upscaler's deinterlace settings clean up the residual combs as part of the upscale pass.

**Requirements:** VapourSynth R65+, the `lsmas` plugin, and `ffmpeg` on `PATH`.

### Usage

Auto-detect cadence and render to ProRes 422 next to the source:

```
python unblend_telecine.py -i "movie.mp4" --audio
```

Quick H.264 preview at a specific timestamp:

```
python unblend_telecine.py -i "movie.mp4" --codec h264 --start 12:30 --duration 5:00
```

Reuse a previously-detected segment list (skips the detection pass):

```
python unblend_telecine.py -i "movie.mp4" --load-segments segments.json --audio
```

### Options

| Flag | Description |
|------|-------------|
| `-i FILE` / `--input` | Source video file (required). |
| `-o FILE` / `--output` | Output video file. **Default**: `<input_stem> [unblended]<ext>` next to the source (`.mov` for ProRes, `.mp4` for H.264). |
| `--phase {0..4}` | Force a single global phase (skips auto-detect). Use only on sources you know have uniform cadence — most edited material does not. |
| `--start TC` | Start time as `HH:MM:SS`, `MM:SS`, or seconds. Default: file start. |
| `--duration TC` | Duration as `HH:MM:SS`, `MM:SS`, or seconds. Default: rest of file. |
| `--codec {prores,prores_hq,h264}` | `prores` = ProRes 422 (recommended for AI upscaler input); `prores_hq` = higher bitrate ProRes 422 HQ; `h264` = libx264 CRF 18 for quick test renders. Default `prores`. |
| `--audio` | Mux source audio with the trim offset applied. |
| `--threads N` | Parallel worker processes for cadence detection. Default 8. Each worker opens its own decoder, so the speedup is real (no GIL contention). |
| `--save-segments PATH` | Write the detected segments JSON to `PATH` so it can be reused with `--load-segments`. |
| `--load-segments PATH` | Skip detection and load segments from a previous JSON. Useful for re-rendering with a different codec or trim window without paying the detection cost. |
| `--dry-run` | Print the generated VapourSynth script and ffmpeg command without rendering. |

### Cadence and phase tracking

Scene cuts in edited sources reset the 5:4 cadence to a different phase. By default, the script auto-detects per-segment phase: it samples 50-frame windows across the file, identifies which two positions per cycle of 5 are interlaced (high adjacent-row variance on motion regions), maps that to the source-absolute phase, smooths out single-window noise, and emits a list of `(start_frame, end_frame, phase)` segments. Each segment is then unblended with its own phase.

Two stages of progress are printed:
1. `Detecting cadence: frames [0, 175040), window=50, stride=25, threads=8` followed by `5%, 10%, ...` — the detection pass (typically ~3 minutes for a 2-hour movie at default settings).
2. `Rendering 140032 frames (5840s @ 23.976fps)...` followed by per-percent render progress — the actual encode.

For unedited sources with truly uniform cadence (rare), `--phase N` skips detection entirely.

**File size estimates** (480p, full 2-hour movie):

| Codec | Size |
|-------|------|
| `h264` (CRF 18) | ~1–2 GB |
| `prores` (422 std) | ~25 GB |
| `prores_hq` (422 HQ) | ~40 GB |

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
