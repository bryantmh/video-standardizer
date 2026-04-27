import subprocess
import sys
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


# ── Optional-feature dependency probe ────────────────────────────────────────
# ffmpeg / ffprobe are non-optional and assumed available. The GUI grays out
# controls whose optional deps are missing, with a tooltip explaining why.

def probe_optional_features():
    """Return a {feature: (available: bool, reason_if_missing: str)} dict."""
    result = {}

    # TVDB API key (config.env at repo root).
    try:
        config_env = os.path.join(_SCRIPT_DIR, 'config.env')
        api_key = ''
        if os.path.isfile(config_env):
            with open(config_env, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('apikey='):
                        api_key = line.split('=', 1)[1].strip()
                        break
        if api_key:
            result['tvdb'] = (True, '')
        else:
            result['tvdb'] = (
                False,
                'No TVDB API key found. Add apikey=... to config.env at the repo root.'
            )
    except Exception as e:
        result['tvdb'] = (False, f'TVDB probe failed: {e}')

    # Comskip executable + comskip.ini.
    comskip_exe = os.path.join(_SCRIPT_DIR, 'comskip_dst', 'comskip.exe')
    comskip_ini = os.path.join(_SCRIPT_DIR, 'comskip_dst', 'comskip.ini')
    if os.path.isfile(comskip_exe) and os.path.isfile(comskip_ini):
        result['comskip'] = (True, '')
    else:
        result['comskip'] = (
            False,
            f'Missing {comskip_exe} or {comskip_ini}. Install Comskip into comskip_dst/.'
        )

    # VideoReDo COM (TVSuite v6) — registry probe only; we don't start the app.
    # The ProgID is registered under HKEY_CLASSES_ROOT when VideoReDo is
    # installed and its COM server is registered.
    try:
        import winreg
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT, 'VideoReDo6.VideoReDoSilent'
            ):
                pass
            result['videoredo'] = (True, '')
        except FileNotFoundError:
            result['videoredo'] = (
                False,
                'VideoReDo TVSuite v6 COM not registered. '
                'Install it and run the registration step.'
            )
        except OSError as ce:
            result['videoredo'] = (
                False,
                f'VideoReDo TVSuite v6 COM probe failed: {ce}'
            )
    except ImportError:
        # winreg is part of the stdlib on Windows; missing == not on Windows.
        result['videoredo'] = (
            False,
            'VideoReDo COM is Windows-only.'
        )

    # VapourSynth + lsmas plugin (frame_dedupe).
    try:
        from vapoursynth import core as _core
        plugin_namespaces = {p.namespace for p in _core.plugins()}
        if 'lsmas' in plugin_namespaces:
            result['vapoursynth'] = (True, '')
        else:
            result['vapoursynth'] = (
                False,
                'VapourSynth installed but the lsmas plugin is missing. '
                'Install it via: vsrepo.py install lsmas'
            )
    except Exception as e:
        result['vapoursynth'] = (False, f'VapourSynth not available: {e}')

    return result


# Probe once at module import; the GUI uses these values to gray out controls.
OPTIONAL_FEATURES = probe_optional_features()


def _attach_tooltip(widget, text: str) -> None:
    """Attach a hover tooltip to a Tk widget. No-op if text is empty."""
    if not text:
        return
    try:
        import tkinter as tk_local
    except ImportError:
        return

    state = {'tip': None}

    def _show(*_args):
        if state['tip'] is not None or not text:
            return
        x = widget.winfo_rootx() + 12
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tip = tk_local.Toplevel(widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f'+{x}+{y}')
        # Wrap long text so the tip doesn't run off-screen.
        lbl = tk_local.Label(
            tip, text=text, justify='left',
            background='#ffffe0', relief='solid', borderwidth=1,
            padx=6, pady=3, wraplength=420,
        )
        lbl.pack()
        state['tip'] = tip

    def _hide(*_args):
        if state['tip'] is not None:
            state['tip'].destroy()
            state['tip'] = None

    widget.bind('<Enter>', _show, add='+')
    widget.bind('<Leave>', _hide, add='+')
    widget.bind('<ButtonPress>', _hide, add='+')


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


def _audio_rank_key(stream):
    """Rank an audio stream for 'best track' selection.

    Priority: channels, then bitrate. (Language is handled by grouping upstream.)
    Missing values sort lowest so a stream with real data beats an unknown one.
    """
    channels = stream.get('channels') or 0
    try:
        bitrate = int(stream.get('bit_rate') or 0)
    except (TypeError, ValueError):
        bitrate = 0
    return (channels, bitrate)


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
            best_idx, _ = max(lang_groups[lang], key=lambda x: _audio_rank_key(x[1]))
            selected.append((best_idx, lang, False))

    has_eng = any(s[1] == 'eng' for s in selected)
    for und_lang in ['und', 'unk', '']:
        if und_lang in lang_groups and not has_eng:
            best_idx, _ = max(lang_groups[und_lang], key=lambda x: _audio_rank_key(x[1]))
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
                elif width >= 1200 or height >= 700:
                    return "720p"
                elif width >= 700 or height >= 440:
                    return "480p"
                elif width >= 560 or height >= 320:
                    return "360p"
                else:
                    return "240p"
    return None


def get_bitrate(format_info):
    if format_info and 'bit_rate' in format_info:
        return int(format_info['bit_rate']) / 1000000
    return None


_ENCODING_ALIASES = {
    'MPEG2VIDEO': 'MP2',
}


def get_encoding(streams):
    for stream in streams:
        if stream.get('codec_type') == 'video':
            name = stream.get('codec_name', '').upper()
            return _ENCODING_ALIASES.get(name, name)
    return None


def extract_episode_info(filename, keep_suffix=False):
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
                if suffix.startswith(' - '):
                    pass  # already properly formatted
                elif keep_suffix:
                    title = suffix.lstrip(' ._-')
                    suffix = f' - {title}' if title else ''
                else:
                    suffix = ''
            return tag, suffix, match
    return None, None, None


