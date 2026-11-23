"""Start / stop / toggle sdesk from a desktop icon:  python -m sdesk.launcher [toggle|start|stop|status]

It never matches processes loosely: only ``python -m sdesk`` ones of this desktop session count (see safety.is_own_sdesk:
same XDG_RUNTIME_DIR and DISPLAY, so a lab or test cannot stop the real sdesk). Stopping is a plain SIGTERM,
which makes sdesk put every window and the Deepin dock back; if it does not finish in time it is killed and its guardian
process does the same clean-up, so the desktop always comes back as it was.

Your own options for sdesk go in ``~/.config/sdesk/args`` (``$XDG_CONFIG_HOME/sdesk/args``), for example
``--sound-match logitech``: shell-style words, any number of lines, ``#`` starts a comment. The launcher adds them after
``--disable-dock``; a missing file means no extra options."""
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

from . import exitlog, safety

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(safety.STATE_DIR, "sdesk.log")
STOP_WAIT = 20.0            # the dock restart can take a few seconds
GUARDIAN_WAIT = 10.0


def args_file():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sdesk", "args")


def user_args(path=None):
    """The extra sdesk options from the user's args file (empty list if it does not exist)."""
    try:
        with open(path or args_file(), encoding="utf-8") as f:
            return shlex.split(f.read(), comments=True)
    except FileNotFoundError:
        return []


def running():
    """Pids of the running sdesk (not counting its guardian)."""
    out = []
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) != os.getpid() and safety.is_own_sdesk(int(name)):
            out.append(int(name))
    return out


def guardians():
    return [int(n) for n in os.listdir("/proc")
            if n.isdigit() and int(n) != os.getpid() and safety.is_own_sdesk(int(n), guardian=True)]


def notify(text):
    """Print, and show a desktop notification if the notification service is up (right after the dock was restored it may
    still be restarting: try again once; a missing notification is never an error)."""
    print(text, flush=True)
    for attempt in range(2):
        try:
            done = subprocess.run(["notify-send", "-a", "Scrolling Desktop", "-i", "sdesk", "Scrolling Desktop", text],
                                  timeout=5, check=False, stderr=subprocess.DEVNULL)
            if done.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            return
        time.sleep(2.0)


def _wait_gone(pids, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(safety.pid_alive(p) for p in pids):
            return True
        time.sleep(0.2)
    return False


def own_scope():
    """Command prefix that runs sdesk in its own systemd scope (``[]`` without systemd-run).

    Without it sdesk stays in the cgroup of whoever ran the launcher: a terminal, or an agent session. When that one
    ends, systemd stops its scope and sends SIGTERM to everything in it, sdesk included (issue #18)."""
    bus = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/nonexistent", "bus")
    if not shutil.which("systemd-run") or not os.path.exists(bus):         # no user manager here (a lab): start plainly
        return []
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--description=sdesk (scrolling desktop)"]


def start():
    if running():
        notify("Already running.")
        return 0
    os.makedirs(safety.STATE_DIR, exist_ok=True)
    with open(LOG, "ab") as log:
        subprocess.Popen([*own_scope(), sys.executable, "-m", "sdesk", "--disable-dock", *user_args()], cwd=ROOT,
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True)
    for _ in range(15):                                   # it must still be alive a moment later, or something is wrong
        time.sleep(0.4)
        if running():
            break
    time.sleep(1.0)
    if running():
        notify("Started: windows now scroll side by side.")
        return 0
    notify(f"Could not start. See {LOG}")
    return 1


def stop():
    pids = running()
    if not pids:
        notify("Not running.")
        return 0
    exitlog.write(f"stop via launcher (pid {os.getpid()}, started by {exitlog.describe_pid(os.getppid())}): "
                  f"SIGTERM to {', '.join(map(str, pids))}")
    for p in pids:
        os.kill(p, signal.SIGTERM)                        # sdesk puts the windows and the dock back itself
    if _wait_gone(pids, STOP_WAIT):
        notify("Stopped: windows and dock are back as before.")
        return 0
    for p in pids:                                        # stuck: the guardian undoes everything after a hard kill
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    _wait_gone(guardians(), GUARDIAN_WAIT)
    notify("Stopped (it had to be killed; windows and dock were restored).")
    return 0


def main(argv=None):
    cmd = (argv if argv is not None else sys.argv[1:] or ["toggle"])[0]
    if cmd == "status":
        print("running" if running() else "stopped")
        return 0
    if cmd == "start":
        return start()
    if cmd == "stop":
        return stop()
    if cmd == "toggle":
        return stop() if running() else start()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
