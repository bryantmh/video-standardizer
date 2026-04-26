#!/usr/bin/env python3
"""
batch_comskip.py — Batch Comskip commercial detection + VideoReDo save.

Runs Comskip on every video in a folder to detect commercial breaks, then
hands the resulting cut list off to VideoReDo's silent COM instance which
smart-renders the file with the ad regions removed.

Usage:
    python batch_comskip.py <directory|->
                            [--threads N] [--recycle]
                            [--comskip PATH] [--comskip-ini PATH]

    Pass "-" as the directory to read a newline-separated list of file paths
    from stdin instead of walking a directory, e.g.:

        python scripts/find_by_ext.py "D:\\DVR" --ext .ts | python batch_comskip.py -
"""

import argparse
import csv
import io
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait as _fut_wait, FIRST_COMPLETED

import batch_vrd_save as bvs
from batch_vrd_save import (
    _HAS_SEND2TRASH,
    console, _log, _build_table, _set_slot, _upd_slot, _del_slot,
    _fmt_bytes, _fmt_elapsed,
    find_videos, save_vrd, update_tally,
    VIDEO_EXTS, NO_ADS_SUFFIX,
)
from rich.live import Live

_PROJECT_ROOT   = os.path.dirname(os.path.abspath(__file__))
COMSKIP_EXE     = os.path.join(_PROJECT_ROOT, 'comskip_dst', 'comskip.exe')
COMSKIP_INI     = os.path.join(_PROJECT_ROOT, 'comskip_dst', 'comskip.ini')

# Single source of truth for per-file wall-clock limit.  The watchdog below
# kills both the Comskip subprocess and the VideoReDo COM process if a worker
# exceeds this; no other timeouts exist anywhere in the pipeline.
FILE_TIMEOUT = 1800   # 30 minutes

# Comskip writes in-place progress updates like:
#   "00:01:23 -  1234 frames, 42%"
# We split on both CR and LF and match the trailing "NN%" token.
_COMSKIP_PROGRESS_RE = re.compile(
    r'(\d+:\d\d:\d\d)\s*-\s*(\d+)\s+frames.*?(\d+)%\s*$'
)

# Register styles for phases on the shared dashboard.
bvs._PHASE_STYLE['Comskip']   = 'magenta'
bvs._PHASE_STYLE['Timed out'] = 'bold yellow'


# ---------------------------------------------------------------------------
#  Timeout registry + single watchdog thread
#
#  Each worker registers its deadline and the child processes that should be
#  forcibly terminated if that deadline is exceeded.  A single background
#  thread walks the registry every second and kills any expired worker's
#  processes.  This is the ONLY timeout mechanism in the whole pipeline.
#
#  Replaces an earlier per-worker threading.Timer + PID-diff design that was
#  racy: two workers Dispatching at the same time both computed "new VRD PIDs
#  since my snapshot" and targeted the same PID, so only one of several stuck
#  workers actually got killed.
# ---------------------------------------------------------------------------

_timeouts_lock = threading.Lock()
# idx -> {
#   'deadline':     monotonic time this slot must finish by,
#   'comskip_proc': live subprocess.Popen for Comskip (or None),
#   'vrd_pid':      OS PID of this worker's VideoReDo instance (or None),
#   'source_path':  input video path (used only for log messages),
#   'out_dir':      directory where Comskip sidecars + _no_ads output live,
#   'out_stem':     Comskip sidecar basename (src-stem + '_comskip'),
#   'temp_output':  partial '<src-stem>_no_ads.mkv' to clean on timeout,
#   'expired':      True once the watchdog has fired on this slot.
# }
_timeouts: dict = {}

# Serializes Dispatch+PID-probe across workers so each observes a distinct new
# VRD PID.  Short contention, but prior races left timeouts targeting the
# wrong process (or no process at all).
_vrd_dispatch_lock = threading.Lock()

_watchdog_stop = threading.Event()
_watchdog_thread: threading.Thread | None = None

_COMSKIP_SIDECAR_EXTS = ('.VPrj', '.edl', '.log', '.logo.txt', '.txt')


def _register_timeout(idx: int, deadline: float, *,
                      source_path: str, out_dir: str, out_stem: str,
                      temp_output: str) -> None:
    with _timeouts_lock:
        _timeouts[idx] = {
            'deadline':     deadline,
            'comskip_proc': None,
            'vrd_pid':      None,
            'source_path':  source_path,
            'out_dir':      out_dir,
            'out_stem':     out_stem,
            'temp_output':  temp_output,
            'expired':      False,
        }


