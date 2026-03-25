import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import subprocess
import json
import re
import threading

def get_file_info(input_file):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height,codec_name,codec_type,channels:format', '-print_format', 'json', input_file]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    output = json.loads(result.stdout)
    return output

def get_resolution(file_info):
    for stream in file_info.get('streams', []):
        if stream.get('codec_type') == 'video':
            width = stream.get('width', 0)
            height = stream.get('height', 0)
            if width >= 3000 or height >= 1800:
                return "4K"
            elif width >= 1800 or height >= 1000:
                return "HD"
            else:
                return "SD"
    return "Unknown"

def get_bitrate(file_info):
    if 'format' in file_info and 'bit_rate' in file_info['format']:
        return int(file_info['format']['bit_rate']) / 1000000
    return None

def get_encoding(file_info):
    for stream in file_info.get('streams', []):
        if stream.get('codec_type') == 'video':
            return stream.get('codec_name', '').upper()
    return "Unknown"

def extract_filename(input_file, extension, dry_run=False, verbose=False, norename=False, convert_force=False, output=False):
    if output:
        directory = os.path.join(os.path.dirname(input_file), 'out')
    else:
        directory = os.path.dirname(input_file)

    if not os.path.exists(directory):
        os.makedirs(directory)

    if norename:
        filename, _ = os.path.splitext(os.path.basename(input_file))
        return os.path.join(directory, f"{filename}.{extension}")

    match = re.search(r'([Ss](\d{1,2})[Ee](\d{1,2}))( - .*)?', input_file)
    if match:
        season = match.group(2).zfill(2)
        episode = match.group(3).zfill(2)
        filename = f"S{season}E{episode}".upper()
        if match.group(4):
            filename_no_ext, _ = os.path.splitext(match.group(4))
            filename += filename_no_ext
    else:
        filename, _ = os.path.splitext(os.path.basename(input_file))

    pattern = r'\[\w+ \d+Mbps \w+\].\w+$'
    if not convert_force and re.search(pattern, input_file):
        return False

    file_info = get_file_info(input_file)
    resolution = get_resolution(file_info)
    bitrate = get_bitrate(file_info)
    encoding = get_encoding(file_info)
    has_info = resolution != "Unknown" or bitrate or encoding != "Unknown"
    if has_info:
        filename += " ["
        if resolution != "Unknown":
            filename += resolution
        if bitrate:
            filename += f" {round(bitrate)}Mbps"
        if encoding != "Unknown":
            filename += f" {encoding}"
        filename += "]"

    filename += f".{extension}"
    return os.path.join(directory, filename) if match else filename

def get_supported_subtitle_codecs(container):
    if container == 'mkv':
        return ['srt', 'ass', 'ssa', 'vtt', 'hdmv_pgs_subtitle', 'dvd_subtitle']
    elif container == 'mp4':
        return ['mov_text']
    return []

def ffmpegConversion(file, extension="mkv", dry_run=False, rename=False, verbose=False, subtitle_convert=False, norename=False, convert_force=False, output=False, subtitle_only=False, audio_stream=None, subtitle_streams=None, progress_callback=None, gui=False):
    output_file = extract_filename(file, extension, dry_run, verbose, norename, convert_force, output)
    if not output_file:
        if not gui:
            print(f"Skipping {file} as it is already processed\n")
        return output_file

    input_file_info = get_file_info(file)
    input_audio_streams = [stream for stream in input_file_info['streams'] if stream['codec_type'] == 'audio']
    input_subtitle_streams = [stream for stream in input_file_info['streams'] if stream['codec_type'] == 'subtitle']

    if subtitle_only:
        audio_streams_to_use = list(range(len(input_audio_streams)))
    else:
        audio_streams_to_use = [audio_stream] if audio_stream is not None else list(range(len(input_audio_streams)))

    subtitle_streams_to_use = subtitle_streams if subtitle_streams is not None else list(range(len(input_subtitle_streams)))

    base_file_name = os.path.splitext(file)[0]
    subtitle_file = next((base_file_name + ext for ext in ['.en.srt', '.eng.srt', '.srt', '.sub'] if os.path.exists(base_file_name + ext)), None)

    if not convert_force and (rename or (len(audio_streams_to_use) == len(input_audio_streams) and len(subtitle_streams_to_use) == len(input_subtitle_streams) and not subtitle_file)):
        original_extension = os.path.splitext(file)[1]
        output_file_with_original_extension = os.path.splitext(output_file)[0] + original_extension
        if not dry_run:
            os.rename(file, output_file_with_original_extension)
            if not gui:
                print(f"Renamed {file} to {output_file_with_original_extension}\n")
        return output_file_with_original_extension

    supported = get_supported_subtitle_codecs(extension)
    for index in subtitle_streams_to_use:
        if index >= len(input_subtitle_streams):
            continue
        subtitle_codec = input_subtitle_streams[index]['codec_name']
        if subtitle_codec not in supported:
            if not gui:
                print(f"Subtitle codec {subtitle_codec} not supported for {extension}\n")
            subtitle_convert = supported[0]
            break

    cmd = ['ffmpeg', '-i', file, '-map', '0:v:0', '-c', 'copy']
    if subtitle_file and not subtitle_streams_to_use:
        cmd.extend(['-i', subtitle_file, '-map', '1:s', '-metadata:s:s:0', 'language=eng'])
    elif file.lower().endswith('.ts'):
        cmd.extend(['-f', 'mkv'])
    elif subtitle_convert:
        cmd.extend(['-c:s', subtitle_convert])

    for audio_index in audio_streams_to_use:
        cmd.extend(['-map', f'0:a:{audio_index}', f'-metadata:s:a:{audio_index}', 'language=eng'])

    for subtitle_index in subtitle_streams_to_use:
        cmd.extend(['-map', f'0:s:{subtitle_index}', f'-metadata:s:s:{subtitle_index}', 'language=eng'])

    cmd.append(output_file)
    if not gui:
        print(f"Copying video, audio tracks: {audio_streams_to_use}, subtitles: {subtitle_streams_to_use} to {output_file}\n")

    if not dry_run:
        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True)
        duration = float(input_file_info['format'].get('duration', 0))
        while process.poll() is None:
            line = process.stderr.readline()
            match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
            if match and duration and progress_callback:
                time = sum(float(x) * 60 ** i for i, x in enumerate(reversed(match.group(1).split(':'))))
                progress_callback(time / duration * 100)
            root.update()  # Keep GUI responsive
        process.wait()
    return output_file

class VideoStandardizerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Standardizer")
        self.streams_loaded = False
        self.create_widgets()

    def create_widgets(self):
        tk.Label(self.root, text="Input (file or folder):").grid(row=0, column=0, padx=10, pady=10)
        self.input_entry = tk.Entry(self.root, width=50)
        self.input_entry.grid(row=0, column=1, padx=10, pady=10)
        tk.Button(self.root, text="Browse", command=self.browse_input).grid(row=0, column=2, padx=10, pady=10)

        self.audio_label = tk.Label(self.root, text="Audio Stream:")
        self.audio_combobox = ttk.Combobox(self.root, state="readonly")
        self.audio_combobox.bind("<<ComboboxSelected>>", lambda e: self.audio_combobox.selection_clear())

        self.subtitle_label = tk.Label(self.root, text="Subtitle Streams (multiple selection):")
        self.subtitle_listbox = tk.Listbox(self.root, selectmode="multiple", height=5, selectbackground="lightblue")

        tk.Label(self.root, text="Output Extension (default: mkv):").grid(row=5, column=0, padx=10, pady=10)
        self.extension_entry = tk.Entry(self.root, width=10)
        self.extension_entry.grid(row=5, column=1, padx=10, pady=10, sticky="w")

        self.dry_run_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Dry Run (print commands)", variable=self.dry_run_var).grid(row=6, column=0, padx=10, pady=5, sticky="w")
        self.rename_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Rename Only", variable=self.rename_var).grid(row=6, column=1, padx=10, pady=5, sticky="w")
        self.verbose_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Verbose Output", variable=self.verbose_var).grid(row=6, column=2, padx=10, pady=5, sticky="w")
        self.subtitle_convert_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Convert Subtitles to SRT", variable=self.subtitle_convert_var).grid(row=7, column=0, padx=10, pady=5, sticky="w")
        self.norename_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Keep Original Name", variable=self.norename_var).grid(row=7, column=1, padx=10, pady=5, sticky="w")
        self.convert_force_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Force Conversion", variable=self.convert_force_var).grid(row=7, column=2, padx=10, pady=5, sticky="w")
        self.subtitle_only_var = tk.BooleanVar()
        tk.Checkbutton(self.root, text="Subtitles Only", variable=self.subtitle_only_var).grid(row=8, column=0, padx=10, pady=5, sticky="w")

        tk.Button(self.root, text="Start", command=lambda: threading.Thread(target=self.start_conversion).start(), bg="green", fg="white").grid(row=9, column=0, padx=10, pady=10, sticky="e")
        tk.Button(self.root, text="Reset", command=self.reset_fields, bg="red", fg="white").grid(row=9, column=1, padx=10, pady=10, sticky="w")

        self.status_label = tk.Label(self.root, text="Status: Ready")
        self.status_label.grid(row=10, column=0, columnspan=4, padx=10, pady=5, sticky="w")
        self.progress = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.root, variable=self.progress, maximum=100)
        self.progress_bar.grid(row=11, column=0, columnspan=4, padx=10, pady=5, sticky="we")

        self.output_file_label = tk.Label(self.root, text="Output File: N/A")
        self.output_file_label.grid(row=12, column=0, columnspan=4, padx=10, pady=5, sticky="w")

    def browse_input(self):
        path = filedialog.askopenfilename(filetypes=[("Video files", "*.mkv *.m4v *.mp4 *.ts *.mov *.mpg *.avi *.flv")])
        if path:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, path)
            self.load_streams(path)

    def load_streams(self, path):
        if os.path.isfile(path):
            try:
                file_info = get_file_info(path)
                audio_streams = [s for s in file_info['streams'] if s['codec_type'] == 'audio']
                subtitle_streams = [s for s in file_info['streams'] if s['codec_type'] == 'subtitle']

                audio_options = [f"{s.get('codec_name', 'unknown')} ({s.get('tags', {}).get('language', 'unknown')}, {s.get('channels', 0)} channels)" for s in audio_streams]
                self.audio_combobox['values'] = audio_options
                self.audio_combobox.current(0) if audio_options else None

                self.subtitle_listbox.delete(0, tk.END)
                for s in subtitle_streams:
                    self.subtitle_listbox.insert(tk.END, f"{s.get('codec_name', 'unknown')} ({s.get('tags', {}).get('language', 'unknown')})")

                output_file = ffmpegConversion(path, self.extension_entry.get() or "mkv", dry_run=True, rename=self.rename_var.get(), verbose=self.verbose_var.get(), subtitle_convert=self.subtitle_convert_var.get(), norename=self.norename_var.get(), convert_force=self.convert_force_var.get(), output=False, subtitle_only=self.subtitle_only_var.get(), gui=True)
                self.output_file_label.config(text=f"Output File: {output_file or 'N/A'}")

                max_channels = -1
                default_audio_idx = 0
                for i, stream in enumerate(audio_streams):
                    if stream.get('tags', {}).get('language', 'unknown') in ['eng', 'unknown']:
                        channels = stream.get('channels', 0)
                        if channels > max_channels:
                            max_channels = channels
                            default_audio_idx = i
                self.audio_combobox.current(default_audio_idx)

                eng_subs = [i for i, s in enumerate(subtitle_streams) if s.get('tags', {}).get('language') == 'eng']
                no_608 = [i for i in eng_subs if s['codec_name'] != 'eia_608']
                selected = no_608 or eng_subs or range(len(subtitle_streams))
                for i in selected:
                    self.subtitle_listbox.select_set(i)

                self.audio_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=10, pady=5)
                self.audio_combobox.grid(row=2, column=0, columnspan=4, sticky="we", padx=10, pady=5)
                self.subtitle_label.grid(row=3, column=0, columnspan=4, sticky="w", padx=10, pady=5)
                self.subtitle_listbox.grid(row=4, column=0, columnspan=4, sticky="we", padx=10, pady=5)
                self.streams_loaded = True
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load streams: {e}")

    def start_conversion(self):
        path = self.input_entry.get()
        if not path:
            messagebox.showerror("Error", "Please specify a file or folder.")
            return

        files = [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f)) and f.lower().endswith(('.mkv', '.m4v', '.mp4', '.ts', '.mov', '.mpg', '.avi', '.flv'))] if os.path.isdir(path) else [path]
        total_files = len(files)

        for idx, file in enumerate(files):
            audio_idx = self.audio_combobox.current() if file == path and self.streams_loaded else None
            subtitle_idxs = self.subtitle_listbox.curselection() if file == path and self.streams_loaded else None

            output_file = ffmpegConversion(file, self.extension_entry.get() or "mkv", dry_run=True, rename=self.rename_var.get(), verbose=self.verbose_var.get(), subtitle_convert=self.subtitle_convert_var.get(), norename=self.norename_var.get(), convert_force=self.convert_force_var.get(), output=False, subtitle_only=self.subtitle_only_var.get(), gui=True)
            self.output_file_label.config(text=f"Output File: {output_file or 'N/A'}")
            self.status_label.config(text=f"Status: Converting {os.path.basename(file)} ({idx + 1}/{total_files})")

            def update_progress(file_progress):
                total_progress = ((idx + file_progress / 100) / total_files) * 100
                self.progress.set(total_progress)
                self.root.update()

            ffmpegConversion(file, self.extension_entry.get() or "mkv", self.dry_run_var.get(), self.rename_var.get(), self.verbose_var.get(), self.subtitle_convert_var.get(), self.norename_var.get(), self.convert_force_var.get(), False, self.subtitle_only_var.get(), audio_idx, subtitle_idxs, update_progress if not self.dry_run_var.get() else None, gui=True)

        self.status_label.config(text="Status: Conversion Completed")
        self.progress.set(100)
        messagebox.showinfo("Info", "Conversion completed.")

    def reset_fields(self):
        self.input_entry.delete(0, tk.END)
        self.extension_entry.delete(0, tk.END)
        self.dry_run_var.set(False)
        self.rename_var.set(False)
        self.verbose_var.set(False)
        self.subtitle_convert_var.set(False)
        self.norename_var.set(False)
        self.convert_force_var.set(False)
        self.subtitle_only_var.set(False)
        self.audio_label.grid_remove()
        self.audio_combobox.grid_remove()
        self.subtitle_label.grid_remove()
        self.subtitle_listbox.grid_remove()
        self.streams_loaded = False
        self.status_label.config(text="Status: Ready")
        self.progress.set(0)
        self.output_file_label.config(text="Output File: N/A")

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoStandardizerGUI(root)
    root.mainloop()