def build_output_filename(input_file, extension, streams, format_info,
                          norename=False, convert_force=False, output_dir=None,
                          keep_suffix=False, tvdb_changes=None):
    directory = output_dir if output_dir else (os.path.dirname(input_file) or '.')

    if not os.path.exists(directory):
        os.makedirs(directory)

    basename = os.path.basename(input_file)

    pattern = r'\[\w+ [\d.]+Mbps \w+\]\.\w+$'
    has_tvdb_changes = any(v is not None for v in (tvdb_changes or {}).values())
    if not convert_force and not has_tvdb_changes and re.search(pattern, basename):
        return None

    if norename:
        name_no_ext = os.path.splitext(basename)[0]
        return os.path.join(directory, f"{name_no_ext}.{extension}")

    tc = tvdb_changes or {}

    # Movie mode: TVDB gave us a canonical title — replace the filename base
    # entirely and skip any SxxExx handling.
    movie_title = tc.get('movie_title')

    if movie_title:
        filename = movie_title
    else:
        # Use TVDB sxxexx if provided and it looks like a valid SxxExx tag,
        # otherwise extract from filename
        _sxxexx_pattern = re.compile(r'^[Ss]\d{1,2}(?:[Ee]\d{1,3})+$')
        raw_sxxexx = tc.get('sxxexx') or ''
        valid_sxxexx = raw_sxxexx if _sxxexx_pattern.match(raw_sxxexx) else ''

        if valid_sxxexx:
            tag = valid_sxxexx
            suffix = tc.get('episode_title', '') or ''
        else:
            tag, suffix, _ = extract_episode_info(basename, keep_suffix=keep_suffix)
            if tag and tc.get('episode_title'):
                suffix = tc['episode_title']

        if tag:
            filename = tag + (suffix or '')
        else:
            filename = os.path.splitext(basename)[0]

    resolution = get_resolution(streams)
    bitrate = get_bitrate(format_info)
    encoding = get_encoding(streams)

    # Strip any existing metadata tag so force-reprocess doesn't double it
    filename = re.sub(r'\s*\[\w+\s+[\d.]+Mbps\s+\w+\]', '', filename).rstrip()

    # Sanitize illegal Windows filename characters (e.g. TVDB titles with '/')
    filename = re.sub(r'[/\\:*?"<>|]', ' -', filename)
    filename = re.sub(r'\s{2,}', ' ', filename).strip()

    # Insert year before metadata tag if provided by TVDB
    if tc.get('year'):
        filename += f" ({tc['year']})"

    parts = []
    if resolution:
        parts.append(resolution)
    if bitrate:
        rounded = round(bitrate, 1)
        if rounded < 1:
            # Sub-1 Mbps: round to nearest 0.1, e.g. 0.87 → ".9Mbps"
            parts.append(f"{rounded:.1f}".lstrip("0") + "Mbps")
        else:
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


SUPPORTED_CONTAINERS = ('mkv', 'mp4')


def determine_best_extension(preferred_extension, selected_subtitle_codecs,
                              input_ext=None, prefer_only=False):
    # "Prefer only" mode: if the input is already one of the supported
    # containers, keep it instead of remuxing to the preferred one — but
    # only when the input container can actually hold the selected subs.
    if (prefer_only and input_ext and input_ext.lower() in SUPPORTED_CONTAINERS):
        input_ext_lc = input_ext.lower()
        if not selected_subtitle_codecs:
            return input_ext_lc
        input_supported = get_supported_subtitle_codecs(input_ext_lc)
        if all(c in input_supported for c in selected_subtitle_codecs):
            return input_ext_lc

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
                 progress_fn=None, proc_holder=None, status_fn=None, keep_suffix=False,
                 tvdb_changes=None, prefer_only=False):
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
    input_ext_for_container = os.path.splitext(file)[1].lstrip('.').lower()

    # Look ahead: would we otherwise rename-only? Prefer-only only kicks
    # in if a remux isn't already required for some other reason (audio
    # relabel, stream trim, external sub).
    base_file_name = os.path.splitext(file)[0]
    subtitle_file = None
    for _ext in ('.en.srt', '.eng.srt', '.srt', '.sub'):
        candidate = base_file_name + _ext
        if os.path.exists(candidate):
            subtitle_file = candidate
            break

    needs_language_change = any(a[2] for a in audio_selection)
    same_audio = len(audio_selection) == len(audio_streams_all) and not needs_language_change
    same_subs = len(subtitle_selection) == len(subtitle_streams_all)
    no_external_subs = subtitle_file is None
    would_rename_only = rename or (same_audio and same_subs and no_external_subs)

    actual_extension = determine_best_extension(
        extension, selected_sub_codecs,
        input_ext=input_ext_for_container,
        prefer_only=prefer_only and would_rename_only,
    )
    if actual_extension != extension:
        if prefer_only and actual_extension == input_ext_for_container:
            print_fn(f"  Note: Keeping {actual_extension.upper()} (prefer-only: input is "
                     f"already a supported container and would be rename-only)")
        else:
            print_fn(f"  Note: Using {actual_extension.upper()} instead of {extension.upper()} "
                     f"for subtitle compatibility")

    output_file = build_output_filename(file, actual_extension, streams, format_info,
                                        norename, convert_force, output_dir,
                                        keep_suffix=keep_suffix,
                                        tvdb_changes=tvdb_changes)
    if not output_file:
        print_fn(f"Skipping {file} as it is already processed")
        return {'status': 'skipped'}

    input_ext = input_ext_for_container
    same_container = input_ext == actual_extension.lower()

    if same_container and would_rename_only:
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

    cmd = ['ffmpeg', '-y', '-progress', 'pipe:1', '-nostats',
           '-fflags', '+genpts', '-i', file]

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

    if dry_run:
        if verbose:
            print_fn(f"  CMD: {' '.join(cmd)}")
        return {'status': 'dry_run'}

    if verbose:
        print_fn(f"  CMD: {' '.join(cmd)}")

    if progress_fn:
        progress_fn(0)
    if status_fn:
        status_fn("")

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

    _prog = {}
    for line in proc.stdout:
        line = line.strip()
        if '=' in line:
            k, _, v = line.partition('=')
            _prog[k] = v
        if line.startswith('progress='):
            if progress_fn and duration > 0 and 'out_time_us' in _prog:
                try:
                    us = int(_prog['out_time_us'])
                    pct = min(99.0, (us / 1_000_000) / duration * 100)
                    progress_fn(pct)
                except (ValueError, ZeroDivisionError):
                    pass
            if status_fn or verbose:
                parts = []
                for key in ('frame', 'fps', 'out_time', 'speed'):
                    val = _prog.get(key, '')
                    if val and val not in ('N/A', '0.00', '0.00x', ''):
                        label = 'time' if key == 'out_time' else key
                        if key == 'out_time':
                            val = val[:8]
                        parts.append(f"{label}={val}")
                stat_line = '  '.join(parts)
                if stat_line and status_fn:
                    status_fn(stat_line)
                if verbose and stat_line:
                    print_fn(f"  {stat_line}")
            _prog = {}

    proc.wait()
    stderr_thread.join(timeout=5)
    full_stderr = ''.join(stderr_data)

    if status_fn:
        status_fn("")

    # Extract final stats from stderr for the status label (already shown below progress bar)
    final_stats = ''
    for line in reversed(full_stderr.splitlines()):
        stripped = line.strip()
        if stripped.startswith('frame=') and 'speed=' in stripped:
            final_stats = stripped
            break
    if final_stats and status_fn:
        status_fn(final_stats)

    if proc.returncode != 0:
        err_lines = [l.strip() for l in full_stderr.splitlines() if l.strip()]
        brief = next(
            (l for l in reversed(err_lines)
             if any(kw in l.lower() for kw in ('error', 'invalid', 'failed', 'no such', 'unable'))),
            err_lines[-1] if err_lines else 'Unknown error'
        )
        print_fn(f"  ✗ Failed: {brief}", 'drop')
        print_fn(f"    (full details → {os.path.basename(ERROR_LOG)})", 'warn')
        log_error(file, cmd, full_stderr)
        return {'status': 'failed'}
    else:
        print_fn("  ✓ Complete", 'keep')
        return {'status': 'remuxed', 'output_file': output_file}


