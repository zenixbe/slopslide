"""Crash safety: if sdesk dies without cleaning up, put the desktop back.

sdesk parks far-away windows at the screen edges and removes Deepin's dock. A clean exit undoes both. A crash, ``kill -9``
or an out-of-memory kill cannot, so a tiny independent *guardian* process watches sdesk and does it instead. Everything it
needs is kept in $XDG_RUNTIME_DIR (tmpfs: gone at reboot, which is also what makes a reboot always start stock).

State files in $XDG_RUNTIME_DIR/sdesk/:
    originals.json   window id -> [x, y, w, h] as they were before sdesk took them over
    clean            exists once sdesk exited normally (the guardian then has nothing to do)
    guardian.pid     the running guardian
"""
import json
import os
import subprocess
import sys
import time

STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/sdesk-{os.getuid()}", "sdesk")
ORIGINALS = os.path.join(STATE_DIR, "originals.json")
CLEAN = os.path.join(STATE_DIR, "clean")
GUARDIAN_PID = os.path.join(STATE_DIR, "guardian.pid")


def _ensure():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)


def begin():
    """sdesk starts running: forget a previous clean exit."""
    _ensure()
    try:
        os.remove(CLEAN)
    except FileNotFoundError:
        pass


def save_originals(rects):
    """rects: {wid: (x, y, w, h)}. Written atomically, so a crash never leaves half a file."""
    _ensure()
    tmp = ORIGINALS + ".tmp"
    with open(tmp, "w") as f:
        json.dump({str(k): list(v) for k, v in rects.items()}, f)
    os.replace(tmp, ORIGINALS)


def load_originals():
    try:
        with open(ORIGINALS) as f:
            return {int(k): tuple(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, TypeError):
        return {}


MINIMIZED = os.path.join(STATE_DIR, "minimized.json")


def save_minimized(stamps):
    """When each minimised window was minimised ({window id: epoch seconds}): kept so a restart of sdesk does not reset it."""
    try:
        _ensure()
        with open(MINIMIZED, "w") as f:
            json.dump({str(k): v for k, v in stamps.items()}, f)
    except OSError:
        pass


def load_minimized():
    try:
        with open(MINIMIZED) as f:
            return {int(k): float(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def mark_clean():
    """sdesk exited normally: nothing left to restore."""
    _ensure()
    open(CLEAN, "w").close()
    for path in (ORIGINALS,):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def visible_rect(rect, screen_w, screen_h, margin=40):
    """The saved geometry, nudged so the window is certainly reachable (a window that started half off-screen is
    brought back at least partly into view)."""
    x, y, w, h = rect
    w, h = max(200, min(w, screen_w)), max(120, min(h, screen_h))
    x = max(-w + margin, min(x, screen_w - margin))
    y = max(0, min(y, screen_h - margin))
    return x, y, w, h


def restore_windows(xwm, originals):
    """Move every still-existing window back to where it was."""
    sw, sh = xwm.screen_size()
    alive = set(xwm._cardinals(xwm.root, "_NET_CLIENT_LIST"))
    from .layout import Rect
    for wid, rect in originals.items():
        if wid in alive:
            try:
                xwm.place(wid, Rect(*visible_rect(rect, sw, sh)))
            except Exception:
                pass
    xwm.d.sync()


def _args(pid):
    """The command line of a process as a list of arguments ([] if it is gone)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def is_sdesk(pid, guardian=False):
    """True only for a process started as ``python -m sdesk ...`` (``--guardian`` ones only if ``guardian``).

    Deliberately exact: it must never match an editor, a terminal or a shell that merely has "sdesk" somewhere in its
    command line, because the panic command signals whatever this says is sdesk."""
    args = _args(pid)
    runs_module = any(a == "-m" and b == "sdesk" for a, b in zip(args, args[1:]))
    return runs_module and (("--guardian" in args) == guardian)


def pid_alive(pid):
    """True while ``pid`` is a running sdesk (also guards against the pid having been reused by something else)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return is_sdesk(pid)


def guard(pid, restore_windows_fn, restore_dock_fn, alive=pid_alive, sleep=time.sleep, poll=1.0):
    """Wait for sdesk (``pid``) to end; if it did not exit cleanly, undo its changes. Returns what happened."""
    while alive(pid):
        sleep(poll)
    if os.path.exists(CLEAN):
        return "clean"
    restore_windows_fn()
    restore_dock_fn()
    return "restored"


def spawn_guardian():
    """Start the guardian for this process, detached (it must outlive a hard kill of sdesk)."""
    _ensure()
    try:
        with open(GUARDIAN_PID) as f:
            if is_sdesk(int(f.read().strip()), guardian=True):
                return                      # one is already watching
    except (OSError, ValueError):
        pass
    p = subprocess.Popen([sys.executable, "-m", "sdesk", "--guardian", str(os.getpid())], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    with open(GUARDIAN_PID, "w") as f:
        f.write(str(p.pid))


def session_ending():
    """True while the user session is being shut down (logout/reboot): then nothing may block."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() in ("stopping", "offline")
    except (OSError, subprocess.SubprocessError):
        return False
