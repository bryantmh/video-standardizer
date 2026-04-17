import subprocess
import json
import re
import os
import argparse
import pprint
import datetime
import threading

# Prevent subprocess calls from spawning a visible console window on Windows.
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

DEFAULT_KEEP_LANGUAGES = ['eng', 'jpn']
VIDEO_EXTENSIONS = ('.mkv', '.m4v', '.mp4', '.ts', '.mov', '.mpg', '.avi', '.flv')

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_SCRIPT_DIR, 'video_standardizer_config.json')
ERROR_LOG = os.path.join(_SCRIPT_DIR, 'conversion_errors.log')


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass


def log_error(file, cmd, stderr):
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(ERROR_LOG, 'a', encoding='utf-8') as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"[{timestamp}] FAILED: {file}\n")
            f.write(f"CMD: {' '.join(cmd)}\n")
            f.write(f"STDERR:\n{stderr}\n")
    except Exception:
        pass


def get_all_streams(input_file):
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-show_entries',
        'stream=index,codec_name,codec_type,bit_rate,channels,bits_per_raw_sample,width,height'
        ':stream_tags=language,title',
        '-show_entries', 'format=bit_rate,duration',
        '-print_format', 'json', input_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                             creationflags=_NO_WINDOW)
    return json.loads(result.stdout)


def select_audio_tracks(streams, keep_languages=None):
    """Select the best audio track per kept language.

    Returns list of (stream_relative_index, language, needs_language_change) tuples.
    """
    if keep_languages is None:
        keep_languages = list(DEFAULT_KEEP_LANGUAGES)

    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
    if not audio_streams:
        return []

    lang_groups = {}
    for i, stream in enumerate(audio_streams):
        lang = stream.get('tags', {}).get('language', 'und')
        lang_groups.setdefault(lang, []).append((i, stream))

    selected = []

    for lang in keep_languages:
        if lang in lang_groups:
            best_idx, _ = max(lang_groups[lang], key=lambda x: x[1].get('channels', 0))
            selected.append((best_idx, lang, False))

    has_eng = any(s[1] == 'eng' for s in selected)
    for und_lang in ['und', 'unk', '']:
        if und_lang in lang_groups and not has_eng:
            best_idx, _ = max(lang_groups[und_lang], key=lambda x: x[1].get('channels', 0))
            selected.append((best_idx, 'eng', True))
            has_eng = True
            break

    if not selected:
        for i, stream in enumerate(audio_streams):
            lang = stream.get('tags', {}).get('language', 'und')
            selected.append((i, lang, lang in ['und', 'unk', '']))

    return selected


def select_subtitle_tracks(streams):
    subtitle_streams = [s for s in streams if s.get('codec_type') == 'subtitle']
    if not subtitle_streams:
        return []

    english, no_608, all_subs = [], [], []

    for i, stream in enumerate(subtitle_streams):
        lang = stream.get('tags', {}).get('language', 'und')
        if lang in ('eng', 'und', 'unk', ''):
            all_subs.append(i)
            if lang == 'eng':
                english.append(i)
                if stream.get('codec_name') != 'eia_608':
                    no_608.append(i)

    return no_608 or english or all_subs


def get_resolution(streams):
    for stream in streams:
        if stream.get('codec_type') == 'video':
            width = stream.get('width')
            height = stream.get('height')
            if width and height:
                if width >= 3000 or height >= 1800:
                    return "4K"
                elif width >= 1800 or height >= 1000:
                    return "HD"
                else:
                    return "SD"
    return None


def get_bitrate(format_info):
    if format_info and 'bit_rate' in format_info:
        return int(format_info['bit_rate']) / 1000000
    return None


def get_encoding(streams):
    for stream in streams:
        if stream.get('codec_type') == 'video':
            return stream.get('codec_name', '').upper()
    return None


def extract_episode_info(filename):
    patterns = [
        r'[Ss](\d{1,2})([Ee]\d{1,2}(?:[Ee]\d{1,2})+)',
        r'[Ss](\d{1,2})([Ee]\d{1,2}-[Ee]\d{1,2})',
        r'[Ss](\d{1,2})([Ee]\d{1,2}-\d{1,2})',
        r'[Ss](\d{1,2})([Ee]\d{1,2})',
    ]
    for pattern in patterns:
        match = re.search(pattern + r'([ _.\-].*)?', filename)
        if match:
            season = match.group(1).zfill(2)
            ep_part = match.group(2).upper()
            tag = f'S{season}{ep_part}'
            suffix = match.group(3) if match.group(3) else ''
            if suffix:
                suffix = os.path.splitext(suffix)[0]
                if not suffix.startswith(' - '):
                    suffix = ''
            return tag, suffix, match
    return None, None, None


