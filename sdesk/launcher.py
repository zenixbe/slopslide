"""Start / stop / toggle sdesk from a desktop icon:  python -m sdesk.launcher [toggle|start|stop|status]

It never matches processes loosely: only ``python -m sdesk`` ones count (see safety.is_sdesk). Stopping is a plain SIGTERM,
which makes sdesk put every window and the Deepin dock back; if it does not finish in time it is killed and its guardian
process does the same clean-up, so the desktop always comes back as it was."""
import os
import signal
import subprocess
import sys
import time

from . import safety

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(safety.STATE_DIR, "sdesk.log")
STOP_WAIT = 20.0            # the dock restart can take a few seconds
GUARDIAN_WAIT = 10.0


def running():
    """Pids of the running sdesk (not counting its guardian)."""
    out = []
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) != os.getpid() and safety.is_sdesk(int(name)):
            out.append(int(name))
    return out


def guardians():
    return [int(n) for n in os.listdir("/proc")
            if n.isdigit() and int(n) != os.getpid() and safety.is_sdesk(int(n), guardian=True)]


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


def start():
    if running():
        notify("Already running.")
        return 0
    os.makedirs(safety.STATE_DIR, exist_ok=True)
    with open(LOG, "ab") as log:
        subprocess.Popen([sys.executable, "-m", "sdesk", "--disable-dock"], cwd=ROOT, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True)
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
