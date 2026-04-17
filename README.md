# Video Standardizer

A Python utility for cleaning up and standardizing a video library. It handles the full pipeline: remuxing files into a consistent container, selecting and filtering audio and subtitle tracks by language, normalizing audio language metadata, renaming files with structured episode tags, and tagging filenames with resolution and encoding info — all without ever re-encoding audio or video.

It works well for processing downloaded TV episodes and movies that arrive in inconsistent states: mixed containers, wrong or missing language tags, multiple audio tracks in unwanted languages, embedded EIA-608 garbage captions, or arbitrary filenames.

## Features

- **Zero re-encoding** — every operation is a stream copy; processing is fast regardless of file size
- **Smart audio track selection** — keeps the best (most channels) track per language; English and Japanese kept by default; configurable per run
- **Language normalization** — audio tracks with unknown/undefined language tags are automatically reassigned to English
- **No-audio-drop safety** — if language filtering would eliminate all audio tracks, all tracks are kept instead
- **Episode renaming** — parses and normalizes episode tags including multi-episode formats (`S01E01E02`, `S01E01-02`, `S01E01-E02`)
- **Metadata filename tags** — appends `[HD 8Mbps HEVC]`-style labels to output filenames
- **Container fallback** — automatically switches to the most compatible container for subtitle streams to avoid re-encoding (e.g. PGS subtitles always go to MKV)
- **External subtitle support** — automatically picks up `.en.srt`, `.eng.srt`, `.srt`, or `.sub` sidecar files and embeds them
- **Error logging** — failed conversions are written to `conversion_errors.log` with the full ffmpeg error for later review
- **GUI and CLI modes** — full Tkinter GUI with live output, or fully scriptable from the command line

## Requirements

- Python 3.8+
- [FFmpeg](https://ffmpeg.org/download.html) (both `ffmpeg` and `ffprobe` must be on your `PATH`)

### Python dependencies

| Package | Purpose | Required? |
|---------|---------|----------|
| [`tkinterdnd2`](https://pypi.org/project/tkinterdnd2/) | Drag-and-drop files/folders onto the GUI input field | **Required** |
| [`prompt_toolkit`](https://python-prompt-toolkit.readthedocs.io/) | Tab-completion in CLI interactive mode | Optional — auto-installed on first use |

Install:

```
pip install tkinterdnd2
```

## Installation

No installation required. Clone or download the repository and run the script directly.

```
git clone https://github.com/yourname/video-standardizer.git
cd video-standardizer
```

Ensure `ffmpeg` and `ffprobe` are available:

```
ffmpeg -version
ffprobe -version
```

## Usage

### GUI (recommended)

Double-click `video_standardizer.pyw` to open the GUI with no console window. The input path defaults to the last folder you processed; on first run it defaults to the folder the script lives in.

Or launch from a terminal (input path defaults to the terminal's current directory and is saved for future double-click launches):

```
python video_standardizer.py
```

The GUI layout:

- **Left panel** — mode, input/output paths, container, language, and option controls.
- **Right panel (top)** — file list with live status icons (⟳ running, ✓ done, → renamed, — skipped, ✗ failed). Select a file and click **Stream Info** or **Show Plan** to inspect it inline.
- **Right panel (bottom)** — per-file and batch progress bars, plus a scrolling output log with colour-coded plan diffs and processing messages.
- **Drag-and-drop** — drop a file or folder onto the input field (requires `tkinterdnd2`).
- **Stop** button — terminates the active ffmpeg process immediately.
- A **batch summary** (remuxed / renamed / skipped / failed counts) is printed after every run.

### Command line — folder mode

Process every video file in a folder:

```
python video_standardizer.py -f "C:\Videos\Show Season 1"
```

### Command line — single file

```
python video_standardizer.py -i "S01E01 - Pilot.mkv"
```

### Interactive mode

Run with no arguments to be prompted for a path with tab-completion:

```
python video_standardizer.py
```

## Options

| Flag | Long form | Description |
|------|-----------|-------------|
| `-f PATH` | `--folder` | Folder containing video files to process |
| `-i FILE` | `--input` | Single input file |
| `-e EXT` | `--extension` | Preferred output container: `mkv` (default) or `mp4`. Automatically adjusted for subtitle compatibility. |
| `-l LANG ...` | `--languages` | Space-separated ISO 639-2 language codes to keep (default: `eng jpn`). The best track per language is kept. |
| `-r` | `--rename` | Rename files only — skip remux entirely |
| `-n` | `--norename` | Keep the original filename; only change the container/tracks |
| `-c` | `--convert-force` | Re-process files that appear to have already been processed |
| `-o DIR` | `--output` | Write output files to a different directory |
| `-d` | `--dry-run` | Preview what would happen without writing any files |
| `-v` | `--verbose` | Print full stream info and ffmpeg commands |

## How it works

1. **Probe** — `ffprobe` reads all stream metadata in a single call.
2. **Audio selection** — for each language in `--languages`, the track with the most channels is selected. Tracks tagged `und`/`unk` are reassigned to English if no English track exists. If the filter would produce zero audio tracks, all tracks are kept.
3. **Subtitle selection** — English and undefined subtitle tracks are kept; EIA-608 embedded captions are dropped in favour of proper subtitle streams.
4. **Container selection** — selected subtitle codecs are checked against the preferred container's compatibility list. If any codec would require re-encoding, the container is switched to a more compatible option automatically.
5. **Filename building** — episode tags are extracted and normalised (`S01E01E02`, `S01E01-02`, etc.), and a metadata suffix is appended (`[HD 8Mbps HEVC]`).
6. **Rename or remux** — if the selected tracks already match the input and the container is unchanged, the file is simply renamed. Otherwise, `ffmpeg` remuxes with stream copy for all tracks. Chapters and global metadata are always preserved (`-map_chapters 0 -map_metadata 0`).
7. **Error logging** — if ffmpeg exits with an error, the filename, full command, and stderr output are appended to `conversion_errors.log` in the script directory.

## Subtitle container compatibility

MKV supports a broad range of subtitle formats (SRT, ASS, PGS, VobSub, DVB, WebVTT, and more). MP4 only natively carries text-based formats (`mov_text`, TTML). The tool picks the container automatically based on what the file actually contains — you never need to worry about this.

## Language codes

Use [ISO 639-2/B](https://en.wikipedia.org/wiki/List_of_ISO_639-2_codes) three-letter codes. Common examples:

| Language | Code |
|----------|------|
| English | `eng` |
| Japanese | `jpn` |
| Spanish | `spa` |
| French | `fra` |
| German | `deu` |
| Chinese | `zho` |
| Korean | `kor` |

## License

MIT