def _attach_comskip(idx: int, proc) -> None:
    with _timeouts_lock:
        entry = _timeouts.get(idx)
        if entry is not None:
            entry['comskip_proc'] = proc


def _attach_vrd_pid(idx: int, pid: int | None) -> None:
    with _timeouts_lock:
        entry = _timeouts.get(idx)
        if entry is not None:
            entry['vrd_pid'] = pid


def _is_expired(idx: int) -> bool:
    with _timeouts_lock:
        entry = _timeouts.get(idx)
        return bool(entry and entry['expired'])


def _unregister_timeout(idx: int) -> None:
    with _timeouts_lock:
        _timeouts.pop(idx, None)


def _kill_process_tree(pid: int) -> None:
    """Force-kill a process and all descendants via taskkill /F /T."""
    if not pid:
        return
    try:
        subprocess.run(
            ['taskkill', '/F', '/T', '/PID', str(pid)],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _cleanup_slot(entry: dict) -> None:
    """Delete the partial _no_ads output and all Comskip sidecars for a slot.

    Only touches files tied to this specific source — never the original
    input, never other workers' outputs.
    """
    temp_output = entry.get('temp_output')
    if temp_output:
        try:
            if os.path.isfile(temp_output):
                os.remove(temp_output)
        except Exception:
            pass
    out_dir  = entry.get('out_dir')
    out_stem = entry.get('out_stem')
    if out_dir and out_stem:
        for ext in _COMSKIP_SIDECAR_EXTS:
            p = os.path.join(out_dir, out_stem + ext)
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass


def _terminate_slot(entry: dict) -> None:
    """Kill the processes registered for one slot and delete its partial
    outputs.  Used by both the timeout watchdog and the Ctrl-C handler.
    """
    proc = entry.get('comskip_proc')
    if proc is not None:
        _kill_process_tree(proc.pid)
    vrd_pid = entry.get('vrd_pid')
    if vrd_pid:
        _kill_process_tree(vrd_pid)
    # Give the OS a moment to release file handles before deleting.
    time.sleep(0.5)
    _cleanup_slot(entry)


def cancel_slot(idx: int) -> bool:
    """Public: force-expire one slot and terminate its processes.

    Used by the GUI Stop button to kill an in-flight Comskip + VRD pair
    without waiting for the 30-minute watchdog deadline. Returns True if a
    live slot was terminated, False if the slot had already finished.
    """
    with _timeouts_lock:
        entry = _timeouts.get(idx)
        if entry is None or entry['expired']:
            return False
        entry['expired'] = True
        snapshot = dict(entry)
    _terminate_slot(snapshot)
    return True


def _expire_all_slots(reason: str) -> None:
    """Mark every registered slot expired and terminate it.  Called on
    Ctrl-C so any partial output is cleaned up before we exit.
    """
    with _timeouts_lock:
        victims: list[dict] = []
        for entry in _timeouts.values():
            if entry['expired']:
                continue
            entry['expired'] = True
            victims.append(dict(entry))
    for entry in victims:
        src = entry.get('source_path', '?')
        _log(f'[yellow]  {reason}: cleaning up {os.path.basename(src)}[/yellow]')
        _terminate_slot(entry)


def _watchdog_loop() -> None:
    """Every second: for each slot past its deadline, kill its processes
    (Comskip + that worker's VRD), then clean up partial output files.  Only
    touches the processes and files tied to the expired slot.
    """
    while not _watchdog_stop.is_set():
        now = time.monotonic()
        victims: list[dict] = []
        with _timeouts_lock:
            for entry in _timeouts.values():
                if entry['expired']:
                    continue
                if now >= entry['deadline']:
                    entry['expired'] = True
                    # Copy so we can act outside the lock even if the worker's
                    # finally removes the entry in the meantime.
                    victims.append(dict(entry))
        # Kill + clean outside the lock so a slow taskkill can't block new
        # registrations from other workers.
        for entry in victims:
            # src = entry.get('source_path', '?')
            # _log(f'[yellow]  TIMEOUT: {os.path.basename(src)} exceeded {FILE_TIMEOUT}s — killing processes[/yellow]')
            _terminate_slot(entry)
        _watchdog_stop.wait(1.0)


def _start_watchdog() -> None:
    """Launch the singleton watchdog thread.  Idempotent."""
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return
    _watchdog_stop.clear()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, name='timeout-watchdog', daemon=True,
    )
    _watchdog_thread.start()


