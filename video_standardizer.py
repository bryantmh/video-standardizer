import subprocess
import json
import re
import os
import argparse
import sys
import pprint

pp = pprint.PrettyPrinter(indent=4)

DEFAULT_KEEP_LANGUAGES = ['eng', 'jpn']
VIDEO_EXTENSIONS = ('.mkv', '.m4v', '.mp4', '.ts', '.mov', '.mpg', '.avi', '.flv')


def get_all_streams(input_file):
    """Get all stream info from a file in one ffprobe call."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-show_entries', 'stream=index,codec_name,codec_type,bit_rate,channels,bits_per_raw_sample,width,height:stream_tags=language,title',
        '-show_entries', 'format=bit_rate',
        '-print_format', 'json', input_file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    return json.loads(result.stdout)


def select_audio_tracks(streams, keep_languages=None):
    """Select the best audio track per kept language.

    For each language in keep_languages, pick the track with the most channels.
    Unknown/undefined language tracks are treated as candidates for language change to English.
    If no tracks would be kept (no matching languages), keep ALL audio tracks instead.
    Returns list of (stream_relative_index, language, needs_language_change) tuples.
    """
    if keep_languages is None:
        keep_languages = list(DEFAULT_KEEP_LANGUAGES)

    audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
    if not audio_streams:
        return []

    # Group by language
    lang_groups = {}
    for i, stream in enumerate(audio_streams):
        lang = stream.get('tags', {}).get('language', 'und')
        lang_groups.setdefault(lang, []).append((i, stream))

    selected = []

    # For each kept language, find the best track
    for lang in keep_languages:
        if lang in lang_groups:
            best_idx, _ = max(lang_groups[lang], key=lambda x: x[1].get('channels', 0))
            selected.append((best_idx, lang, False))

    # Handle unknown/undefined tracks - assign to English if English not already found
    has_eng = any(s[1] == 'eng' for s in selected)
    for und_lang in ['und', 'unk', '']:
        if und_lang in lang_groups and not has_eng:
            best_idx, _ = max(lang_groups[und_lang], key=lambda x: x[1].get('channels', 0))
            selected.append((best_idx, 'eng', True))  # needs language change
            has_eng = True
            break

    # If we would end up with NO audio tracks, keep all of them
    if not selected:
        for i, stream in enumerate(audio_streams):
            lang = stream.get('tags', {}).get('language', 'und')
            selected.append((i, lang, lang in ['und', 'unk', '']))

    return selected


def select_subtitle_tracks(streams):
    """Select English/undefined subtitle tracks, preferring non-EIA608."""
    subtitle_streams = [s for s in streams if s.get('codec_type') == 'subtitle']
    if not subtitle_streams:
        return []

    english = []
    no_608 = []
    all_subs = []

    for i, stream in enumerate(subtitle_streams):
        lang = stream.get('tags', {}).get('language', 'und')
        if lang in ('eng', 'und', 'unk', ''):
            all_subs.append(i)
            if lang == 'eng':
                english.append(i)
                if stream.get('codec_name') != 'eia_608':
                    no_608.append(i)

    if no_608:
        return no_608
    elif english:
        return english
    else:
        return all_subs


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
    """Extract episode info supporting multi-episode patterns like S01E01E02, S01E01-02, S01E01-E02."""
    patterns = [
        # S01E01E02E03... (multiple E## concatenated)
        r'[Ss](\d{1,2})([Ee]\d{1,2}(?:[Ee]\d{1,2})+)',
        # S01E01-E02 (range with E prefix)
        r'[Ss](\d{1,2})([Ee]\d{1,2}-[Ee]\d{1,2})',
        # S01E01-02 (range without E prefix)
        r'[Ss](\d{1,2})([Ee]\d{1,2}-\d{1,2})',
        # S01E01 (single episode)
        r'[Ss](\d{1,2})([Ee]\d{1,2})',
    ]
    for pattern in patterns:
        match = re.search(pattern + r'([ _.\-].*)?', filename)
        if match:
            season = match.group(1).zfill(2)
            ep_part = match.group(2).upper()

            tag = f'S{season}{ep_part}'

            # Clean suffix
            suffix = match.group(3) if match.group(3) else ''
            if suffix:
                suffix = os.path.splitext(suffix)[0]
                if not suffix.startswith(' - '):
                    suffix = ''

            return tag, suffix, match
    return None, None, None


def build_output_filename(input_file, extension, streams, format_info, norename=False, convert_force=False, output_dir=None):
    """Build output filename with metadata tag."""
    if output_dir:
        directory = output_dir
    else:
        directory = os.path.dirname(input_file) or '.'

    if not os.path.exists(directory):
        os.makedirs(directory)

    basename = os.path.basename(input_file)

    # Check if already processed
    pattern = r'\[\w+ \d+Mbps \w+\]\.\w+$'
    if not convert_force and re.search(pattern, basename):
        return None

    if norename:
        name_no_ext = os.path.splitext(basename)[0]
        return os.path.join(directory, f"{name_no_ext}.{extension}")

    # Try episode extraction
    tag, suffix, ep_match = extract_episode_info(basename)
    if tag:
        filename = tag
        if suffix:
            filename += suffix
    else:
        filename = os.path.splitext(basename)[0]

    # Add metadata tag
    resolution = get_resolution(streams)
    bitrate = get_bitrate(format_info)
    encoding = get_encoding(streams)
    has_info = resolution or bitrate or encoding
    if has_info:
        filename += " ["
        parts = []
        if resolution:
            parts.append(resolution)
        if bitrate:
            parts.append(f"{round(bitrate)}Mbps")
        if encoding:
            parts.append(encoding)
        filename += " ".join(parts)
        filename += "]"

    filename += f".{extension}"
    return os.path.join(directory, filename)


def get_supported_subtitle_codecs(container):
    if container == 'mkv':
        return ['srt', 'ass', 'ssa', 'vtt', 'hdmv_pgs_subtitle', 'dvd_subtitle']
    elif container == 'mp4':
        return ['mov_text']
    else:
        return []


def process_file(file, extension="mkv", dry_run=False, rename=False, verbose=False,
                 subtitle_convert=False, norename=False, convert_force=False,
                 output_dir=None, keep_languages=None, print_fn=None):
    """Process a single video file."""
    if print_fn is None:
        print_fn = print

    if keep_languages is None:
        keep_languages = list(DEFAULT_KEEP_LANGUAGES)

    # Get all file info
    probe = get_all_streams(file)
    streams = probe.get('streams', [])
    format_info = probe.get('format', {})

    if not streams:
        print_fn(f"No streams found in {file}")
        return

    if verbose:
        print_fn(pprint.pformat(probe))

    # Build output filename
    output_file = build_output_filename(file, extension, streams, format_info,
                                        norename, convert_force, output_dir)
    if not output_file:
        print_fn(f"Skipping {file} as it is already processed")
        return

    # Select tracks
    audio_selection = select_audio_tracks(streams, keep_languages)
    subtitle_selection = select_subtitle_tracks(streams)

    audio_streams_all = [s for s in streams if s.get('codec_type') == 'audio']
    subtitle_streams_all = [s for s in streams if s.get('codec_type') == 'subtitle']

    # Check for external subtitle files
    base_file_name = os.path.splitext(file)[0]
    subtitle_file = None
    for ext in ['.en.srt', '.eng.srt', '.srt', '.sub']:
        candidate = base_file_name + ext
        if os.path.exists(candidate):
            subtitle_file = candidate
            break

    # Determine if we can just rename
    audio_indices = [a[0] for a in audio_selection]
    needs_language_change = any(a[2] for a in audio_selection)
    same_audio = len(audio_indices) == len(audio_streams_all) and not needs_language_change
    same_subs = len(subtitle_selection) == len(subtitle_streams_all)
    no_external_subs = subtitle_file is None

    if not convert_force and (rename or (same_audio and same_subs and no_external_subs and not subtitle_convert)):
        original_ext = os.path.splitext(file)[1]
        output_with_orig = os.path.splitext(output_file)[0] + original_ext
        if os.path.normpath(file) == os.path.normpath(output_with_orig):
            print_fn(f"Skipping {file} - already correctly named")
            return
        if not dry_run:
            os.rename(file, output_with_orig)
            print_fn(f"Renamed {file} to {output_with_orig}")
        else:
            print_fn(f"Will rename {file} to {output_with_orig}")
        return

    # Check subtitle codec compatibility
    supported = get_supported_subtitle_codecs(extension)
    sub_codec_convert = None
    for idx in subtitle_selection:
        codec = subtitle_streams_all[idx].get('codec_name', '')
        if codec not in supported and supported:
            sub_codec_convert = supported[0]
            break

    if subtitle_convert and supported:
        sub_codec_convert = supported[0]

    # Build ffmpeg command
    cmd = ['ffmpeg', '-y', '-i', file]

    has_external_sub = subtitle_file and not subtitle_selection
    if has_external_sub:
        cmd.extend(['-i', subtitle_file])
        print_fn(f"  Adding external subtitle: {subtitle_file}")

    # Map video
    cmd.extend(['-map', '0:v:0', '-c:v', 'copy'])

    # Map audio tracks with correct output indices
    out_audio_idx = 0
    for rel_idx, lang, change_lang in audio_selection:
        cmd.extend(['-map', f'0:a:{rel_idx}'])
        cmd.extend([f'-metadata:s:a:{out_audio_idx}', f'language={lang}'])
        cmd.extend([f'-c:a:{out_audio_idx}', 'copy'])
        out_audio_idx += 1

    # Map subtitle tracks with correct output indices
    text_codecs = {'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'vtt', 'subrip'}
    bitmap_codecs = {'hdmv_pgs_subtitle', 'dvd_subtitle', 'dvb_subtitle'}

    out_sub_idx = 0
    for sub_idx in subtitle_selection:
        cmd.extend(['-map', f'0:s:{sub_idx}'])
        cmd.extend([f'-metadata:s:s:{out_sub_idx}', 'language=eng'])

        src_codec = subtitle_streams_all[sub_idx].get('codec_name', '')
        if sub_codec_convert:
            src_text = src_codec in text_codecs
            src_bitmap = src_codec in bitmap_codecs
            dest_text = sub_codec_convert in text_codecs
            dest_bitmap = sub_codec_convert in bitmap_codecs
            if (src_text and dest_text) or (src_bitmap and dest_bitmap):
                cmd.extend([f'-c:s:{out_sub_idx}', sub_codec_convert])
            else:
                cmd.extend([f'-c:s:{out_sub_idx}', 'copy'])
        else:
            cmd.extend([f'-c:s:{out_sub_idx}', 'copy'])
        out_sub_idx += 1

    # Handle external subtitle (when no embedded subs found)
    if has_external_sub:
        cmd.extend(['-map', '1:s:0'])
        cmd.extend([f'-metadata:s:s:{out_sub_idx}', 'language=eng'])
        cmd.extend([f'-c:s:{out_sub_idx}', 'copy'])

    cmd.append(output_file)

    audio_desc = ", ".join(f"{l}(idx={i})" for i, l, _ in audio_selection)
    print_fn(f"Processing: {os.path.basename(file)}")
    print_fn(f"  Audio: [{audio_desc}] | Subtitles: {subtitle_selection}")
    print_fn(f"  Output: {output_file}")

    if not dry_run:
        if verbose:
            print_fn(f"  CMD: {' '.join(cmd)}")
        process = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if process.returncode != 0:
            print_fn(f"  ERROR: {process.stderr[:500]}")
        else:
            print_fn(f"  Done.")
    else:
        print_fn(f"  CMD: {' '.join(cmd)}")


def get_video_files(folder_path):
    """Get all video files from a folder."""
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
    parser.add_argument("-s", '--subtitle-convert', action='store_true', help="Convert subtitles to container-native format")
    parser.add_argument("-n", "--norename", action="store_true", help="Keep original filename")
    parser.add_argument("-c", "--convert-force", action="store_true", help="Process even if already processed")
    parser.add_argument("-o", "--output", help="Output directory path")
    parser.add_argument("-l", "--languages", nargs='+', default=list(DEFAULT_KEEP_LANGUAGES),
                        help=f"Languages to keep (default: {' '.join(DEFAULT_KEEP_LANGUAGES)})")
    parser.add_argument("--gui", action="store_true", help="Launch GUI")

    args = parser.parse_args()

    if args.gui:
        launch_gui()
        return

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
        try:
            from prompt_toolkit import prompt
            from prompt_toolkit.completion import PathCompleter
            input_file = prompt("Enter input file name (with extension): ", completer=PathCompleter())
            files = [input_file]
        except ImportError:
            print("Installing prompt_toolkit...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", 'prompt_toolkit'])
            print("Please run the script again.")
            return

    for file in files:
        process_file(file, args.extension, args.dry_run, args.rename, args.verbose,
                     args.subtitle_convert, args.norename, args.convert_force,
                     args.output, args.languages)


def launch_gui():
    """Launch the GUI for the video standardizer."""
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext
    import threading

    class VideoStandardizerGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Video Standardizer")
            self.root.geometry("1100x700")
            self.root.minsize(900, 500)
            self.processing = False

            style = ttk.Style()
            try:
                style.theme_use('vista')
            except Exception:
                pass

            # Main horizontal pane
            main_pane = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
            main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

            # ---- LEFT: Options Panel ----
            left_frame = ttk.Frame(main_pane, width=350)
            main_pane.add(left_frame, weight=1)

            # Mode
            mode_frame = ttk.LabelFrame(left_frame, text="Mode", padding=5)
            mode_frame.pack(fill=tk.X, padx=5, pady=(5, 2))

            self.mode_var = tk.StringVar(value="folder")
            ttk.Radiobutton(mode_frame, text="Folder", variable=self.mode_var, value="folder").pack(side=tk.LEFT, padx=5)
            ttk.Radiobutton(mode_frame, text="Single File", variable=self.mode_var, value="file").pack(side=tk.LEFT, padx=5)

            # Input path
            path_frame = ttk.LabelFrame(left_frame, text="Input", padding=5)
            path_frame.pack(fill=tk.X, padx=5, pady=2)

            self.path_var = tk.StringVar(value=os.getcwd())
            ttk.Entry(path_frame, textvariable=self.path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            ttk.Button(path_frame, text="Browse", command=self._browse).pack(side=tk.RIGHT)

            # Output directory
            out_frame = ttk.LabelFrame(left_frame, text="Output Directory (optional)", padding=5)
            out_frame.pack(fill=tk.X, padx=5, pady=2)

            self.output_var = tk.StringVar(value="")
            ttk.Entry(out_frame, textvariable=self.output_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
            ttk.Button(out_frame, text="Browse", command=self._browse_output).pack(side=tk.RIGHT)

            # Container
            container_frame = ttk.LabelFrame(left_frame, text="Container", padding=5)
            container_frame.pack(fill=tk.X, padx=5, pady=2)

            self.extension_var = tk.StringVar(value="mkv")
            for ext in ["mkv", "mp4"]:
                ttk.Radiobutton(container_frame, text=ext.upper(), variable=self.extension_var, value=ext).pack(side=tk.LEFT, padx=5)

            # Languages
            lang_frame = ttk.LabelFrame(left_frame, text="Keep Audio Languages", padding=5)
            lang_frame.pack(fill=tk.X, padx=5, pady=2)

            self.lang_var = tk.StringVar(value="eng jpn")
            ttk.Entry(lang_frame, textvariable=self.lang_var).pack(fill=tk.X)
            ttk.Label(lang_frame, text="Space-separated ISO 639-2 codes", font=("", 8)).pack(anchor=tk.W)

            # Options
            opts_frame = ttk.LabelFrame(left_frame, text="Options", padding=5)
            opts_frame.pack(fill=tk.X, padx=5, pady=2)

            self.dry_run_var = tk.BooleanVar(value=False)
            self.rename_var = tk.BooleanVar(value=False)
            self.norename_var = tk.BooleanVar(value=False)
            self.verbose_var = tk.BooleanVar(value=False)
            self.sub_convert_var = tk.BooleanVar(value=False)
            self.force_var = tk.BooleanVar(value=False)

            ttk.Checkbutton(opts_frame, text="Dry Run (preview only)", variable=self.dry_run_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Rename Only (no re-encode)", variable=self.rename_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Keep Original Filename", variable=self.norename_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Convert Subtitles", variable=self.sub_convert_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Force Re-process", variable=self.force_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Verbose Output", variable=self.verbose_var).pack(anchor=tk.W)

            # Buttons
            btn_frame = ttk.Frame(left_frame, padding=5)
            btn_frame.pack(fill=tk.X, padx=5, pady=5)

            self.run_btn = ttk.Button(btn_frame, text="Run", command=self._run)
            self.run_btn.pack(fill=tk.X, pady=(0, 3))

            self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop, state=tk.DISABLED)
            self.stop_btn.pack(fill=tk.X)

            # ---- RIGHT: Output Panel ----
            right_frame = ttk.Frame(main_pane)
            main_pane.add(right_frame, weight=2)

            ttk.Label(right_frame, text="Output", font=("", 10, "bold")).pack(anchor=tk.W, padx=5, pady=(5, 0))

            self.output_text = scrolledtext.ScrolledText(
                right_frame, wrap=tk.WORD, font=("Consolas", 9),
                state=tk.DISABLED, bg="#1e1e1e", fg="#cccccc", insertbackground="#cccccc"
            )
            self.output_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

            ttk.Button(right_frame, text="Clear Output", command=self._clear_output).pack(anchor=tk.E, padx=5, pady=(0, 5))

            self._stop_flag = False

        def _browse(self):
            if self.mode_var.get() == "folder":
                path = filedialog.askdirectory(initialdir=self.path_var.get() or os.getcwd())
            else:
                path = filedialog.askopenfilename(
                    initialdir=os.path.dirname(self.path_var.get()) or os.getcwd(),
                    filetypes=[("Video files", " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)), ("All files", "*.*")]
                )
            if path:
                self.path_var.set(path)

        def _browse_output(self):
            path = filedialog.askdirectory(initialdir=self.output_var.get() or os.getcwd())
            if path:
                self.output_var.set(path)

        def _log(self, text):
            self.output_text.configure(state=tk.NORMAL)
            self.output_text.insert(tk.END, text + "\n")
            self.output_text.see(tk.END)
            self.output_text.configure(state=tk.DISABLED)

        def _clear_output(self):
            self.output_text.configure(state=tk.NORMAL)
            self.output_text.delete("1.0", tk.END)
            self.output_text.configure(state=tk.DISABLED)

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
                    self._log(f"No video files found in {path}")
                    return
            else:
                if not os.path.isfile(path):
                    self._log(f"File not found: {path}")
                    return
                files = [path]

            languages = self.lang_var.get().strip().split()
            if not languages:
                languages = list(DEFAULT_KEEP_LANGUAGES)

            output_dir = self.output_var.get().strip() or None

            self.processing = True
            self._stop_flag = False
            self.run_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)

            def print_fn(msg):
                self.root.after(0, self._log, msg)

            def worker():
                try:
                    for f in files:
                        if self._stop_flag:
                            print_fn("--- Stopped by user ---")
                            break
                        process_file(
                            f,
                            extension=self.extension_var.get(),
                            dry_run=self.dry_run_var.get(),
                            rename=self.rename_var.get(),
                            verbose=self.verbose_var.get(),
                            subtitle_convert=self.sub_convert_var.get(),
                            norename=self.norename_var.get(),
                            convert_force=self.force_var.get(),
                            output_dir=output_dir,
                            keep_languages=languages,
                            print_fn=print_fn,
                        )
                    if not self._stop_flag:
                        print_fn("\n=== All files processed ===")
                except Exception as e:
                    print_fn(f"ERROR: {e}")
                finally:
                    self.root.after(0, self._processing_done)

            threading.Thread(target=worker, daemon=True).start()

        def _stop(self):
            self._stop_flag = True

        def _processing_done(self):
            self.processing = False
            self.run_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)

    root = tk.Tk()
    VideoStandardizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