def build_output_filename(input_file, extension, streams, format_info,
                          norename=False, convert_force=False, output_dir=None):
    directory = output_dir if output_dir else (os.path.dirname(input_file) or '.')

    if not os.path.exists(directory):
        os.makedirs(directory)

    basename = os.path.basename(input_file)

    pattern = r'\[\w+ \d+Mbps \w+\]\.\w+$'
    if not convert_force and re.search(pattern, basename):
        return None

    if norename:
        name_no_ext = os.path.splitext(basename)[0]
        return os.path.join(directory, f"{name_no_ext}.{extension}")

    tag, suffix, _ = extract_episode_info(basename)
    if tag:
        filename = tag + (suffix or '')
    else:
        filename = os.path.splitext(basename)[0]

    resolution = get_resolution(streams)
    bitrate = get_bitrate(format_info)
    encoding = get_encoding(streams)
    parts = []
    if resolution:
        parts.append(resolution)
    if bitrate:
        parts.append(f"{round(bitrate)}Mbps")
    if encoding:
        parts.append(encoding)
    if parts:
        filename += " [" + " ".join(parts) + "]"

    filename += f".{extension}"
    return os.path.join(directory, filename)


SUBTITLE_CODECS_BY_CONTAINER = {
    'mkv': {
        'subrip', 'srt', 'ass', 'ssa', 'webvtt', 'vtt',
        'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvb_subtitle',
        'hdmv_text_subtitle', 'microdvd', 'xsub', 'ttml', 'dfxp',
    },
    'mp4': {
        'mov_text', 'hdmv_text_subtitle', 'ttml', 'dfxp',
    },
}


def get_supported_subtitle_codecs(container):
    return SUBTITLE_CODECS_BY_CONTAINER.get(container, set())


def determine_best_extension(preferred_extension, selected_subtitle_codecs):
    if not selected_subtitle_codecs:
        return preferred_extension

    preferred_supported = get_supported_subtitle_codecs(preferred_extension)
    if all(c in preferred_supported for c in selected_subtitle_codecs):
        return preferred_extension

    fallback = 'mp4' if preferred_extension == 'mkv' else 'mkv'
    fallback_supported = get_supported_subtitle_codecs(fallback)
    if all(c in fallback_supported for c in selected_subtitle_codecs):
        return fallback

    return 'mkv'