def build_file_plan(file, extension="mkv", norename=False, convert_force=False,
                    output_dir=None, keep_languages=None, keep_suffix=False,
                    tvdb_changes=None, rename=False, prefer_only=False):
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
    input_ext_for_container = os.path.splitext(file)[1].lstrip('.').lower()

    # Check for external subtitle file alongside the video
    _base = os.path.splitext(file)[0]
    external_sub = next(
        ((_base + ext) for ext in ('.en.srt', '.eng.srt', '.srt', '.sub')
         if os.path.exists(_base + ext)), None)

    # Look ahead: would a remux be required even without the container
    # change? Prefer-only only applies when the answer is no.
    needs_language_change = any(a[2] for a in audio_selection)
    same_audio = len(audio_selection) == len(audio_streams_all) and not needs_language_change
    same_subs = len(subtitle_selection) == len(subtitle_streams_all)
    would_rename_only = rename or (same_audio and same_subs and external_sub is None)

    actual_extension = determine_best_extension(
        extension, selected_sub_codecs,
        input_ext=input_ext_for_container,
        prefer_only=prefer_only and would_rename_only,
    )

    output_file = build_output_filename(file, actual_extension, streams, format_info,
                                        norename, convert_force, output_dir,
                                        keep_suffix=keep_suffix,
                                        tvdb_changes=tvdb_changes)

    dur = float(format_info.get('duration') or 0)
    total_br = int(format_info.get('bit_rate') or 0)
    dur_str = f"{int(dur//3600):02d}:{int((dur%3600)//60):02d}:{int(dur%60):02d}"
    br_str = f"{total_br/1_000_000:.1f} Mbps" if total_br else "?"

    SEP = '─' * 58
    lines = []

    if not output_file:
        lines.append("Output:   (skip — already processed)")
        lines.append(SEP)
        return "\n".join(lines)

    out_name = os.path.basename(output_file)
    in_name = os.path.basename(file)
    lines.append(f"Output:   {out_name}" if out_name != in_name else "Output:   (same name)")

    # Determine action: rename-only vs remux.
    # Mirrors process_file logic: we only rename if the container already
    # matches AND no stream-level changes are needed.
    input_ext = input_ext_for_container
    same_container = input_ext == actual_extension.lower()
    will_rename = same_container and would_rename_only
    lines.append(f"Action:   {'RENAME ONLY (no re-encode)' if will_rename else 'REMUX (re-mux streams)'}")

    lines.append(f"File:     {os.path.basename(file)}")
    lines.append(f"Duration: {dur_str}   Total bitrate: {br_str}")

    # Only show container change if the input container is actually different
    if not same_container:
        lines.append(f"Container: {input_ext.upper()} → {actual_extension.upper()}")
    else:
        lines.append(f"Container: {actual_extension.upper()}")
    lines.append("")

    def _stream_br(stream):
        br = int(stream.get('bit_rate') or 0)
        if not br:
            return ''
        if br >= 1_000_000:
            return f"  {br/1_000_000:.1f}Mbps"
        return f"  {br//1000}kbps"

    if will_rename:
        # Informational only — no remux actions
        for s in video_streams_all:
            codec = s.get('codec_name', '?').upper()
            w, h = s.get('width', '?'), s.get('height', '?')
            br_note = _stream_br(s)
            lines.append(f"     Video    [{codec:>6}]  {w}x{h}{br_note}")

        for s in audio_streams_all:
            lang = s.get('tags', {}).get('language', 'und')
            codec = s.get('codec_name', '?').upper()
            ch = s.get('channels', '?')
            br_note = _stream_br(s)
            lines.append(f"     Audio    [{codec:>6}]  {ch}ch{br_note}  lang={lang}")

        for s in subtitle_streams_all:
            lang = s.get('tags', {}).get('language', 'und')
            codec = s.get('codec_name', '?')
            title = s.get('tags', {}).get('title', '')
            label = f"lang={lang}" + (f"  {title}" if title else "")
            lines.append(f"     Sub      [{codec:>20}]  {label}")
    else:
        for s in video_streams_all:
            codec = s.get('codec_name', '?').upper()
            w, h = s.get('width', '?'), s.get('height', '?')
            br_note = _stream_br(s)
            lines.append(f"  ✓ Video    [{codec:>6}]  {w}x{h}{br_note}  →  copy")

        audio_kept = {a[0]: a for a in audio_selection}
        for i, s in enumerate(audio_streams_all):
            lang = s.get('tags', {}).get('language', 'und')
            codec = s.get('codec_name', '?').upper()
            ch = s.get('channels', '?')
            br_note = _stream_br(s)
            if i in audio_kept:
                _, new_lang, changed = audio_kept[i]
                tag_note = f"  [tag: {lang}→{new_lang}]" if changed else ""
                lines.append(f"  ✓ Audio    [{codec:>6}]  {ch}ch{br_note}  lang={lang}{tag_note}  →  copy")
            else:
                lines.append(f"  ✗ Audio    [{codec:>6}]  {ch}ch{br_note}  lang={lang}  →  DROP")

        for i, s in enumerate(subtitle_streams_all):
            lang = s.get('tags', {}).get('language', 'und')
            codec = s.get('codec_name', '?')
            title = s.get('tags', {}).get('title', '')
            label = f"lang={lang}" + (f"  {title}" if title else "")
            if i in subtitle_selection:
                lines.append(f"  ✓ Sub      [{codec:>20}]  {label}  →  copy")
            else:
                lines.append(f"  ✗ Sub      [{codec:>20}]  {label}  →  DROP")

        if external_sub:
            lines.append(f"  ✓ Sub      [{'external':>20}]  {os.path.basename(external_sub)}  →  embed")

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
    parser.add_argument("--prefer-only", action='store_true',
                        help="Treat --extension as a preference between MKV/MP4: "
                             "don't remux a file that's already in the other supported "
                             "container. Other containers still get remuxed.")
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
                     args.output, args.languages,
                     prefer_only=args.prefer_only)