def _stop_watchdog() -> None:
    _watchdog_stop.set()


def _find_vrd_pids() -> set[int]:
    """Return PIDs of currently running VideoReDo processes via tasklist."""
    try:
        out = subprocess.check_output(
            ['tasklist', '/FO', 'CSV', '/NH'],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        )
        pids = set()
        for row in csv.reader(io.StringIO(out)):
            if len(row) >= 2 and 'videoredo' in row[0].lower():
                try:
                    pids.add(int(row[1]))
                except ValueError:
                    pass
        return pids
    except Exception:
        return set()


def _run_comskip(src: str, out_dir: str, out_stem: str,
                 comskip_exe: str, comskip_ini: str,
                 status_fn, *, idx: int) -> str | None:
    """
    Run Comskip on `src`, writing outputs into `out_dir` with basename
    `out_stem`.  Returns the path to the produced .VPrj on success, else None.
    Streams stdout to drive the live-dashboard percent indicator.
    """
    args = [
        comskip_exe,
        '--videoredo3',
        f'--ini={os.path.normpath(comskip_ini)}',
        f'--output={os.path.normpath(out_dir)}',
        f'--output-filename={out_stem}',
        os.path.normpath(src),
    ]
    status_fn(phase='Comskip', pct=0.0)

    # On Windows, detach from the parent's console so Comskip's verbose
    # WriteConsole/fprintf output can't bypass our stdout pipe and leak into
    # the rich Live table.
    popen_kwargs = {}
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        popen_kwargs['startupinfo'] = startupinfo
        popen_kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        errors='replace',
        bufsize=1,
        **popen_kwargs,
    )
    assert proc.stdout is not None
    # Hand the Popen to the watchdog so it can kill the whole tree on timeout.
    _attach_comskip(idx, proc)
    buf = ''
    try:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                # Pipe closed — either Comskip exited normally, or the
                # watchdog killed it.  Either way, stop reading.
                break
            buf += chunk.replace('\r', '\n')
            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                m = _COMSKIP_PROGRESS_RE.search(line)
                if m:
                    pos_str, _frames, pct_str = m.groups()
                    try:
                        pct = float(pct_str)
                    except ValueError:
                        continue
                    status_fn(phase='Comskip', pct=pct, pos=pos_str)
    finally:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Belt-and-braces: the watchdog should have killed it, but if the
            # pipe closed for some other reason, make sure the tree is gone.
            _kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    if _is_expired(idx):
        return None

    vprj = os.path.join(out_dir, out_stem + '.VPrj')
    return vprj if os.path.isfile(vprj) else None


def _cleanup_sidecars(out_dir: str, out_stem: str) -> None:
    """Remove Comskip sidecar files (.VPrj, .edl, .log, etc.) after a
    successful save.  On timeout the watchdog performs this cleanup itself."""
    for ext in _COMSKIP_SIDECAR_EXTS:
        p = os.path.join(out_dir, out_stem + ext)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


def process_file(vrd, path: str, recycle: bool,
                 comskip_exe: str, comskip_ini: str,
                 *, status_fn, idx: int, out_dir: str, out_stem: str) -> tuple:
    """
    Run Comskip on `path`, then hand its Vprj to VRD for the cut-and-save.
    Returns (success, orig_bytes, new_bytes, n_cuts, err_msg).
    """
    vprj = _run_comskip(
        path, out_dir, out_stem, comskip_exe, comskip_ini, status_fn,
        idx=idx,
    )
    if vprj is None:
        if _is_expired(idx):
            status_fn(phase='Timed out')
            return False, 0, 0, 0, 'Timed out (limit exceeded)'
        _cleanup_sidecars(out_dir, out_stem)
        status_fn(phase='Error')
        return False, 0, 0, 0, 'Comskip failed to produce a .VPrj'

    try:
        return save_vrd(vrd, path, vprj, recycle=recycle, status_fn=status_fn)
    finally:
        # On success this tidies sidecars.  On timeout the watchdog has
        # already cleaned everything (including any partial _no_ads output),
        # so a duplicate best-effort pass here is harmless.
        if not _is_expired(idx):
            _cleanup_sidecars(out_dir, out_stem)