def process_file(file, extension="mkv", dry_run=False, rename=False, verbose=False,
                 norename=False, convert_force=False,
                 output_dir=None, keep_languages=None, print_fn=None,
                 progress_fn=None, proc_holder=None):
    """Process a single video file.

    Returns a dict with 'status': 'renamed' | 'remuxed' | 'skipped' | 'failed' | 'dry_run'.
    """
    if print_fn is None:
        print_fn = print
    if keep_languages is None:
        keep_languages = list(DEFAULT_KEEP_LANGUAGES)

    probe = get_all_streams(file)
    streams = probe.get('streams', [])
    format_info = probe.get('format', {})

    if not streams:
        print_fn(f"No streams found in {file}")
        return {'status': 'failed'}

    if verbose:
        print_fn(pprint.pformat(probe))

    duration = float(format_info.get('duration') or 0)

    audio_selection = select_audio_tracks(streams, keep_languages)
    subtitle_selection = select_subtitle_tracks(streams)

    audio_streams_all = [s for s in streams if s.get('codec_type') == 'audio']
    subtitle_streams_all = [s for s in streams if s.get('codec_type') == 'subtitle']

    selected_sub_codecs = [subtitle_streams_all[i].get('codec_name', '') for i in subtitle_selection]
    actual_extension = determine_best_extension(extension, selected_sub_codecs)
    if actual_extension != extension:
        print_fn(f"  Note: Using {actual_extension.upper()} instead of {extension.upper()} "
                 f"for subtitle compatibility")

    output_file = build_output_filename(file, actual_extension, streams, format_info,
                                        norename, convert_force, output_dir)
    if not output_file:
        print_fn(f"Skipping {file} as it is already processed")
        return {'status': 'skipped'}

    base_file_name = os.path.splitext(file)[0]
    subtitle_file = None
    for ext in ['.en.srt', '.eng.srt', '.srt', '.sub']:
        candidate = base_file_name + ext
        if os.path.exists(candidate):
            subtitle_file = candidate
            break

    needs_language_change = any(a[2] for a in audio_selection)
    same_audio = len(audio_selection) == len(audio_streams_all) and not needs_language_change
    same_subs = len(subtitle_selection) == len(subtitle_streams_all)
    no_external_subs = subtitle_file is None
    input_ext = os.path.splitext(file)[1].lstrip('.').lower()
    same_container = input_ext == actual_extension.lower()

    if not convert_force and (rename or (same_audio and same_subs and no_external_subs and same_container)):
        original_ext = os.path.splitext(file)[1]
        output_with_orig = os.path.splitext(output_file)[0] + original_ext
        if os.path.normpath(file) == os.path.normpath(output_with_orig):
            print_fn(f"Skipping {file} - already correctly named")
            return {'status': 'skipped'}
        if not dry_run:
            os.rename(file, output_with_orig)
            print_fn(f"Renamed {file} to {output_with_orig}")
            return {'status': 'renamed'}
        else:
            print_fn(f"Will rename {file} to {output_with_orig}")
            return {'status': 'dry_run'}

    cmd = ['ffmpeg', '-y', '-progress', 'pipe:1', '-nostats', '-i', file]

    has_external_sub = bool(subtitle_file and not subtitle_selection)
    if has_external_sub:
        cmd.extend(['-i', subtitle_file])
        print_fn(f"  Adding external subtitle: {subtitle_file}")

    cmd.extend(['-map', '0:v:0', '-c:v', 'copy'])
    cmd.extend(['-map_chapters', '0', '-map_metadata', '0'])

    out_audio_idx = 0
    for rel_idx, lang, _change in audio_selection:
        cmd.extend(['-map', f'0:a:{rel_idx}'])
        cmd.extend([f'-metadata:s:a:{out_audio_idx}', f'language={lang}'])
        cmd.extend([f'-c:a:{out_audio_idx}', 'copy'])
        out_audio_idx += 1

    out_sub_idx = 0
    for sub_idx in subtitle_selection:
        cmd.extend(['-map', f'0:s:{sub_idx}'])
        cmd.extend([f'-metadata:s:s:{out_sub_idx}', 'language=eng'])
        cmd.extend([f'-c:s:{out_sub_idx}', 'copy'])
        out_sub_idx += 1

    if has_external_sub:
        cmd.extend(['-map', '1:s:0'])
        cmd.extend([f'-metadata:s:s:{out_sub_idx}', 'language=eng'])
        cmd.extend([f'-c:s:{out_sub_idx}', 'copy'])

    cmd.append(output_file)

    audio_desc = ", ".join(f"{l}(idx={i})" for i, l, _ in audio_selection)
    print_fn(f"Processing: {os.path.basename(file)}")
    print_fn(f"  Audio: [{audio_desc}] | Subtitles: {subtitle_selection}")
    print_fn(f"  Output: {output_file}")

    if dry_run:
        print_fn(f"  CMD: {' '.join(cmd)}")
        return {'status': 'dry_run'}

    if verbose:
        print_fn(f"  CMD: {' '.join(cmd)}")

    if progress_fn:
        progress_fn(0)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace',
        creationflags=_NO_WINDOW
    )
    if proc_holder is not None:
        proc_holder['proc'] = proc

    stderr_data = []

    def _read_stderr():
        for line in proc.stderr:
            stderr_data.append(line)

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    for line in proc.stdout:
        line = line.strip()
        if line.startswith('out_time_us=') and progress_fn and duration > 0:
            try:
                us = int(line.split('=', 1)[1])
                pct = min(99.0, (us / 1_000_000) / duration * 100)
                progress_fn(pct)
            except (ValueError, ZeroDivisionError):
                pass

    proc.wait()
    stderr_thread.join(timeout=5)
    full_stderr = ''.join(stderr_data)

    if proc.returncode != 0:
        print_fn(f"  ERROR: {full_stderr[:500]}")
        print_fn(f"  Details written to: {ERROR_LOG}")
        log_error(file, cmd, full_stderr)
        return {'status': 'failed'}
    else:
        print_fn(f"  Done.")
        return {'status': 'remuxed'}