def launch_gui(terminal_cwd=None):
    """Launch the GUI for the video standardizer.

    terminal_cwd: the working directory of the launching terminal (passed from main()).
    When None (double-click launch via .pyw), only the saved last_folder or
    the script's own directory is used — never os.getcwd().
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, simpledialog
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
            self._tvdb_changes = {}  # filepath -> {year, sxxexx, episode_title}
            self._multi_drop_active = False
            self._multi_drop_sentinel = ""

            # Register the root window as a drop target so the whole app
            # accepts file/folder drops, not just the path entry field.
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self._on_drop)

            # ── Menu bar ─────────────────────────────────────────────────
            menubar = tk.Menu(self.root)
            tools_menu = tk.Menu(menubar, tearoff=0)
            tools_menu.add_command(label="Plex Thumbnails",
                                   command=self._open_plex_popup)
            menubar.add_cascade(label="Tools", menu=tools_menu)
            self.root.configure(menu=menubar)

            # ── Dark mode colours ────────────────────────────────────────
            _BG  = '#1e1e1e'
            _BG2 = '#252526'
            _FG  = '#cccccc'
            _SEL = '#0078d4'
            _ENT = '#3c3c3c'
            _BOR = '#555555'
            self.root.configure(bg=_BG)
            # Dark title bar on Windows 10/11
            self.root.after(20, self._apply_dark_titlebar)

            style = ttk.Style()
            style.theme_use('clam')
            style.configure('.',
                background=_BG, foreground=_FG,
                bordercolor=_BOR, focuscolor=_SEL, troughcolor=_BG2)
            style.configure('TFrame',           background=_BG)
            style.configure('TLabel',           background=_BG,  foreground=_FG)
            style.configure('TLabelframe',      background=_BG,  foreground=_FG,
                bordercolor=_BOR)
            style.configure('TLabelframe.Label', background=_BG, foreground=_FG)
            style.configure('TButton',
                background=_ENT, foreground=_FG,
                bordercolor=_BOR, relief='flat', padding=4)
            style.map('TButton',
                background=[('active', '#4c4c4c'), ('pressed', _SEL)],
                foreground=[('active', _FG)])
            style.configure('TEntry',
                fieldbackground=_ENT, foreground=_FG,
                insertcolor=_FG, bordercolor=_BOR,
                selectbackground=_SEL, selectforeground=_FG)
            style.configure('TRadiobutton',
                background=_BG, foreground=_FG, focuscolor=_BG,
                indicatorcolor=_ENT, indicatorbackground=_ENT)
            style.map('TRadiobutton',
                background=[('active', _BG)],
                indicatorcolor=[('selected', _SEL)])
            style.configure('TCheckbutton',
                background=_BG, foreground=_FG, focuscolor=_BG,
                indicatorcolor=_ENT, indicatorbackground=_ENT)
            style.map('TCheckbutton',
                background=[('active', _BG)],
                indicatorcolor=[('selected', _SEL)])
            style.configure('TPanedwindow',   background=_BOR)
            style.configure('Sash',           sashthickness=4, background=_BOR)
            style.configure('Horizontal.TProgressbar',
                troughcolor=_ENT, background=_SEL,
                bordercolor=_BOR, lightcolor=_SEL, darkcolor=_SEL)
            style.configure('Vertical.TScrollbar',
                troughcolor=_BG2, background=_ENT,
                bordercolor=_BOR, arrowcolor=_FG,
                lightcolor=_ENT, darkcolor=_ENT)
            style.map('Vertical.TScrollbar',
                background=[('active', '#4c4c4c'), ('pressed', _SEL)],
                arrowcolor=[('disabled', '#606060')])
            style.configure('Horizontal.TScrollbar',
                troughcolor=_BG2, background=_ENT,
                bordercolor=_BOR, arrowcolor=_FG,
                lightcolor=_ENT, darkcolor=_ENT)
            style.map('Horizontal.TScrollbar',
                background=[('active', '#4c4c4c'), ('pressed', _SEL)])

            # ttk.Combobox dropdown is a classic Tk Listbox — its colours come
            # from the option database, not the ttk style engine. Set them
            # here so both the main window's and the TVDB popup's comboboxes
            # get a dark dropdown menu without needing per-popup wiring.
            self.root.option_add('*TCombobox*Listbox.background', _BG2)
            self.root.option_add('*TCombobox*Listbox.foreground', _FG)
            self.root.option_add('*TCombobox*Listbox.selectBackground', _SEL)
            self.root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
            self.root.option_add('*TCombobox*Listbox.borderWidth', 0)
            # Accent style for primary action button
            _ACC = '#0e639c'
            style.configure('Accent.TButton',
                background=_ACC, foreground='#ffffff',
                bordercolor='#1177bb', relief='flat', padding=4)
            style.map('Accent.TButton',
                background=[('active', '#1177bb'), ('pressed', '#094771'),
                            ('disabled', '#3a3a3a')],
                foreground=[('disabled', '#777777')])
            # Section headers — soft blue tint
            style.configure('TLabelframe.Label', background=_BG, foreground='#9ec9f5')

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
            radio_row = ttk.Frame(container_frame)
            radio_row.pack(fill=tk.X)
            for ext in ["mkv", "mp4"]:
                ttk.Radiobutton(radio_row, text=ext.upper(),
                                variable=self.extension_var, value=ext).pack(side=tk.LEFT, padx=5)
            self.prefer_only_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                container_frame,
                text="Prefer only (don't remux between MKV/MP4)",
                variable=self.prefer_only_var,
            ).pack(anchor=tk.W, padx=2, pady=(3, 0))

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
            self.keep_suffix_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(opts_frame, text="Keep Episode Suffix",
                            variable=self.keep_suffix_var).pack(anchor=tk.W)
            ttk.Checkbutton(opts_frame, text="Force Re-process",
                            variable=self.force_var).pack(anchor=tk.W)
            self.delete_original_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(opts_frame, text="Delete Original After Re-process",
                            variable=self.delete_original_var).pack(anchor=tk.W)
            self.remove_ads_var = tk.BooleanVar(value=False)
            self._remove_ads_chk = ttk.Checkbutton(
                opts_frame, text="Remove Ads First (Comskip + VideoReDo)",
                variable=self.remove_ads_var)
            self._remove_ads_chk.pack(anchor=tk.W)
            ads_ok, ads_why = (OPTIONAL_FEATURES['comskip'][0]
                               and OPTIONAL_FEATURES['videoredo'][0]), None
            if not ads_ok:
                self._remove_ads_chk.configure(state=tk.DISABLED)
                ads_why = (OPTIONAL_FEATURES['comskip'][1]
                           or OPTIONAL_FEATURES['videoredo'][1])
                _attach_tooltip(self._remove_ads_chk, ads_why)
            ttk.Checkbutton(opts_frame, text="Verbose Output",
                            variable=self.verbose_var).pack(anchor=tk.W)

            # Action buttons
            btn_frame = ttk.Frame(left_frame, padding=5)
            btn_frame.pack(fill=tk.X, padx=5, pady=5)
            self.run_btn = ttk.Button(btn_frame, text="Run", command=self._run,
                                       style='Accent.TButton')
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

            # Find toolbar — each button replaces the file list with a
            # filtered scan of the current input path.
            # find_bar = ttk.Frame(list_frame)
            # find_bar.pack(fill=tk.X, padx=5, pady=(5, 0))
            # ttk.Label(find_bar, text="Find:", font=("", 9, "bold")).pack(
            #     side=tk.LEFT, padx=(0, 6))
            # self._find_buttons = []
            # for label, cmd in (
            #     ("By Ext",    self._find_by_ext),
            #     ("By Name",   self._find_by_name),
            #     ("Malformed", self._find_malformed),
            #     ("Low-res",   self._find_low_res),
            #     ("Corrupt",   self._find_corrupt),
            #     ("Metadata",  self._find_metadata),
            # ):
            #     b = ttk.Button(find_bar, text=label, command=cmd)
            #     b.pack(side=tk.LEFT, padx=(0, 3))
            #     self._find_buttons.append(b)

            list_inner = ttk.Frame(list_frame)
            list_inner.pack(fill=tk.BOTH, expand=True, padx=5, pady=(3, 2))

            list_scroll = ttk.Scrollbar(list_inner, orient=tk.VERTICAL)
            self.file_listbox = tk.Listbox(
                list_inner, yscrollcommand=list_scroll.set,
                font=("Consolas", 9), selectmode=tk.MULTIPLE,
                bg="#252526", fg="#cccccc", selectbackground="#0078d4",
                activestyle='none', height=7
            )
            list_scroll.config(command=self.file_listbox.yview)
            list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.file_listbox.pack(fill=tk.BOTH, expand=True)

            # Shift-click selects the range from the last clicked row to the
            # shift-clicked row (additive — existing selections are preserved).
            # MULTIPLE selectmode doesn't give us this natively.
            self._last_click_idx = None

            def _on_listbox_click(event):
                idx = self.file_listbox.nearest(event.y)
                if idx >= 0:
                    self._last_click_idx = idx

            def _on_listbox_shift_click(event):
                idx = self.file_listbox.nearest(event.y)
                if idx < 0:
                    return 'break'
                anchor = self._last_click_idx if self._last_click_idx is not None else idx
                lo, hi = (anchor, idx) if anchor <= idx else (idx, anchor)
                self.file_listbox.selection_set(lo, hi)
                self._last_click_idx = idx
                return 'break'

            self.file_listbox.bind('<Button-1>', _on_listbox_click, add='+')
            self.file_listbox.bind('<Shift-Button-1>', _on_listbox_shift_click)

            list_btn_row = ttk.Frame(list_frame)
            list_btn_row.pack(fill=tk.X, padx=5, pady=(0, 5))
            ttk.Button(list_btn_row, text="Show Plan",
                       command=self._show_plan).pack(side=tk.LEFT, padx=(0, 4))
            self._tvdb_btn = ttk.Button(
                list_btn_row, text="TVDB Lookup",
                command=self._tvdb_lookup)
            self._tvdb_btn.pack(side=tk.LEFT, padx=(0, 4))
            if not OPTIONAL_FEATURES['tvdb'][0]:
                self._tvdb_btn.configure(state=tk.DISABLED)
                _attach_tooltip(self._tvdb_btn, OPTIONAL_FEATURES['tvdb'][1])
            ttk.Button(list_btn_row, text="Clear Selection",
                       command=self._clear_selection).pack(side=tk.LEFT)
            # Danger action lives on the right, visually separated from the
            # benign buttons on the left. Matches --recycle on the find scripts.
            style.configure('Danger.TButton',
                background='#5a1f1f', foreground='#ffffff',
                bordercolor='#7a2a2a', relief='flat', padding=4)
            style.map('Danger.TButton',
                background=[('active', '#7a2a2a'), ('pressed', '#3a1414'),
                            ('disabled', '#3a3a3a')],
                foreground=[('disabled', '#777777')])
            ttk.Button(list_btn_row, text="Recycle",
                       style='Danger.TButton',
                       command=self._recycle_files).pack(side=tk.RIGHT)

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

            # Live ffmpeg stats line
            self.status_label = ttk.Label(
                prog_frame, text="", font=("Consolas", 10),
                foreground='#6a9fd8', anchor=tk.W)
            self.status_label.pack(fill=tk.X, pady=(2, 0))

            # Output text — build our own Text + ttk.Scrollbar instead of
            # scrolledtext.ScrolledText so the scrollbar matches the dark theme
            # (ScrolledText embeds a classic tk.Scrollbar that ignores ttk style).
            output_container = ttk.Frame(bottom_frame)
            output_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=(2, 0))
            output_scroll = ttk.Scrollbar(output_container, orient=tk.VERTICAL)
            self.output_text = tk.Text(
                output_container, wrap=tk.WORD, font=("Consolas", 9),
                state=tk.DISABLED, bg="#1e1e1e", fg="#cccccc",
                insertbackground="#cccccc", borderwidth=0,
                yscrollcommand=output_scroll.set,
            )
            output_scroll.configure(command=self.output_text.yview)
            output_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
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

        def _apply_dark_titlebar(self):
            try:
                import ctypes
                self.root.update()  # ensure Win32 window exists
                child_hwnd = self.root.winfo_id()
                # GA_ROOT=2 walks up to the actual top-level frame window
                hwnd = ctypes.windll.user32.GetAncestor(child_hwnd, 2)
                if not hwnd:
                    hwnd = child_hwnd
                for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Win11=20, Win10=19)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(ctypes.c_int(1)),
                        ctypes.sizeof(ctypes.c_int))
            except Exception:
                pass

        # ── Path / file list helpers ─────────────────────────────────────

        def _path_changed(self, *_):
            if self._path_trace_id:
                self.root.after_cancel(self._path_trace_id)
            self._path_trace_id = self.root.after(300, self._on_path_change)

        def _on_path_change(self):
            if self.processing:
                return
            path = self.path_var.get().strip()
            # Preserve a multi-drop list until the user changes the path
            # themselves — edits to path_var that still match our sentinel
            # are just the programmatic set from _on_drop.
            if self._multi_drop_active and path == self._multi_drop_sentinel:
                return
            self._multi_drop_active = False
            self.file_listbox.delete(0, tk.END)
            self._file_paths = []
            _done_pat = re.compile(r'\[\w+ [\d.]+Mbps \w+\]\.\w+$')
            if self.mode_var.get() == 'folder' and os.path.isdir(path):
                for f in get_video_files(path):
                    self.file_listbox.insert(tk.END, f"  {os.path.basename(f)}")
                    self._file_paths.append(f)
            elif self.mode_var.get() == 'file' and os.path.isfile(path):
                self.file_listbox.insert(tk.END, f"  {os.path.basename(path)}")
                self._file_paths.append(path)
            # Mark already-processed files immediately (no ffprobe needed)
            for idx, f in enumerate(self._file_paths):
                if _done_pat.search(os.path.basename(f)):
                    self._set_file_status(idx, 'done')

        def _get_selected_files(self):
            """Return the selected files, or all files if nothing is selected."""
            sel = self.file_listbox.curselection()
            if sel:
                return [self._file_paths[i] for i in sel if i < len(self._file_paths)]
            return list(self._file_paths)

        def _clear_selection(self):
            self.file_listbox.selection_clear(0, tk.END)

        def _recycle_files(self):
            """Send selected files (or all listed files if none selected) to
            the recycle bin after an explicit confirmation. Mirrors --recycle
            on the find scripts but with a safety prompt since in the GUI the
            list can contain hundreds of items picked via a single scan.
            """
            from tkinter import messagebox
            try:
                from send2trash import send2trash
            except ImportError:
                messagebox.showerror(
                    "Recycle unavailable",
                    "send2trash is not installed. Run:\n\n  pip install send2trash",
                    parent=self.root)
                return

            files = self._get_selected_files()
            if not files:
                self._log("Recycle: nothing to recycle.", 'warn')
                return

            # Build a preview so the confirmation tells the user what will happen.
            preview = '\n'.join(f"  • {os.path.basename(p)}" for p in files[:5])
            more = f"\n  … and {len(files) - 5} more" if len(files) > 5 else ''
            ok = messagebox.askyesno(
                "Send to Recycle Bin",
                f"Send {len(files)} file(s) to the Recycle Bin?\n\n"
                f"{preview}{more}\n\n"
                "Files can be restored from the Recycle Bin.",
                icon='warning', parent=self.root)
            if not ok:
                return

            recycled = 0
            failed = 0
            for p in files:
                try:
                    send2trash(p)
                    recycled += 1
                except Exception as e:
                    failed += 1
                    self._log(f"  Recycle failed for {os.path.basename(p)}: {e}", 'drop')

            # Rebuild the listbox, dropping any paths that no longer exist.
            remaining = [p for p in self._file_paths if os.path.exists(p)]
            self.file_listbox.delete(0, tk.END)
            self._file_paths = []
            for p in remaining:
                self.file_listbox.insert(tk.END, f"  {os.path.basename(p)}")
                self._file_paths.append(p)

            self._log(f"Recycled {recycled} file(s)" +
                      (f", {failed} failed" if failed else "") + ".",
                      'warn' if failed else 'info')

        def _set_file_status(self, index, status):
            icons  = {'pending': '  ', 'running': '⟳ ', 'done': '✓ ',
                      'renamed': '→ ', 'skipped': '— ', 'failed': '✗ '}
            colors = {'done': '#4ec994', 'renamed': '#9cdcfe', 'skipped': '#888888',
                      'failed': '#f14c4c', 'running': '#dcdcaa', 'pending': '#cccccc'}
            try:
                raw = self.file_listbox.get(index)
                base = raw[2:] if len(raw) >= 2 else raw
                # Tk's delete+insert drops the selection for this index; preserve
                # it so runs (including dry runs) don't clear the user's picks.
                was_selected = self.file_listbox.selection_includes(index)
                self.file_listbox.delete(index)
                self.file_listbox.insert(index, f"{icons.get(status, '  ')}{base}")
                self.file_listbox.itemconfig(index, fg=colors.get(status, '#cccccc'))
                if was_selected:
                    self.file_listbox.selection_set(index)
            except Exception:
                pass

        # ── Drag-and-drop ────────────────────────────────────────────────

        def _on_drop(self, event):
            paths = self._parse_dropped_paths(event.data)
            if not paths:
                return

            # Single item: keep the existing path-driven behavior so
            # Browse / the entry field stay in sync with the listbox.
            if len(paths) == 1:
                p = paths[0]
                if os.path.isdir(p):
                    self.mode_var.set('folder')
                    self.path_var.set(p)
                elif os.path.isfile(p):
                    self.mode_var.set('file')
                    self.path_var.set(p)
                return

            # Multiple items: collect video files from dropped files and
            # (non-recursively) dropped folders, preserving drop order and
            # deduplicating.
            collected = []
            seen = set()
            for p in paths:
                if os.path.isdir(p):
                    for f in get_video_files(p):
                        if f not in seen:
                            seen.add(f)
                            collected.append(f)
                elif os.path.isfile(p) and p.lower().endswith(VIDEO_EXTENSIONS):
                    if p not in seen:
                        seen.add(p)
                        collected.append(p)

            if not collected:
                return

            sentinel = f"[{len(collected)} items dropped]"
            self._multi_drop_active = True
            self._multi_drop_sentinel = sentinel
            self.mode_var.set('file')
            self.path_var.set(sentinel)

            # Populate the listbox ourselves — the path_var trace will
            # short-circuit while _multi_drop_active is True.
            self.file_listbox.delete(0, tk.END)
            self._file_paths = []
            _done_pat = re.compile(r'\[\w+ [\d.]+Mbps \w+\]\.\w+$')
            for f in collected:
                self.file_listbox.insert(tk.END, f"  {os.path.basename(f)}")
                self._file_paths.append(f)
            for idx, f in enumerate(self._file_paths):
                if _done_pat.search(os.path.basename(f)):
                    self._set_file_status(idx, 'done')

        @staticmethod
        def _parse_dropped_paths(data):
            """Split a tkinterdnd2 drop payload into individual paths.

            Paths containing spaces are wrapped in braces: {C:\\My Dir\\a.mkv}.
            Multiple items are space-separated, with each wrapped item
            (if it contains a space) braced independently.
            """
            s = (data or '').strip()
            out = []
            i = 0
            n = len(s)
            while i < n:
                if s[i].isspace():
                    i += 1
                    continue
                if s[i] == '{':
                    end = s.find('}', i + 1)
                    if end == -1:
                        out.append(s[i+1:].strip())
                        break
                    out.append(s[i+1:end])
                    i = end + 1
                else:
                    j = i
                    while j < n and not s[j].isspace():
                        j += 1
                    out.append(s[i:j])
                    i = j
            return [p for p in out if p]

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

        def _update_status(self, text):
            self.status_label.configure(text=text)

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

        def _show_plan(self):
            languages = self.lang_var.get().strip().split() or list(DEFAULT_KEEP_LANGUAGES)
            for f in self._get_selected_files():
                try:
                    plan = build_file_plan(
                        f,
                        extension=self.extension_var.get(),
                        norename=self.norename_var.get(),
                        convert_force=self.force_var.get(),
                        output_dir=self.output_var.get().strip() or None,
                        keep_languages=languages,
                        keep_suffix=self.keep_suffix_var.get(),
                        tvdb_changes=self._tvdb_changes.get(f),
                        rename=self.rename_var.get(),
                        prefer_only=self.prefer_only_var.get(),
                    )
                    self._log_plan(plan)
                except Exception as e:
                    self._log(f"Error building plan for {os.path.basename(f)}: {e}")

        # ── Find toolbar ─────────────────────────────────────────────────

        def _find_scan_root(self):
            """Return the folder to scan for Find operations, or None."""
            path = self.path_var.get().strip()
            if self._multi_drop_active and self._file_paths:
                # Scan the folder the first dropped file lives in.
                return os.path.dirname(self._file_paths[0])
            if os.path.isdir(path):
                return path
            if os.path.isfile(path):
                return os.path.dirname(path)
            return None

        def _populate_from_results(self, paths, summary_msg):
            """Replace the file list with an arbitrary set of paths."""
            self.file_listbox.delete(0, tk.END)
            self._file_paths = []
            sentinel = f"[{len(paths)} results]"
            self._multi_drop_active = True
            self._multi_drop_sentinel = sentinel
            self.path_var.set(sentinel)
            _done_pat = re.compile(r'\[\w+ [\d.]+Mbps \w+\]\.\w+$')
            for p in paths:
                self.file_listbox.insert(tk.END, f"  {os.path.basename(p)}")
                self._file_paths.append(p)
            for idx, f in enumerate(self._file_paths):
                if _done_pat.search(os.path.basename(f)):
                    self._set_file_status(idx, 'done')
            self._log(summary_msg, 'info')

        def _set_find_enabled(self, enabled):
            state = tk.NORMAL if enabled else tk.DISABLED
            for b in self._find_buttons:
                b.configure(state=state)
            self.run_btn.configure(state=state)

        def _run_find_scan(self, label, collector_fn):
            """Run `collector_fn(root, log_fn)` in a worker thread. The
            collector returns a list of matched paths. label is used in
            the progress messages.
            """
            if self.processing:
                self._log("Busy — finish the current run first.", 'warn')
                return
            root_dir = self._find_scan_root()
            if not root_dir:
                self._log(f"Find {label}: set a valid folder or file in Input first.", 'drop')
                return
            self._set_find_enabled(False)
            self._log(f"Find {label}: scanning {root_dir} …", 'info')

            def log_fn(msg, tag=None):
                self.root.after(0, self._log, msg, tag)

            def worker():
                try:
                    results = collector_fn(root_dir, log_fn)
                except Exception as e:
                    log_fn(f"Find {label} failed: {e}", 'drop')
                    results = []
                def finish():
                    self._set_find_enabled(True)
                    if results is None:
                        return
                    self._populate_from_results(
                        results, f"Find {label}: {len(results)} match(es) in {root_dir}")
                self.root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        # Fast, inline-scanning find operations

        _FIND_VIDEO_EXTS = {
            '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.m4v', '.ts', '.m2ts',
            '.mpg', '.mpeg', '.flv', '.webm', '.vob', '.divx', '.xvid', '.rmvb',
        }

        def _find_by_ext(self):
            ext = simpledialog.askstring(
                "Find by extension", "Extension (e.g. .ts):",
                initialvalue=".mpg", parent=self.root)
            if not ext:
                return
            ext = ext if ext.startswith('.') else '.' + ext
            ext = ext.lower()

            def collect(root, log_fn):
                out = []
                for dp, _d, fs in os.walk(root):
                    for f in fs:
                        if os.path.splitext(f)[1].lower() == ext:
                            out.append(os.path.join(dp, f))
                return sorted(out)

            self._run_find_scan(f"by ext '{ext}'", collect)

        def _find_by_name(self):
            needle = simpledialog.askstring(
                "Find by name", "Substring to match in filename:",
                parent=self.root)
            if not needle:
                return

            def collect(root, log_fn):
                n = needle.lower()
                out = []
                for dp, _d, fs in os.walk(root):
                    for f in fs:
                        if os.path.splitext(f)[1].lower() not in self._FIND_VIDEO_EXTS:
                            continue
                        if n in f.lower():
                            out.append(os.path.join(dp, f))
                return sorted(out)

            self._run_find_scan(f"by name '{needle}'", collect)

        def _find_malformed(self):
            _sxxexx = re.compile(r's\d{1,3}e\d{1,3}', re.IGNORECASE)

            def collect(root, log_fn):
                out = []
                for dp, _d, fs in os.walk(root):
                    for f in fs:
                        stem, ext = os.path.splitext(f)
                        if not ext:
                            out.append(os.path.join(dp, f))
                        elif ext.lower() in self._FIND_VIDEO_EXTS and not _sxxexx.search(stem):
                            out.append(os.path.join(dp, f))
                return sorted(out)

            self._run_find_scan("malformed", collect)

        # Heavier scans delegated to the scripts/ subprocess helpers.

        def _run_script_collector(self, argv, label):
            """Run a scripts/*.py helper and collect its stdout as paths."""
            script_py = os.path.join(_SCRIPT_DIR, 'scripts', argv[0])
            if not os.path.isfile(script_py):
                raise RuntimeError(f"Missing helper: {argv[0]}")
            cmd = [sys.executable, script_py] + argv[1:]

            def collect(root, log_fn):
                full = cmd + [root]
                proc = subprocess.Popen(
                    full, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_NO_WINDOW,
                )
                out_lines = []
                # Stream stderr (progress) to the log; collect stdout as paths.
                def _pump_stderr():
                    assert proc.stderr is not None
                    for line in proc.stderr:
                        line = line.rstrip()
                        if line:
                            log_fn(f"  {line}")
                t = threading.Thread(target=_pump_stderr, daemon=True)
                t.start()
                assert proc.stdout is not None
                for line in proc.stdout:
                    p = line.strip()
                    if p and os.path.isfile(p):
                        out_lines.append(p)
                proc.wait()
                t.join(timeout=2)
                return out_lines

            return self._run_find_scan(label, collect)

        def _find_low_res(self):
            h = simpledialog.askinteger(
                "Find low resolution", "Minimum acceptable vertical resolution:",
                initialvalue=480, minvalue=1, parent=self.root)
            if not h:
                return
            self._run_script_collector(
                ['check_resolution.py', '--min-height', str(h)],
                f"resolution < {h}p",
            )

        def _find_corrupt(self):
            self._run_script_collector(
                ['check_corrupt.py'],
                "corruption scan",
            )

        def _find_metadata(self):
            value = simpledialog.askstring(
                "Find by metadata", "Value substring to match "
                "(case-insensitive, any key):",
                initialvalue="Hulu", parent=self.root)
            if not value:
                return
            self._run_script_collector(
                ['check_metadata.py', '--value', value],
                f"metadata contains '{value}'",
            )

        def _open_plex_popup(self):
            try:
                from plex_integration.plex_episode_thumbs import build_plex_popup
            except Exception as e:
                self._log(f"Could not open Plex popup: {e}", 'drop')
                return
            build_plex_popup(self.root)

        def _tvdb_lookup(self):
            files = self._get_selected_files()
            if not files:
                self._log("No files selected for TVDB lookup.")
                return
            from tvdb_lookup.tvdb_lookup import build_tvdb_popup

            def _apply_cb(filepath, changes):
                self._tvdb_changes[filepath] = changes
                self._log(f"TVDB changes queued for {os.path.basename(filepath)}:", 'info')
                if changes.get('year'):
                    self._log(f"  Year: ({changes['year']})", 'keep')
                if changes.get('movie_title'):
                    self._log(f"  Movie Title: {changes['movie_title']}", 'keep')
                if changes.get('sxxexx'):
                    self._log(f"  Episode ID: {changes['sxxexx']}", 'keep')
                if changes.get('episode_title'):
                    self._log(f"  Episode Title: {changes['episode_title']}", 'keep')

            def _on_apply_done(applied_paths):
                # Sync the main file list selection to match what the user
                # picked in the popup: clear current selection, then reselect
                # exactly the files that had changes applied.
                idx_by_path = {p: i for i, p in enumerate(self._file_paths)}
                self.file_listbox.selection_clear(0, tk.END)
                for p in applied_paths:
                    i = idx_by_path.get(p)
                    if i is not None:
                        self.file_listbox.selection_set(i)

            build_tvdb_popup(self.root, files, _apply_cb,
                             on_apply_done=_on_apply_done)

        # ── Run / Stop ───────────────────────────────────────────────────

        def _run(self):
            if self.processing:
                return

            self._clear_output()

            # Use selected files, or all files in the list if nothing is selected
            files = self._get_selected_files()
            if not files:
                self._log("No video files to process.")
                return

            path = self.path_var.get().strip()

            # Persist the folder for next launch
            run_cfg = load_config()
            if self._multi_drop_active and self._file_paths:
                run_cfg['last_folder'] = os.path.dirname(self._file_paths[0])
            elif self.mode_var.get() == 'folder':
                run_cfg['last_folder'] = path
            else:
                run_cfg['last_folder'] = os.path.dirname(path)
            save_config(run_cfg)

            # Map file path → listbox index for live status updates
            file_to_lb_idx = {f: idx for idx, f in enumerate(self._file_paths)}

            # Mark queued files as pending
            for f in files:
                lb_idx = file_to_lb_idx.get(f, -1)
                if lb_idx >= 0:
                    self._set_file_status(lb_idx, 'pending')

            languages = self.lang_var.get().strip().split() or list(DEFAULT_KEEP_LANGUAGES)
            output_dir = self.output_var.get().strip() or None
            total = len(files)

            self.processing = True
            self._stop_flag = False
            self._ads_cancel = threading.Event()
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
            keep_suffix = self.keep_suffix_var.get()
            delete_original = self.delete_original_var.get()
            prefer_only = self.prefer_only_var.get()
            remove_ads = self.remove_ads_var.get() and not dry_run

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

                        lb_idx = file_to_lb_idx.get(f, -1)
                        if lb_idx >= 0:
                            self.root.after(0, self._set_file_status, lb_idx, 'running')

                        # Optional ad-removal pass before standardizing. If
                        # Comskip+VRD produces a _no_ads.mkv we use that as
                        # the input to process_file instead of the original.
                        ad_strip_output = None
                        proc_input = f
                        if remove_ads:
                            print_fn(f"  Removing ads: {os.path.basename(f)}…", 'info')
                            def _ads_status(**kw):
                                pct = kw.get('pct')
                                phase = kw.get('phase', '')
                                if pct is not None and phase:
                                    self.root.after(
                                        0, self._update_progress,
                                        float(pct), i, total)
                                    self.root.after(
                                        0, self._update_status,
                                        f"  {phase}: {pct:.0f}%")
                            try:
                                from commercial_skip.batch_comskip import strip_ads_one_file
                                success, out_path, msg = strip_ads_one_file(
                                    f, log_fn=lambda m: print_fn(f"  {m}"),
                                    status_fn=_ads_status,
                                    cancel_event=self._ads_cancel)
                            except Exception as ads_err:
                                print_fn(f"  Ad removal failed to start: {ads_err}", 'drop')
                                success, out_path, msg = False, None, str(ads_err)
                            if msg == 'canceled':
                                print_fn("  Ad removal canceled — stopping batch.", 'warn')
                                self._stop_flag = True
                                break
                            if success and out_path and os.path.isfile(out_path):
                                print_fn(f"  Ads removed: {msg}", 'keep')
                                # Drop the _no_ads suffix so the standardizer's
                                # filename builder sees a clean stem (matters
                                # for files without an SxxExx tag when
                                # norename is also on).
                                cleaned = out_path.replace('_no_ads.mkv', '.mkv')
                                if cleaned != out_path and not os.path.exists(cleaned):
                                    try:
                                        os.rename(out_path, cleaned)
                                        out_path = cleaned
                                    except Exception:
                                        pass
                                ad_strip_output = out_path
                                proc_input = out_path
                            elif msg == 'no-ads':
                                print_fn("  No ads detected — standardizing original.", 'info')
                            elif msg == 'rejected':
                                # _validate_ad_strip_output already logged the
                                # specific reason; just clarify the next step.
                                print_fn("  Standardizing original instead.", 'info')
                            else:
                                print_fn(f"  Ad removal failed ({msg}) — "
                                         f"standardizing original.", 'warn')

                        try:
                            plan = build_file_plan(
                                proc_input, extension=ext_var, norename=norename,
                                convert_force=force, output_dir=output_dir,
                                keep_languages=languages, keep_suffix=keep_suffix,
                                tvdb_changes=self._tvdb_changes.get(f),
                                rename=rename, prefer_only=prefer_only,
                            )
                            print_plan(plan)
                        except Exception as pe:
                            print_fn(f"  (plan error: {pe})")

                        def make_progress_fn(file_idx):
                            def _pfn(pct):
                                self.root.after(
                                    0, self._update_progress, pct, file_idx, total)
                            return _pfn

                        def make_status_fn():
                            def _sfn(stats):
                                self.root.after(0, self._update_status, stats)
                            return _sfn

                        result = process_file(
                            proc_input,
                            extension=ext_var,
                            dry_run=dry_run,
                            rename=rename,
                            verbose=verbose,
                            norename=norename,
                            convert_force=force,
                            output_dir=output_dir,
                            keep_languages=languages,
                            keep_suffix=keep_suffix,
                            print_fn=print_fn,
                            progress_fn=make_progress_fn(i + 1),
                            proc_holder=self._proc_holder,
                            status_fn=make_status_fn(),
                            tvdb_changes=self._tvdb_changes.get(f),
                            prefer_only=prefer_only,
                        )

                        status = (result or {}).get('status', 'skipped')
                        stats[status] = stats.get(status, 0) + 1
                        out_file = (result or {}).get('output_file')

                        # When ad-stripping produced an intermediate and the
                        # standardizer has written a new output, remove the
                        # intermediate. Skip if the standardizer just renamed
                        # the intermediate (out_file == ad_strip_output).
                        if (ad_strip_output and out_file != ad_strip_output
                                and os.path.exists(ad_strip_output)):
                            try:
                                os.remove(ad_strip_output)
                            except Exception as rm_err:
                                print_fn(f"  Could not remove {os.path.basename(ad_strip_output)}: "
                                         f"{rm_err}", 'drop')

                        if (status == 'remuxed' and delete_original
                                and not dry_run
                                and out_file != f
                                and os.path.exists(f)):
                            try:
                                os.remove(f)
                                print_fn(f"  Deleted original: {os.path.basename(f)}", 'warn')
                            except Exception as del_err:
                                print_fn(f"  Could not delete original: {del_err}", 'drop')

                        icon = ('done'    if status == 'remuxed' else
                                'renamed' if status == 'renamed' else
                                'skipped' if status in ('skipped', 'dry_run') else
                                'failed')
                        if lb_idx >= 0:
                            self.root.after(0, self._set_file_status, lb_idx, icon)
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
            # Kill the ffmpeg child, if any.
            proc = self._proc_holder.get('proc')
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
            # Signal ad-removal to tear down its Comskip + VRD processes.
            ads_cancel = getattr(self, '_ads_cancel', None)
            if ads_cancel is not None:
                ads_cancel.set()

        def _processing_done(self):
            self.processing = False
            self.run_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)
            self.status_label.configure(text="")

    root = TkinterDnD.Tk()
    VideoStandardizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