def _worker(task: tuple) -> tuple:
    idx, total, path, recycle, comskip_exe, comskip_ini = task
    fname = os.path.basename(path)

    _set_slot(idx, total=total, fname=fname, phase='Comskip')

    def status_fn(**kw):
        _upd_slot(idx, **kw)

    stem, _ext  = os.path.splitext(path)
    out_dir     = os.path.dirname(path)
    out_stem    = os.path.basename(stem) + '_comskip'
    temp_output = stem + '_no_ads.mkv'

    t0 = time.monotonic()
    _register_timeout(
        idx, t0 + FILE_TIMEOUT,
        source_path=path, out_dir=out_dir, out_stem=out_stem,
        temp_output=temp_output,
    )

    import pythoncom
    pythoncom.CoInitialize()
    vrd_silent = vrd = None
    vrd_pid: int | None = None
    try:
        import win32com.client

        # Serialize Dispatch + PID probe across all workers.  Without this,
        # two workers that snapshot "pids_before" at the same instant both
        # see each other's new PIDs and race to target the same process —
        # only one of several stuck workers actually gets killed on timeout.
        with _vrd_dispatch_lock:
            pids_before = _find_vrd_pids()
            vrd_silent = win32com.client.Dispatch('VideoReDo6.VideoReDoSilent')
            vrd = vrd_silent.VRDInterface
            # VRD may take a moment to appear in the process list after Dispatch.
            probe_deadline = time.monotonic() + 6
            while time.monotonic() < probe_deadline:
                new_pids = _find_vrd_pids() - pids_before
                if new_pids:
                    vrd_pid = next(iter(new_pids))
                    break
                time.sleep(0.25)

        if vrd_pid is None:
            _log(f'[yellow]  WARNING: {fname}: could not determine VRD PID — timeout kill will not work[/yellow]')
        else:
            _attach_vrd_pid(idx, vrd_pid)

        success, orig_b, new_b, n_cuts, err_msg = process_file(
            vrd, path, recycle, comskip_exe, comskip_ini,
            status_fn=status_fn, idx=idx,
            out_dir=out_dir, out_stem=out_stem,
        )
        elapsed = _fmt_elapsed(time.monotonic() - t0)
        cuts_str = (f'  [bright_white]{n_cuts} cut{"s" if n_cuts != 1 else ""}[/bright_white]'
                    if n_cuts > 0 else '')
        if success:
            saved_b  = orig_b - new_b
            pct_save = saved_b / orig_b * 100 if orig_b else 0.0
            _log(
                f'[bright_green]✓[/bright_green] [dim]{idx}/{total}[/dim]'
                f'  {fname}'
                f'  [cyan]{_fmt_bytes(orig_b)}[/cyan] → [cyan]{_fmt_bytes(new_b)}[/cyan]'
                f'  [green]saved {_fmt_bytes(saved_b)} ({pct_save:.0f}%)[/green]'
                f'{cuts_str}'
                f'  [dim]{elapsed}[/dim]'
            )
        elif err_msg == '':
            _log(f'[dim]○ {idx}/{total}  {fname}  No ads detected  {elapsed}[/dim]')
        else:
            _log(
                f'[bold red]✗[/bold red] [dim]{idx}/{total}[/dim]'
                f'  {fname}  [red]{err_msg}[/red]'
                f'{cuts_str}'
                f'  [dim]{elapsed}[/dim]'
            )
        return path, success, orig_b, new_b
    except Exception as exc:
        elapsed = _fmt_elapsed(time.monotonic() - t0)
        # If the watchdog already fired, the exception is almost certainly a
        # COM failure caused by VRD being killed — report it as a timeout.
        if _is_expired(idx):
            label = 'Timed out (limit exceeded)'
            status_fn(phase='Timed out')
        else:
            label = str(exc)
            status_fn(phase='Error')
        _log(
            f'[bold red]✗[/bold red] [dim]{idx}/{total}[/dim]'
            f'  {fname}  [red]{label}[/red]  [dim]{elapsed}[/dim]'
        )
        return path, False, 0, 0
    finally:
        _unregister_timeout(idx)
        _del_slot(idx)
        # Clean shutdown of our VRD instance.  If ProgramExit blocks (e.g.
        # because the watchdog already killed VRD), we fall through to the
        # taskkill below.  Either way, don't leave an orphan VRD process.
        if vrd is not None:
            try:
                vrd.ProgramExit()
            except Exception:
                pass
        if vrd_pid is not None:
            _kill_process_tree(vrd_pid)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