def build_file_plan(file, extension="mkv", norename=False, convert_force=False,
                    output_dir=None, keep_languages=None):
    """Return a human-readable diff of what will be done to a file."""
    if keep_languages is None:
        keep_languages = list(DEFAULT_KEEP_LANGUAGES)

    probe = get_all_streams(file)
    streams = probe.get('streams', [])
    format_info = probe.get('format', {})

    if not streams:
        return f"{os.path.basename(file)}\n  ERROR: No streams found"

    audio_selection = select_audio_tracks(streams, keep_languages)
    subtitle_selection = select_subtitle_tracks(streams)

    audio_streams_all = [s for s in streams if s.get('codec_type') == 'audio']
    subtitle_streams_all = [s for s in streams if s.get('codec_type') == 'subtitle']
    video_streams_all = [s for s in streams if s.get('codec_type') == 'video']

    selected_sub_codecs = [subtitle_streams_all[i].get('codec_name', '') for i in subtitle_selection]
    actual_extension = determine_best_extension(extension, selected_sub_codecs)

    output_file = build_output_filename(file, actual_extension, streams, format_info,
                                        norename, convert_force, output_dir)

    SEP = '─' * 54
    lines = [SEP, f"File:   {os.path.basename(file)}"]

    if not output_file:
        lines.append("Output: (skip — already processed)")
        lines.append(SEP)
        return "\n".join(lines)

    out_name = os.path.basename(output_file)
    in_name = os.path.basename(file)
    lines.append(f"Output: {out_name}" if out_name != in_name else "Output: (same name)")

    if actual_extension != extension:
        lines.append(f"Container: {extension.upper()} → {actual_extension.upper()}"
                     f"  (subtitle compatibility)")
    else:
        lines.append(f"Container: {actual_extension.upper()}")
    lines.append("")

    for s in video_streams_all:
        codec = s.get('codec_name', '?').upper()
        w, h = s.get('width', '?'), s.get('height', '?')
        lines.append(f"  ✓ Video    [{codec:>6}]  {w}x{h}  →  copy")

    audio_kept = {a[0]: a for a in audio_selection}
    for i, s in enumerate(audio_streams_all):
        lang = s.get('tags', {}).get('language', 'und')
        codec = s.get('codec_name', '?').upper()
        ch = s.get('channels', '?')
        if i in audio_kept:
            _, new_lang, changed = audio_kept[i]
            tag_note = f"  [tag: {lang}→{new_lang}]" if changed else ""
            lines.append(f"  ✓ Audio    [{codec:>6}]  {ch}ch  lang={lang}{tag_note}  →  copy")
        else:
            lines.append(f"  ✗ Audio    [{codec:>6}]  {ch}ch  lang={lang}  →  DROP")

    for i, s in enumerate(subtitle_streams_all):
        lang = s.get('tags', {}).get('language', 'und')
        codec = s.get('codec_name', '?')
        title = s.get('tags', {}).get('title', '')
        label = f"lang={lang}" + (f"  {title}" if title else "")
        if i in subtitle_selection:
            lines.append(f"  ✓ Sub      [{codec:>20}]  {label}  →  copy")
        else:
            lines.append(f"  ✗ Sub      [{codec:>20}]  {label}  →  DROP")

    lines.append(SEP)
    return "\n".join(lines)


def format_stream_info(file):
    """Return a human-readable stream info string for a file."""
    probe = get_all_streams(file)
    streams = probe.get('streams', [])
    fmt = probe.get('format', {})

    SEP = '─' * 54
    lines = [SEP, f"Stream Info: {os.path.basename(file)}"]

    dur = float(fmt.get('duration') or 0)
    br = int(fmt.get('bit_rate') or 0) // 1000
    lines.append(f"Duration: {int(dur//3600):02d}:{int((dur%3600)//60):02d}:{dur%60:05.2f}")
    lines.append(f"Bitrate:  {br} kb/s")
    lines.append("")

    for s in streams:
        stype = s.get('codec_type', '?')
        codec = s.get('codec_name', '?')
        lang = s.get('tags', {}).get('language', 'und')
        title = s.get('tags', {}).get('title', '')
        idx = s.get('index', '?')
        if stype == 'video':
            lines.append(f"  [{idx}] Video    {codec.upper():>8}  "
                         f"{s.get('width', '?')}x{s.get('height', '?')}")
        elif stype == 'audio':
            ch = s.get('channels', '?')
            lines.append(f"  [{idx}] Audio    {codec.upper():>8}  {ch}ch  "
                         f"lang={lang}  {title}")
        elif stype == 'subtitle':
            lines.append(f"  [{idx}] Subtitle {codec:>20}  lang={lang}  {title}")
        else:
            lines.append(f"  [{idx}] {stype}")

    lines.append(SEP)
    return "\n".join(lines)


def get_video_files(folder_path):
    if not os.path.isdir(folder_path):
        return []
    return sorted([
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, f)) and f.lower().endswith(VIDEO_EXTENSIONS)
    ])


def main():
    parser = argparse.ArgumentParser(description="Video Standardizer - Process and standardize video files.")
    parser.add_argument("-f", "--folder", help="Folder path containing the files to process")
    parser.add_argument("-i", "--input", help="Single input file name")
    parser.add_argument("-d", "--dry-run", action='store_true', help="Print commands without executing")
    parser.add_argument("-e", "--extension", default='mkv', help="Output container (default: mkv)")
    parser.add_argument("-r", "--rename", action='store_true', help="Just rename files without re-encoding")
    parser.add_argument("-v", "--verbose", action='store_true', help="Print detailed information")
    parser.add_argument("-n", "--norename", action="store_true", help="Keep original filename")
    parser.add_argument("-c", "--convert-force", action="store_true", help="Process even if already processed")
    parser.add_argument("-o", "--output", help="Output directory path")
    parser.add_argument("-l", "--languages", nargs='+', default=list(DEFAULT_KEEP_LANGUAGES),
                        help=f"Languages to keep (default: {' '.join(DEFAULT_KEEP_LANGUAGES)})")

    args = parser.parse_args()

    if args.folder and args.input:
        print("Specify only one option: -f or -i.")
        return

    if args.folder:
        files = get_video_files(args.folder)
        if not files:
            print("No video files found in folder.")
            return
    elif args.input:
        files = [args.input]
    else:
        launch_gui(terminal_cwd=os.getcwd())
        return

    for file in files:
        process_file(file, args.extension, args.dry_run, args.rename, args.verbose,
                     args.norename, args.convert_force,
                     args.output, args.languages)


