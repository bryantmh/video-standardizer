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
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = json.loads(result.stdout)
    if not 'streams' in output or not len(output['streams']):
        return []
    offset = output['streams'][0]['index']
    streams = []
    max_channels_stream = None
    max_channels = 0
    for stream in output['streams']:
        channels = stream.get('channels', 0)
        if stream_type == 's' and (not 'tags' in stream or not 'language' in stream['tags'] or stream['tags']['language'] == 'eng' or stream['tags']['language'] == 'und'):
            streams.append(stream['index'] - offset)
        elif channels > max_channels and (not 'tags' in stream or not 'language' in stream['tags'] or stream['tags']['language'] == 'eng' or stream['tags']['language'] == 'und'):
            max_channels = channels
            max_channels_stream = stream['index'] - offset
    if stream_type == 's':
        return streams
    else:
        return [max_channels_stream] if max_channels_stream is not None else []

def get_file_info(input_file):
    cmd = ['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height,codec_name,codec_type:format', '-print_format', 'json', input_file]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = json.loads(result.stdout)
    return output

def get_resolution(file_info):
    if 'streams' not in file_info or len(file_info['streams']) == 0 or 'width' not in file_info['streams'][0] or 'height' not in file_info['streams'][0]:
        return None
    width = file_info['streams'][0]['width']
    height = file_info['streams'][0]['height']
    if width >= 3000 or height >= 1800:
        return "4K"
    elif width >= 1920 or height >= 1080:
        return "HD"
    else:
        return "SD"

def get_bitrate(file_info):
    if 'format' in file_info and 'bit_rate' in file_info['format']:
        return int(file_info['format']['bit_rate']) / 1000000
    return None


def get_encoding(file_info):
    if 'streams' not in file_info or len(file_info['streams']) == 0 or 'codec_name' not in file_info['streams'][0]:
        return None
    return file_info['streams'][0]['codec_name'].upper()
    

def extract_filename(input_file, extension, dry_run=False, verbose=False):
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
        filename = os.path.splitext(input_file)[0]
        
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
    directory = os.path.dirname(input_file)
    return os.path.join(directory, filename)


def ffmpegConversion(file, extension="mkv", dry_run=False, rename=False, verbose=False):
    output_file = extract_filename(file, extension, dry_run, verbose)

    audio_streams = get_streams_info(file, 'a')
    subtitle_streams = get_streams_info(file, 's')

    input_file_info = get_file_info(file)
    input_audio_streams = [stream for stream in input_file_info['streams'] if stream['codec_type'] == 'audio']
    input_subtitle_streams = [stream for stream in input_file_info['streams'] if stream['codec_type'] == 'subtitle']

    if rename or (len(audio_streams) == len(input_audio_streams) and len(subtitle_streams) == len(input_subtitle_streams)):
        if not dry_run:
            os.rename(file, output_file)
            print(f"Renamed {file} to {output_file}")
            return
        else:
            print(f"Will rename {file} to {output_file}")
            return

    # Check for subtitle files
    base_file_name = os.path.splitext(file)[0]
    subtitle_file = None
    if os.path.exists(base_file_name + '.en.srt'):
        subtitle_file = base_file_name + '.en.srt'
    elif os.path.exists(base_file_name + '.srt'):
        subtitle_file = base_file_name + '.srt'

    if subtitle_file:
        cmd = ['ffmpeg', '-i', file, '-i', subtitle_file, '-map', '0:v', '-c', 'copy', '-map', '1:s', '-metadata:s:s:0', 'language=eng']
        print(f"Subtitle file {subtitle_file} will be added\n")
    elif file.lower().endswith('.ts'):
        cmd = ['ffmpeg', '-i', file, '-map', '0:v', '-c', 'copy', '-f', 'mkv']
    else:
        cmd = ['ffmpeg', '-i', file, '-map', '0:v', '-c', 'copy']

    for audio_index in audio_streams:
        metadata_option = f'-metadata:s:a:{audio_index}'
        cmd.extend(['-map', f'0:a:{audio_index}', metadata_option, 'language=eng'])
    for subtitle_index in subtitle_streams:
        metadata_option = f'-metadata:s:s:{subtitle_index}'
        cmd.extend(['-map', f'0:s:{subtitle_index}', metadata_option, 'language=eng'])

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


    args = parser.parse_args()

    dry_run = args.dry_run
    if args.folder and args.input:
        print("Specify only one option: -f or -i.")
        return
    elif args.folder:
        folder_path = args.folder
        if not os.path.isdir(folder_path):
            print("Invalid folder path.")
            return
        files = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f)) and not f.endswith('.srt') and not f.endswith('.db')]
    elif args.input:
        files = [args.input]
    else:
        input_file = prompt("Enter input file name (with extension): ", completer=PathCompleter())
        files = [input_file]
    
    for file in files:
        ffmpegConversion(file, args.extension, dry_run, args.rename, args.verbose)
      

if __name__ == "__main__":
    main()
