import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
from video_standardizer import ffmpegConversion

class VideoStandardizerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Standardizer")

        self.create_widgets()

    def create_widgets(self):
        self.folder_label = tk.Label(self.root, text="Folder path containing the files to process:")
        self.folder_label.grid(row=0, column=0, padx=10, pady=10)
        self.folder_entry = tk.Entry(self.root, width=50)
        self.folder_entry.grid(row=0, column=1, padx=10, pady=10)
        self.folder_button = tk.Button(self.root, text="Browse", command=self.browse_folder)
        self.folder_button.grid(row=0, column=2, padx=10, pady=10)

        self.file_label = tk.Label(self.root, text="Single input file name:")
        self.file_label.grid(row=1, column=0, padx=10, pady=10)
        self.file_entry = tk.Entry(self.root, width=50)
        self.file_entry.grid(row=1, column=1, padx=10, pady=10)
        self.file_button = tk.Button(self.root, text="Browse", command=self.browse_file)
        self.file_button.grid(row=1, column=2, padx=10, pady=10)

        self.extension_label = tk.Label(self.root, text="Output file extension (default: mkv):")
        self.extension_label.grid(row=2, column=0, padx=10, pady=10)
        self.extension_entry = tk.Entry(self.root, width=10)
        self.extension_entry.grid(row=2, column=1, padx=10, pady=10, sticky="w")

        self.dry_run_var = tk.BooleanVar()
        self.dry_run_check = tk.Checkbutton(self.root, text="Debug: print the command instead of executing it", variable=self.dry_run_var)
        self.dry_run_check.grid(row=3, column=0, padx=10, pady=10, sticky="w")

        self.rename_var = tk.BooleanVar()
        self.rename_check = tk.Checkbutton(self.root, text="Just rename the files without re-encoding", variable=self.rename_var)
        self.rename_check.grid(row=3, column=1, padx=10, pady=10, sticky="w")

        self.verbose_var = tk.BooleanVar()
        self.verbose_check = tk.Checkbutton(self.root, text="Print more information", variable=self.verbose_var)
        self.verbose_check.grid(row=3, column=2, padx=10, pady=10, sticky="w")

        self.subtitle_convert_var = tk.BooleanVar()
        self.subtitle_convert_check = tk.Checkbutton(self.root, text="Convert subtitles to srt", variable=self.subtitle_convert_var)
        self.subtitle_convert_check.grid(row=4, column=0, padx=10, pady=10, sticky="w")

        self.norename_var = tk.BooleanVar()
        self.norename_check = tk.Checkbutton(self.root, text="Don't rename", variable=self.norename_var)
        self.norename_check.grid(row=4, column=1, padx=10, pady=10, sticky="w")

        self.convert_force_var = tk.BooleanVar()
        self.convert_force_check = tk.Checkbutton(self.root, text="Convert file even if it is already processed", variable=self.convert_force_var)
        self.convert_force_check.grid(row=4, column=2, padx=10, pady=10, sticky="w")

        self.subtitle_only_var = tk.BooleanVar()
        self.subtitle_only_check = tk.Checkbutton(self.root, text="Only perform subtitle operations and leave audio untouched", variable=self.subtitle_only_var)
        self.subtitle_only_check.grid(row=5, column=0, padx=10, pady=10, sticky="w")

        self.output_label = tk.Label(self.root, text="Output folder path:")
        self.output_label.grid(row=6, column=0, padx=10, pady=10)
        self.output_entry = tk.Entry(self.root, width=50)
        self.output_entry.grid(row=6, column=1, padx=10, pady=10)
        self.output_button = tk.Button(self.root, text="Browse", command=self.browse_output_folder)
        self.output_button.grid(row=6, column=2, padx=10, pady=10)

        self.start_button = tk.Button(self.root, text="Start", command=self.start_conversion, bg="green", fg="white")
        self.start_button.grid(row=7, column=0, padx=10, pady=10, sticky="e")

        self.reset_button = tk.Button(self.root, text="Reset", command=self.reset_fields, bg="red", fg="white")
        self.reset_button.grid(row=7, column=1, padx=10, pady=10, sticky="w")

        self.status_label = tk.Label(self.root, text="Status: Ready")
        self.status_label.grid(row=8, column=0, columnspan=3, padx=10, pady=10, sticky="w")

        self.progress = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.root, variable=self.progress, maximum=100)
        self.progress_bar.grid(row=9, column=0, columnspan=3, padx=10, pady=10, sticky="we")

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_entry.delete(0, tk.END)
            self.folder_entry.insert(0, folder)

    def browse_file(self):
        file = filedialog.askopenfilename(filetypes=[("Video files", "*.mkv *.m4v *.mp4 *.ts *.mov *.mpg *.avi *.flv")])
        if file:
            self.file_entry.delete(0, tk.END)
            self.file_entry.insert(0, file)

    def browse_output_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)

    def start_conversion(self):
        folder = self.folder_entry.get()
        file = self.file_entry.get()
        extension = self.extension_entry.get() or "mkv"
        dry_run = self.dry_run_var.get()
        rename = self.rename_var.get()
        verbose = self.verbose_var.get()
        subtitle_convert = self.subtitle_convert_var.get()
        norename = self.norename_var.get()
        convert_force = self.convert_force_var.get()
        subtitle_only = self.subtitle_only_var.get()
        output = self.output_entry.get()

        if folder and file:
            messagebox.showerror("Error", "Specify only one option: folder or file.")
            return

        if not folder and not file:
            messagebox.showerror("Error", "Please specify a folder or a file.")
            return

        files = []
        if folder:
            if not os.path.isdir(folder):
                messagebox.showerror("Error", "Invalid folder path.")
                return
            files = [os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f)) and (f.endswith('.mkv') or f.endswith('.m4v') or f.endswith('.mp4') or f.endswith('.ts') or f.endswith('.mov') or f.endswith('.mpg') or f.endswith('.avi') or f.endswith('.flv'))]
        elif file:
            files = [file]

        total_files = len(files)
        for index, file in enumerate(files):
            self.status_label.config(text=f"Status: Converting {file}")
            self.progress.set((index + 1) / total_files * 100)
            self.root.update_idletasks()
            ffmpegConversion(file, extension, dry_run, rename, verbose, subtitle_convert, norename, convert_force, output, subtitle_only)

        self.status_label.config(text="Status: Conversion completed.")
        messagebox.showinfo("Info", "Conversion completed.")

    def reset_fields(self):
        self.folder_entry.delete(0, tk.END)
        self.file_entry.delete(0, tk.END)
        self.extension_entry.delete(0, tk.END)
        self.dry_run_var.set(False)
        self.rename_var.set(False)
        self.verbose_var.set(False)
        self.subtitle_convert_var.set(False)
        self.norename_var.set(False)
        self.convert_force_var.set(False)
        self.subtitle_only_var.set(False)
        self.output_entry.delete(0, tk.END)
        self.status_label.config(text="Status: Ready")
        self.progress.set(0)

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoStandardizerGUI(root)
    root.mainloop()