_single_file_idx = 100000
_single_file_idx_lock = threading.Lock()


def _probe_duration_seconds(path: str) -> float | None:
    """Return duration via ffprobe, or None if unavailable."""
    try:
        out = subprocess.check_output(
            ['ffprobe', '-v', 'error', '-show_entries',
             'format=duration', '-of', 'default=nw=1:nk=1', path],
            text=True, timeout=30,
            stderr=subprocess.DEVNULL,
        )
        return float(out.strip())
    except Exception:
        return None


# Reject outputs below this fraction of the original size. Tuned conservatively:
# a standard 22-minute sitcom cut from a 30-minute slot is ~73% of the source,
# so 0.40 only triggers on clearly-broken Comskip runs that cut nearly
# everything.
_MIN_OUTPUT_SIZE_RATIO = 0.40


def _validate_ad_strip_output(output_path: str, orig_bytes: int, new_bytes: int,
                              *, min_output_seconds: float, log_fn) -> str | None:
    """Check the cut output against sanity rules. Return None if it passes,
    or a short human-readable reason if it should be rejected.
    """
    if min_output_seconds > 0:
        dur = _probe_duration_seconds(output_path)
        if dur is None:
            return 'output duration unreadable'
        if dur < min_output_seconds:
            return (f'output {dur:.0f}s shorter than minimum '
                    f'{min_output_seconds:.0f}s')
    # A wildly small file is a near-certain Comskip misdetect even if the
    # minimum-duration check passes (e.g. produced a sliver we can still
    # probe). Size check catches that case.
    if orig_bytes > 0 and new_bytes > 0:
        ratio = new_bytes / orig_bytes
        if ratio < _MIN_OUTPUT_SIZE_RATIO:
            return (f'output only {ratio * 100:.0f}% of source size '
                    f'(<{_MIN_OUTPUT_SIZE_RATIO * 100:.0f}%)')
    return None