def launch_gui(terminal_cwd=None):
    """Launch the GUI for the video standardizer.

    terminal_cwd: the working directory of the launching terminal (passed from main()).
    When None (double-click launch via .pyw), only the saved last_folder or
    the script's own directory is used — never os.getcwd().
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext
    from tkinterdnd2 import DND_FILES, TkinterDnD

    # ── Resolve default path and persist terminal_cwd if applicable ──────
    cfg = load_config()
    saved_folder = cfg.get('last_folder', '')

    if terminal_cwd and os.path.isdir(terminal_cwd):
        # Launched from terminal with --gui: save cwd so next double-click
        # picks it up, and use it now.
        cfg['last_folder'] = terminal_cwd
        save_config(cfg)
        _default_path = terminal_cwd
    elif saved_folder and os.path.isdir(saved_folder):
        _default_path = saved_folder
    else:
        # No saved value and no terminal context (double-click first run):
        # use the folder the script lives in rather than System32.
        _default_path = _SCRIPT_DIR

    class VideoStandardizerGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Video Standardizer")
            self.root.geometry("1200x780")
            self.root.minsize(950, 560)
            self.processing = False
            self._stop_flag = False
            self._proc_holder = {}
            self._file_paths = []

            # Register the root window as a drop target so the whole app
            # accepts file/folder drops, not just the path entry field.
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop)

            style = ttk.Style()
            try:
                style.theme_use('vista')
            except Exception:
                pass

            # ── Main horizontal split ────────────────────────────────────
            main_pane = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
            main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

            # ── LEFT: Options ────────────────────────────────────────────
            left_frame = ttk.Frame(main_pane, width=340)
            main_pane.add(left_frame, weight=1)

            # Mode
            mode_frame = ttk.LabelFrame(left_frame, text="Mode", padding=5)
            mode_frame.pack(fill=tk.X, padx=5, pady=(5, 2))
            self.mode_var = tk.StringVar(value="folder")
            ttk.Radiobutton(mode_frame, text="Folder", variable=self.mode_var,
                            value="folder", command=self._on_path_change).pack(side=tk.LEFT, padx=5)
            ttk.Radiobutton(mode_frame, text="Single File", variable=self.mode_var,
                            value="file", command=self._on_path_change).pack(side=tk.LEFT, padx=5)

            # Input path
            path_frame = ttk.LabelFrame(left_frame, text="Input", padding=5)
            path_frame.pack(fill=tk.X, padx=5, pady=2)

            self.path_var = tk.StringVar(value=_default_path)
            self._path_trace_id = None
            self.path_var.trace_add('write', self._path_changed)

            path_entry = ttk.Entry(path_frame, textvariable=self.path_var)
            path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            path_entry.drop_target_register(DND_FILES)
            path_entry.dnd_bind('<<Drop>>', self._on_drop)

            ttk.Button(path_frame, text="Browse", command=self._browse).pack(side=tk.RIGHT)

            # Output directory
            out_frame = ttk.LabelFrame(left_frame, text="Output Directory (optional)", padding=5)
            out_frame.pack(fill=tk.X, padx=5, pady=2)
            self.output_var = tk.StringVar(value="")
            ttk.Entry(out_frame, textvariable=self.output_var).pack(
                side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            ttk.Button(out_frame, text="Browse", command=self._browse_output).pack(side=tk.RIGHT)

            # Container
            container_frame = ttk.LabelFrame(left_frame, text="Container", padding=5)
            container_frame.pack(fill=tk.X, padx=5, pady=2)
            self.extension_var = tk.StringVar(value="mkv")
            for ext in ["mkv", "mp4"]:
                ttk.Radiobutton(container_frame, text=ext.upper(),
                                variable=self.extension_var, value=ext).pack(side=tk.LEFT, padx=5)

            # Languages
            lang_frame = ttk.LabelFrame(left_frame, text="Keep Audio Languages", padding=5)
            lang_frame.pack(fill=tk.X, padx=5, pady=2)
            self.lang_var = tk.StringVar(value="eng jpn")
            ttk.Entry(lang_frame, textvariable=self.lang_var).pack(fill=tk.X)
            ttk.Label(lang_frame, text="Space-separated ISO 639-2 codes",
                      font=("", 8)).pack(anchor=tk.W)

            # Options
            opts_frame = ttk.LabelFrame(left_frame, text="Options", padding=5)
            opts_frame.pack(fill=tk.X, padx=5, pady=2)
            self.dry_run_var = tk.BooleanVar(value=False)
            self.rename_var = tk.BooleanVar(value=False)
            self.norename_var = tk.BooleanVar(value=False)
            self.verbose_var = tk.BooleanVar(value=False)
            self.force_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(opts_frame, text="Dry Run (preview only)",
                            variable=self.dry_run_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Rename Only (no re-encode)",
                            variable=self.rename_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Keep Original Filename",
                            variable=self.norename_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Force Re-process",
                            variable=self.force_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Verbose Output",
                            variable=self.verbose_var).pack(anchor=tk.W)

            # Action buttons
            btn_frame = ttk.Frame(left_frame, padding=5)
            btn_frame.pack(fill=tk.X, padx=5, pady=5)
            self.run_btn = ttk.Button(btn_frame, text="Run", command=self._run)
            self.run_btn.pack(fill=tk.X, pady=(0, 3))
            self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop,
                                       state=tk.DISABLED)
            self.stop_btn.pack(fill=tk.X)

            # ── RIGHT: File list + output ────────────────────────────────
            right_frame = ttk.Frame(main_pane)
            main_pane.add(right_frame, weight=2)

            right_pane = ttk.PanedWindow(right_frame, orient=tk.VERTICAL)
            right_pane.pack(fill=tk.BOTH, expand=True)

            # ── TOP: File list ───────────────────────────────────────────
            list_frame = ttk.LabelFrame(right_pane, text="Files")
            right_pane.add(list_frame, weight=1)

            list_inner = ttk.Frame(list_frame)
            list_inner.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 2))

            list_scroll = ttk.Scrollbar(list_inner, orient=tk.VERTICAL)
            self.file_listbox = tk.Listbox(
                list_inner, yscrollcommand=list_scroll.set,
                font=("Consolas", 9), selectmode=tk.SINGLE,
                bg="#252526", fg="#cccccc", selectbackground="#0078d4",
                activestyle='none', height=7
            )
            list_scroll.config(command=self.file_listbox.yview)
            list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.file_listbox.pack(fill=tk.BOTH, expand=True)

            list_btn_row = ttk.Frame(list_frame)
            list_btn_row.pack(fill=tk.X, padx=5, pady=(0, 5))
            ttk.Button(list_btn_row, text="Stream Info",
                       command=self._show_stream_info).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Button(list_btn_row, text="Show Plan",
                       command=self._show_plan).pack(side=tk.LEFT)

            # ── BOTTOM: Progress + Output ────────────────────────────────
            bottom_frame = ttk.Frame(right_pane)
            right_pane.add(bottom_frame, weight=3)

            # Progress bars
            prog_frame = ttk.Frame(bottom_frame)
            prog_frame.pack(fill=tk.X, padx=5, pady=(5, 2))

            row1 = ttk.Frame(prog_frame)
            row1.pack(fill=tk.X, pady=(0, 2))
            ttk.Label(row1, text="File: ", width=7, anchor=tk.W).pack(side=tk.LEFT)
            self.file_progress = ttk.Progressbar(row1, mode='determinate', maximum=100)
            self.file_progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.file_pct_label = ttk.Label(row1, text="", width=6, anchor=tk.E)
            self.file_pct_label.pack(side=tk.LEFT, padx=(4, 0))

            row2 = ttk.Frame(prog_frame)
            row2.pack(fill=tk.X)
            ttk.Label(row2, text="Batch:", width=7, anchor=tk.W).pack(side=tk.LEFT)
            self.batch_progress = ttk.Progressbar(row2, mode='determinate', maximum=100)
            self.batch_progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self.batch_lbl = ttk.Label(row2, text="", width=6, anchor=tk.E)
            self.batch_lbl.pack(side=tk.LEFT, padx=(4, 0))

            # Output text
            self.output_text = scrolledtext.ScrolledText(
                bottom_frame, wrap=tk.WORD, font=("Consolas", 9),
                state=tk.DISABLED, bg="#1e1e1e", fg="#cccccc",
                insertbackground="#cccccc"
            )
            self.output_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=(2, 0))
            self.output_text.tag_config('keep', foreground='#4ec994')
            self.output_text.tag_config('drop', foreground='#f14c4c')
            self.output_text.tag_config('info', foreground='#9cdcfe')
            self.output_text.tag_config('warn', foreground='#dcdcaa')
            self.output_text.tag_config('sep',  foreground='#555555')
            self.output_text.tag_config('head', foreground='#c586c0')

            ttk.Button(bottom_frame, text="Clear Output",
                       command=self._clear_output).pack(anchor=tk.E, padx=5, pady=5)

            # Initial file list population
            self._on_path_change()

        # ── Path / file list helpers ─────────────────────────────────────

        def _path_changed(self, *_):
            if self._path_trace_id:
                self.root.after_cancel(self._path_trace_id)
            self._path_trace_id = self.root.after(300, self._on_path_change)

        def _on_path_change(self):
            if self.processing:
                return
            path = self.path_var.get().strip()
            self.file_listbox.delete(0, tk.END)
            self._file_paths = []
            if self.mode_var.get() == 'folder' and os.path.isdir(path):
                for f in get_video_files(path):
                    self.file_listbox.insert(tk.END, f"  {os.path.basename(f)}")
                    self._file_paths.append(f)
            elif self.mode_var.get() == 'file' and os.path.isfile(path):
                self.file_listbox.insert(tk.END, f"  {os.path.basename(path)}")
                self._file_paths.append(path)

        def _get_selected_file(self):
            sel = self.file_listbox.curselection()
            if not sel:
                return None
            idx = sel[0]
            return self._file_paths[idx] if idx < len(self._file_paths) else None

        def _set_file_status(self, index, status):
            icons  = {'pending': '  ', 'running': '⟳ ', 'done': '✓ ',
                      'renamed': '→ ', 'skipped': '— ', 'failed': '✗ '}
            colors = {'done': '#4ec994', 'renamed': '#9cdcfe', 'skipped': '#888888',
                      'failed': '#f14c4c', 'running': '#dcdcaa', 'pending': '#cccccc'}
            try:
                raw = self.file_listbox.get(index)
                base = raw[2:] if len(raw) >= 2 else raw
                self.file_listbox.delete(index)
                self.file_listbox.insert(index, f"{icons.get(status, '  ')}{base}")
                self.file_listbox.itemconfig(index, fg=colors.get(status, '#cccccc'))
            except Exception:
                pass

        # ── Drag-and-drop ────────────────────────────────────────────────

        def _on_drop(self, event):
            # tkinterdnd2 wraps paths containing spaces in braces: {C:\My Dir\file.mkv}
            # Multiple dropped items arrive as: {path1} {path2} ...
            # We take only the first item.
            data = event.data.strip()
            if data.startswith('{'):
                end = data.index('}')
                path = data[1:end]
            else:
                path = data.split()[0]
            if os.path.isdir(path):
                self.mode_var.set('folder')
                self.path_var.set(path)
            elif os.path.isfile(path):
                self.mode_var.set('file')
                self.path_var.set(path)

        # ── Browse buttons ───────────────────────────────────────────────

        def _browse(self):
            current = self.path_var.get().strip()
            init_dir = current if os.path.isdir(current) else _SCRIPT_DIR
            if self.mode_var.get() == "folder":
                path = filedialog.askdirectory(initialdir=init_dir)
            else:
                path = filedialog.askopenfilename(
                    initialdir=os.path.dirname(current) if os.path.isfile(current) else init_dir,
                    filetypes=[("Video files",
                                " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)),
                               ("All files", "*.*")]
                )
            if path:
                self.path_var.set(path)

        def _browse_output(self):
            current = self.output_var.get().strip()
            path = filedialog.askdirectory(
                initialdir=current if os.path.isdir(current) else _SCRIPT_DIR)
            if path:
                self.output_var.set(path)

        # ── Output text helpers ──────────────────────────────────────────

        def _log(self, text, tag=None):
            self.output_text.configure(state=tk.NORMAL)
            if tag:
                self.output_text.insert(tk.END, text + "\n", tag)
            else:
                self.output_text.insert(tk.END, text + "\n")
            self.output_text.see(tk.END)
            self.output_text.configure(state=tk.DISABLED)

        def _log_plan(self, text):
            self.output_text.configure(state=tk.NORMAL)
            for line in text.splitlines():
                stripped = line.lstrip()
                if stripped.startswith('✓'):
                    self.output_text.insert(tk.END, line + "\n", 'keep')
                elif stripped.startswith('✗'):
                    self.output_text.insert(tk.END, line + "\n", 'drop')
                elif stripped.startswith('─'):
                    self.output_text.insert(tk.END, line + "\n", 'sep')
                else:
                    self.output_text.insert(tk.END, line + "\n", 'info')
            self.output_text.see(tk.END)
            self.output_text.configure(state=tk.DISABLED)

        def _clear_output(self):
            self.output_text.configure(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            self.output_text.configure(state=tk.DISABLED)

        # ── Progress bars ────────────────────────────────────────────────

        def _update_progress(self, file_pct, done, total):
            self.file_progress['value'] = file_pct
            self.file_pct_label.configure(text=f"{int(file_pct)}%")
            batch_pct = (done / total * 100) if total else 0
            self.batch_progress['value'] = batch_pct
            self.batch_lbl.configure(text=f"{done}/{total}")

        def _reset_progress(self):
            self.file_progress['value'] = 0
            self.file_pct_label.configure(text="")
            self.batch_progress['value'] = 0
            self.batch_lbl.configure(text="")

        # ── Inline stream info & plan (no popup windows) ────────────────

        def _show_stream_info(self):
            f = self._get_selected_file()
            if not f:
                self._log("Select a file in the list first.")
                return
            try:
                info = format_stream_info(f)
                self._log_plan(info)
            except Exception as e:
                self._log(f"Error reading stream info: {e}")

        def _show_plan(self):
            f = self._get_selected_file()
            if not f:
                self._log("Select a file in the list first.")
                return
            languages = self.lang_var.get().strip().split() or list(DEFAULT_KEEP_LANGUAGES)
            try:
                plan = build_file_plan(
                    f,
                    extension=self.extension_var.get(),
                    norename=self.norename_var.get(),
                    convert_force=self.force_var.get(),
                    output_dir=self.output_var.get().strip() or None,
                    keep_languages=languages,
                )
                self._log_plan(plan)
            except Exception as e:
                self._log(f"Error building plan: {e}")

        # ── Run / Stop ───────────────────────────────────────────────────

        def _run(self):
            if self.processing:
                return

            path = self.path_var.get().strip()
            if not path:
                self._log("ERROR: No input path specified.")
                return

            if self.mode_var.get() == "folder":
                files = get_video_files(path)
                if not files:
                    self._log(f"No video files found in: {path}")
                    return
            else:
                if not os.path.isfile(path):
                    self._log(f"File not found: {path}")
                    return
                files = [path]

            # Persist the folder for next launch
            run_cfg = load_config()
            run_cfg['last_folder'] = path if self.mode_var.get() == 'folder' else os.path.dirname(path)
            save_config(run_cfg)

            # Refresh file list and reset all status icons
            self._on_path_change()
            for i in range(len(self._file_paths)):
                self._set_file_status(i, 'pending')

            languages = self.lang_var.get().strip().split() or list(DEFAULT_KEEP_LANGUAGES)
            output_dir = self.output_var.get().strip() or None
            total = len(files)

            self.processing = True
            self._stop_flag = False
            self._proc_holder.clear()
            self.run_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
            self._reset_progress()

            ext_var = self.extension_var.get()
            dry_run = self.dry_run_var.get()
            rename = self.rename_var.get()
            verbose = self.verbose_var.get()
            norename = self.norename_var.get()
            force = self.force_var.get()

            def print_fn(msg, tag=None):
                self.root.after(0, self._log, msg, tag)

            def print_plan(msg):
                self.root.after(0, self._log_plan, msg)

            def worker():
                stats = {'remuxed': 0, 'renamed': 0, 'skipped': 0,
                         'failed': 0, 'dry_run': 0}
                try:
                    for i, f in enumerate(files):
                        if self._stop_flag:
                            print_fn("--- Stopped by user ---")
                            break

                        self.root.after(0, self._set_file_status, i, 'running')

                        try:
                            plan = build_file_plan(
                                f, extension=ext_var, norename=norename,
                                convert_force=force, output_dir=output_dir,
                                keep_languages=languages,
                            )
                            print_plan(plan)
                        except Exception as pe:
                            print_fn(f"  (plan error: {pe})")

                        def make_progress_fn(file_idx):
                            def _pfn(pct):
                                self.root.after(
                                    0, self._update_progress, pct, file_idx, total)
                            return _pfn

                        result = process_file(
                            f,
                            extension=ext_var,
                            dry_run=dry_run,
                            rename=rename,
                            verbose=verbose,
                            norename=norename,
                            convert_force=force,
                            output_dir=output_dir,
                            keep_languages=languages,
                            print_fn=print_fn,
                            progress_fn=make_progress_fn(i + 1),
                            proc_holder=self._proc_holder,
                        )

                        status = (result or {}).get('status', 'skipped')
                        stats[status] = stats.get(status, 0) + 1
                        icon = ('done'    if status == 'remuxed' else
                                'renamed' if status == 'renamed' else
                                'skipped' if status in ('skipped', 'dry_run') else
                                'failed')
                        self.root.after(0, self._set_file_status, i, icon)
                        self.root.after(0, self._update_progress, 100, i + 1, total)

                    if not self._stop_flag:
                        print_fn("")
                        print_fn("══════════════════════════════════════════════", 'sep')
                        print_fn("  BATCH COMPLETE", 'head')
                        print_fn(f"  Remuxed : {stats.get('remuxed', 0)}", 'keep')
                        print_fn(f"  Renamed : {stats.get('renamed', 0)}", 'info')
                        print_fn(f"  Skipped : {stats.get('skipped', 0)}")
                        if dry_run:
                            print_fn(f"  Dry Run : {stats.get('dry_run', 0)}", 'warn')
                        if stats.get('failed', 0):
                            print_fn(f"  Failed  : {stats['failed']}  "
                                     f"— see {ERROR_LOG}", 'drop')
                        print_fn("══════════════════════════════════════════════", 'sep')

                except Exception as e:
                    import traceback
                    print_fn(f"ERROR: {e}\n{traceback.format_exc()}")
                finally:
                    self.root.after(0, self._processing_done)

            threading.Thread(target=worker, daemon=True).start()

        def _stop(self):
            self._stop_flag = True
            proc = self._proc_holder.get('proc')
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass

        def _processing_done(self):
            self.processing = False
            self.run_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)

    root = TkinterDnD.Tk()
    VideoStandardizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
