import subprocess
import json
import re
import os
import argparse
import sys
import pprint

pp = pprint.PrettyPrinter(indent=4)


def get_streams_info(input_file, stream_type):
    cmd = ['ffprobe', '-v', 'quiet', '-select_streams', stream_type,
           '-show_entries', 'stream=index,codec_name,bit_rate,channels,bits_per_raw_sample:stream_tags=language', '-print_format', 'json', input_file]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    output = json.loads(result.stdout)
    if not 'streams' in output or not len(output['streams']):
        return []
    offset = output['streams'][0]['index']
    all_streams = []
    english_streams = []
    no_608 = []
    max_channels_stream = None
    max_channels = 0
    for stream in output['streams']:
        channels = stream.get('channels', 0)
        if stream_type == 's' and (not 'tags' in stream or not 'language' in stream['tags'] or stream['tags']['language'] == 'eng' or stream['tags']['language'] == 'und'):
            all_streams.append(stream['index'] - offset)
            if stream['tags'] and stream['tags']['language'] == 'eng':
                english_streams.append(stream['index'] - offset)
                if stream['codec_name'] != 'eia_608':
                    no_608.append(stream['index'] - offset)
        elif channels > max_channels and (not 'tags' in stream or not 'language' in stream['tags'] or stream['tags']['language'] == 'eng' or stream['tags']['language'] == 'und'):
            max_channels = channels
            max_channels_stream = stream['index'] - offset
    if stream_type == 's':
        if len(english_streams):
            if len(no_608):
                return no_608
            else:
                return english_streams
        else:
            return all_streams
    else:
        return [max_channels_stream] if max_channels_stream is not None else []

def get_file_info(input_file):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height,codec_name,codec_type:format', '-print_format', 'json', input_file]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    output = json.loads(result.stdout)
    return output

def get_resolution(file_info):
    for stream in file_info.get('streams', []):
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

def get_bitrate(file_info):
    if 'format' in file_info and 'bit_rate' in file_info['format']:
        return int(file_info['format']['bit_rate']) / 1000000
    return None


def get_encoding(file_info):
    for stream in file_info.get('streams', []):
        if stream.get('codec_type') == 'video':
            return stream.get('codec_name', '').upper()
    return None
    

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
        filename = 'S' + season + 'E' + episode
        filename = filename.upper()
        if match.group(4):
            filename_no_ext, _ = os.path.splitext(match.group(4))
            filename += filename_no_ext
    else:
        newfilename, _ = os.path.splitext(os.path.basename(input_file))
        filename = os.path.join(directory, f"{newfilename}")

    pattern = r'\[\w+ \d+Mbps \w+\].\w+$'
    if not convert_force and re.search(pattern, input_file):
        return False
        
    file_info = get_file_info(input_file)
    if dry_run and verbose:
        pp.pprint(file_info)
        print('\n')
    resolution = get_resolution(file_info)
    bitrate = get_bitrate(file_info)
    encoding = get_encoding(file_info)
    hasInfo = resolution or bitrate or encoding
    if hasInfo:
        filename += " ["
    if resolution:
        filename += f"{resolution}"
    if bitrate:
        filename += f" {round(bitrate)}Mbps"
    if encoding:
        filename += f" {encoding}"
    if hasInfo: 
        filename += "]"

    filename += f".{extension}"
    return os.path.join(directory, filename) if match else filename


def get_supported_subtitle_codecs(container):
    if container == 'mkv':
        return ['srt', 'ass', 'ssa', 'vtt', 'hdmv_pgs_subtitle', 'dvd_subtitle']
    elif container == 'mp4':
        return ['mov_text']
    else:
        return []