def strip_ads_one_file(path: str, *,
                       comskip_exe: str = COMSKIP_EXE,
                       comskip_ini: str = COMSKIP_INI,
                       log_fn=None,
                       status_fn=None,
                       cancel_event=None,
                       min_output_seconds: float = 20 * 60) -> tuple[bool, str | None, str]:
    """Run Comskip + VRD on a single file. Callable from any Python context —
    the GUI uses this to chain ad removal ahead of the standardizer.

    Pass a threading.Event as cancel_event to allow cancellation: when set,
    the slot's Comskip and VRD processes are killed and partial output is
    cleaned up.

    min_output_seconds rejects outputs shorter than this many seconds as a
    safety net against Comskip misdetecting huge spans as commercials.
    Set to 0 to disable.

    Returns (success, output_path, message):
      (True,  '<src>_no_ads.mkv', 'ok')       cut + rendered
      (False, src_path,           'no-ads')   Comskip found nothing to cut
      (False, src_path,           'rejected') output failed a sanity guard
      (False, None,               'canceled') user aborted via cancel_event
      (False, None,               err_msg)    other failure
    """
    import pythoncom
    import win32com.client

    if log_fn is None:
        def log_fn(msg):
            pass
    if status_fn is None:
        def status_fn(**kw):
            pass

    with _single_file_idx_lock:
        global _single_file_idx
        idx = _single_file_idx
        _single_file_idx += 1

    _start_watchdog()

    # Cancel watcher — polls the caller's Event and flips the slot to expired,
    # which makes the real watchdog kill Comskip + VRD and clean up.
    cancel_stop = threading.Event()
    if cancel_event is not None:
        def _cancel_watcher():
            while not cancel_stop.is_set():
                if cancel_event.is_set():
                    cancel_slot(idx)
                    return
                cancel_stop.wait(0.25)
        threading.Thread(target=_cancel_watcher,
                         name=f'ads-cancel-{idx}',
                         daemon=True).start()

    stem, _ext  = os.path.splitext(path)
    out_dir     = os.path.dirname(path)
    out_stem    = os.path.basename(stem) + '_comskip'
    temp_output = stem + '_no_ads.mkv'

    _register_timeout(
        idx, time.monotonic() + FILE_TIMEOUT,
        source_path=path, out_dir=out_dir, out_stem=out_stem,
        temp_output=temp_output,
    )

    pythoncom.CoInitialize()
    vrd = vrd_silent = None
    vrd_pid: int | None = None
    try:
        with _vrd_dispatch_lock:
            pids_before = _find_vrd_pids()
            vrd_silent = win32com.client.Dispatch('VideoReDo6.VideoReDoSilent')
            vrd = vrd_silent.VRDInterface
            probe_deadline = time.monotonic() + 6
            while time.monotonic() < probe_deadline:
                new_pids = _find_vrd_pids() - pids_before
                if new_pids:
                    vrd_pid = next(iter(new_pids))
                    break
                time.sleep(0.25)
        if vrd_pid is not None:
            _attach_vrd_pid(idx, vrd_pid)

        success, orig_bytes, new_bytes, n_cuts, err = process_file(
            vrd, path, recycle=False,
            comskip_exe=comskip_exe, comskip_ini=comskip_ini,
            status_fn=status_fn, idx=idx,
            out_dir=out_dir, out_stem=out_stem,
        )
        if success:
            # Sanity guards against Comskip cutting the entire show as ads.
            rejection = _validate_ad_strip_output(
                temp_output, orig_bytes, new_bytes,
                min_output_seconds=min_output_seconds, log_fn=log_fn,
            )
            if rejection is not None:
                try:
                    os.remove(temp_output)
                except Exception:
                    pass
                log_fn(f'  Ad removal rejected: {rejection} — keeping original.')
                return False, path, 'rejected'
            return True, temp_output, f'ok ({n_cuts} cuts)'
        if err == '':
            return False, path, 'no-ads'
        return False, None, err or 'failed'
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            return False, None, 'canceled'
        if _is_expired(idx):
            return False, None, 'Timed out'
        return False, None, str(exc)
    finally:
        cancel_stop.set()
        _unregister_timeout(idx)
        if vrd is not None:
            try:
                vrd.ProgramExit()
            except Exception:
                pass
        if vrd_pid is not None:
            _kill_process_tree(vrd_pid)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _read_paths_from_stdin() -> list[str]:
    """
    Read newline-separated video paths from stdin. Blank lines are ignored.
    Non-existent paths and non-video extensions are skipped with a warning;
    paths already ending in NO_ADS_SUFFIX are silently skipped to match the
    folder-walk behaviour.
    """
    paths: list[str] = []
    for raw in sys.stdin:
        p = raw.strip().strip('"').strip("'")
        if not p:
            continue
        stem, ext = os.path.splitext(os.path.basename(p))
        if stem.endswith(NO_ADS_SUFFIX):
            continue
        if ext.lower() not in VIDEO_EXTS:
            console.print(f'[yellow]  skip (not a video): {p}[/yellow]')
            continue
        if not os.path.isfile(p):
            console.print(f'[yellow]  skip (not found): {p}[/yellow]')
            continue
        paths.append(os.path.abspath(p))
    # De-dup while preserving order.
    seen = set()
    unique = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def main():
    parser = argparse.ArgumentParser(
        description='Batch Comskip + VideoReDo save.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('directory', nargs='?', default=None,
                        help='Root folder of videos to process, or "-" to '
                             'read newline-separated file paths from stdin')
    parser.add_argument('--threads', type=int, default=10,
                        help='Parallel worker count (default: 10).  Each worker '
                             'runs Comskip and a VideoReDo instance in sequence.')
    parser.add_argument('--recycle', action='store_true',
                        help='Send the original to the recycle bin and rename '
                             'the output to take its place')
    parser.add_argument('--comskip', default=COMSKIP_EXE,
                        help=f'Path to comskip.exe (default: {COMSKIP_EXE})')
    parser.add_argument('--comskip-ini', default=COMSKIP_INI,
                        help=f'Path to comskip.ini (default: {COMSKIP_INI})')
    parser.add_argument('--start-at', type=int, default=1, metavar='N',
                        help='Skip the first N-1 files and start at file number N (1-based, default: 1)')
    args = parser.parse_args()

    try:
        import win32com.client  # noqa: F401
        import pythoncom        # noqa: F401
    except ImportError:
        console.print('ERROR: pywin32 is not installed.  Run: pip install pywin32',
                      style='red')
        sys.exit(1)

    if args.recycle and not _HAS_SEND2TRASH:
        console.print('ERROR: --recycle requires the send2trash package.  '
                      'Run: pip install send2trash', style='red')
        sys.exit(1)

    if not os.path.isfile(args.comskip):
        console.print(f'ERROR: comskip.exe not found at {args.comskip!r}', style='red')
        sys.exit(1)
    if not os.path.isfile(args.comskip_ini):
        console.print(f'ERROR: comskip.ini not found at {args.comskip_ini!r}',
                      style='red')
        sys.exit(1)

    if not args.directory:
        parser.print_help()
        sys.exit(1)

    if args.directory == '-':
        if sys.stdin.isatty():
            console.print(
                'ERROR: "-" was passed but stdin is a terminal. '
                'Pipe a list of paths in, e.g.:\n'
                '  python scripts/find_by_ext.py DIR --ext .ts | '
                'python batch_comskip.py -',
                style='red',
            )
            sys.exit(1)
        videos = _read_paths_from_stdin()
        source_desc = '<stdin>'
    else:
        if not os.path.isdir(args.directory):
            console.print(f'ERROR: not a directory: {args.directory!r}', style='red')
            sys.exit(1)
        videos = find_videos(args.directory)
        source_desc = args.directory

    total = len(videos)
    if total == 0:
        console.print('No video files found.')
        return

    start_idx = max(1, args.start_at)
    if start_idx > 1:
        if start_idx > total:
            console.print(f'ERROR: --start-at {start_idx} exceeds total file count ({total}).', style='red')
            sys.exit(1)
        videos = videos[start_idx - 1:]

    n_workers = min(args.threads, len(videos))
    console.rule('[bold]Comskip + VideoReDo Batch[/bold]')
    console.print(f'  Found    [cyan]{total}[/cyan] video file(s) in [dim]{source_desc!r}[/dim]')
    if start_idx > 1:
        console.print(f'  Starting at file [cyan]{start_idx}[/cyan] ([dim]{total - len(videos)} skipped[/dim])')
    console.print(f'  Comskip  [cyan]{args.comskip}[/cyan]')
    console.print(f'  Workers  [cyan]{n_workers}[/cyan]')
    if args.recycle:
        console.print('  Mode     [yellow]--recycle[/yellow] (originals → recycle bin)')
    console.print()

    tasks = [
        (start_idx + i, total, p, args.recycle, args.comskip, args.comskip_ini)
        for i, p in enumerate(videos)
    ]

    processed = skipped = errors = 0
    total_orig_bytes = total_new_bytes = 0

    _start_watchdog()
    try:
        with Live(_build_table(), console=console, refresh_per_second=4,
                  vertical_overflow='visible') as live:
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {pool.submit(_worker, t): t for t in tasks}
                pending = set(futures.keys())
                while pending:
                    live.update(_build_table())
                    done, pending = _fut_wait(
                        pending, timeout=0.25, return_when=FIRST_COMPLETED
                    )
                    for fut in done:
                        try:
                            _p, success, orig_b, new_b = fut.result()
                        except KeyboardInterrupt:
                            raise
                        except Exception as exc:
                            _log(f'[red]ERROR (unexpected): {exc}[/red]')
                            errors += 1
                            continue
                        if success:
                            processed += 1
                            total_orig_bytes += orig_b
                            total_new_bytes  += new_b
                            update_tally(orig_b, new_b)
                        else:
                            if orig_b == 0:
                                errors += 1
                            else:
                                skipped += 1
                live.update(_build_table())
    except KeyboardInterrupt:
        bvs._stop_event.set()
        _log('\n[yellow]Ctrl-C received — killing active workers and cleaning up partial output...[/yellow]')
        _expire_all_slots('INTERRUPTED')
    finally:
        _stop_watchdog()

    console.print()
    console.rule()
    console.print(f'  [bold]Files processed[/bold]   [green]{processed}[/green]')
    console.print(f'  [bold]Skipped (no ads)[/bold]  [dim]{skipped}[/dim]')
    if errors:
        console.print(f'  [bold]Errors[/bold]            [red]{errors}[/red]')
    if processed:
        saved = total_orig_bytes - total_new_bytes
        pct   = saved / total_orig_bytes * 100 if total_orig_bytes else 0.0
        console.print(
            f'  [bold]Space saved[/bold]       '
            f'[green]{_fmt_bytes(saved)}[/green]'
            f'  [dim]({_fmt_bytes(total_orig_bytes)} → '
            f'{_fmt_bytes(total_new_bytes)}, {pct:.1f}%)[/dim]'
        )


if __name__ == '__main__':
    main()