def ffmpegConversion(file, extension="mkv", dry_run=False, rename=False, verbose=False, subtitle_convert=False, norename=False, convert_force=False, output=False, subtitle_only=False):
    output_file = extract_filename(file, extension, dry_run, verbose, norename, convert_force, output)
    if not output_file:
        print(f"Skipping {file} as it is already processed\n")
        return

    audio_streams = get_streams_info(file, 'a')
    subtitle_streams = get_streams_info(file, 's')

    input_file_info = get_file_info(file)
    input_audio_streams = [stream for stream in input_file_info['streams'] if stream['codec_type'] == 'audio']
    input_subtitle_streams = [stream for stream in input_file_info['streams'] if stream['codec_type'] == 'subtitle']

    # Check for subtitle files
    base_file_name = os.path.splitext(file)[0]
    subtitle_file = None
    if os.path.exists(base_file_name + '.en.srt'):
        subtitle_file = base_file_name + '.en.srt'
    elif os.path.exists(base_file_name + '.eng.srt'):
        subtitle_file = base_file_name + '.eng.srt'
    elif os.path.exists(base_file_name + '.srt'):
        subtitle_file = base_file_name + '.srt'
    elif os.path.exists(base_file_name + '.sub'):
        subtitle_file = base_file_name + '.sub'

    if not convert_force and (rename or (len(audio_streams) == len(input_audio_streams) and len(subtitle_streams) == len(input_subtitle_streams) and not subtitle_file )):
        original_extension = os.path.splitext(file)[1]
        output_file_with_original_extension = os.path.splitext(output_file)[0] + original_extension
        if not dry_run:
            os.rename(file, output_file_with_original_extension)
            print(f"Renamed {file} to {output_file_with_original_extension}\n")
            return
        else:
            print(f"Will rename {file} to {output_file_with_original_extension}\n")
            return

    supported = get_supported_subtitle_codecs(extension)
    for index in subtitle_streams:
        subtitle_codec = input_subtitle_streams[index]['codec_name']
        print(subtitle_codec)
        if subtitle_codec not in supported:
            print(f"Subtitle codec {subtitle_codec} is not supported for {extension} container\n")
            subtitle_convert = supported[0]
            break
        
    # Build base ffmpeg command and handle subtitle conversion/copy carefully.
    # We avoid using a global copy codec which would apply to subtitles and cause
    # multiple -c options for the same stream. Instead, set `-c:v copy` and
    # `-c:a copy` per audio/video stream and only set `-c:s` when conversion is
    # valid (text->text or bitmap->bitmap).
    base_cmd = ['ffmpeg', '-i', file]

    if subtitle_file and not len(subtitle_streams):
        # Add external subtitle file as a second input
        base_cmd.extend(['-i', subtitle_file])
        print(f"Subtitle file {subtitle_file} will be added\n")

    # Always map first video stream and copy video
    cmd = list(base_cmd)
    cmd.extend(['-map', '0:v:0', '-c:v', 'copy'])

    if subtitle_only:
        for audio_index in range(len(input_audio_streams)):
            metadata_option = f'-metadata:s:a:{audio_index}'
            cmd.extend(['-map', f'0:a:{audio_index}', '-c:a', 'copy'])
    else:
        for audio_index in audio_streams:
            metadata_option = f'-metadata:s:a:{audio_index}'
            cmd.extend(['-map', f'0:a:{audio_index}', metadata_option, 'language=eng', '-c:a', 'copy'])
            
    # Handle subtitle streams: decide whether to copy or convert per-stream.
    for subtitle_index in subtitle_streams:
        metadata_option = f'-metadata:s:s:{subtitle_index}'
        # Inspect input subtitle codec
        subtitle_codec = input_subtitle_streams[subtitle_index]['codec_name']
        # If subtitle_convert is requested, check compatibility. ffmpeg only
        # supports text->text and bitmap->bitmap subtitle encoding. If the
        # requested conversion would cross types, just copy the subtitle stream
        # instead of attempting invalid re-encoding.
        if subtitle_convert:
            # Determine rough type by codec name: treat known text codecs as text
            text_codecs = {'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'vtt'}
            bitmap_codecs = {'hdmv_pgs_subtitle', 'dvd_subtitle'}
            src_is_text = subtitle_codec in text_codecs
            src_is_bitmap = subtitle_codec in bitmap_codecs
            dest_is_text = subtitle_convert in text_codecs
            dest_is_bitmap = subtitle_convert in bitmap_codecs

            if (src_is_text and dest_is_text) or (src_is_bitmap and dest_is_bitmap):
                # valid conversion
                cmd.extend(['-map', f'0:s:{subtitle_index}', metadata_option, 'language=eng', '-c:s', subtitle_convert])
            else:
                # incompatible conversion: copy the stream instead
                cmd.extend(['-map', f'0:s:{subtitle_index}', metadata_option, 'language=eng', '-c:s', 'copy'])
        else:
            # No conversion requested: copy subtitle stream
            cmd.extend(['-map', f'0:s:{subtitle_index}', metadata_option, 'language=eng', '-c:s', 'copy'])

    cmd.append(output_file)

    print(f"Video, English audio tracks: ({audio_streams}) and English subtitle tracks: ({subtitle_streams}) will be copied to {output_file}\n")

    if not dry_run:
        subprocess.run(cmd)


def main():

    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.completion import PathCompleter
    except ImportError:
        print("Installing prompt_toolkit...\n")
        subprocess.check_call([sys.executable, "-m", "pip", "install", 'prompt_toolkit'])
        print("Please run the script again.")

    parser = argparse.ArgumentParser(description="Process files in a folder.")
    parser.add_argument("-f", "--folder", help="Folder path containing the files to process")
    parser.add_argument("-i", "--input", help="Single input file name")
    parser.add_argument("-d", "--dry-run", action='store_true', help="Debug: print the command instead of executing it")
    parser.add_argument("-e", "--extension", help="Output file extension", default='mkv')
    parser.add_argument("-r", "--rename", action='store_true', help="Just rename the files without re-encoding")
    parser.add_argument("-v", "--verbose", action='store_true', help="Print more information")
    parser.add_argument("-s", '--subtitle-convert', action='store_true', help="Convert subtitles to srt")
    parser.add_argument("-n", "--norename", action="store_true", help="Don't rename")
    parser.add_argument("-c", "--convert-force", action="store_true", help="Convert file even if it is already processed")
    parser.add_argument("-o", "--output", help="Output folder path")
    parser.add_argument("-so", "--subtitle-only", action="store_true", help="Only perform subtitle operations and leave audio untouched")
    parser.add_argument("--gui", action="store_true", help="Launch GUI")

    args = parser.parse_args()

    if args.gui:
        import tkinter as tk
        from gui import VideoStandardizerGUI
        root = tk.Tk()
        app = VideoStandardizerGUI(root)
        root.mainloop()
        return

    dry_run = args.dry_run
    if args.folder and args.input:
        print("Specify only one option: -f or -i.")
        return
    elif args.folder:
        folder_path = args.folder
        if not os.path.isdir(folder_path):
            print("Invalid folder path.")
            return
        files = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f)) and (f.endswith('.mkv') or f.endswith('.m4v') or f.endswith('.mp4') or f.endswith('.ts') or f.endswith('.mov') or f.endswith('.mpg') or f.endswith('.avi') or f.endswith('.flv'))]
    elif args.input:
        files = [args.input]
    else:
        input_file = prompt("Enter input file name (with extension): ", completer=PathCompleter())
        files = [input_file]
    
    for file in files:
        ffmpegConversion(file, args.extension, dry_run, args.rename, args.verbose, args.subtitle_convert, args.norename, args.convert_force, args.output, args.subtitle_only)
      

if __name__ == "__main__":
    main()
