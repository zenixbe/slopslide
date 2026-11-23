"""Why sdesk stopped: one timestamped line per exit in $XDG_RUNTIME_DIR/sdesk/sdesk.log (issue #18).

A stop signal (SIGTERM, SIGINT, SIGHUP) says who sent it only in its siginfo, and only ``sigwaitinfo`` gives that to
Python. So sdesk blocks these signals from the very start (``sdesk/__main__.py``, before any thread exists, so every
thread inherits the mask) and one small thread waits for them. It writes the signal, the sender pid with its command
line and the sender's parent, then asks the GLib main loop to shut down as before.

A blocked signal mask is inherited by child processes. Programs that sdesk starts for the user must therefore be
started inside ``unblocked()``, or they could not be stopped with SIGTERM later."""
import contextlib
import os
import signal
import threading
import time

from . import safety

LOG = os.path.join(safety.STATE_DIR, "sdesk.log")
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def write(text, path=None):
    """Append one timestamped line to sdesk.log (never fails: a missing line is better than a crash on exit)."""
    path = path or LOG
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} sdesk[{os.getpid()}]: {text}\n"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _parent(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def describe_pid(pid):
    """``1234 (python -m sdesk.launcher stop)``; ``1234 (gone)`` if it already ended."""
    args = safety._args(pid)
    return f"{pid} ({' '.join(args)[:300] if args else 'gone'})"


def describe(signo, code, sender):
    """One readable line for a received stop signal. ``code`` is siginfo's si_code: > 0 means the kernel sent it (for
    example SIGHUP when the terminal closes), otherwise ``sender`` is the pid that called kill()."""
    name = signal.Signals(signo).name
    if code > 0 or not sender:
        return f"stopping: {name} from the kernel (si_code {code})"
    text = f"stopping: {name} from pid {describe_pid(sender)}"
    parent = _parent(sender)
    if parent:
        text += f", its parent {describe_pid(parent)}"
    return text


def block():
    signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)


def unblock():
    signal.pthread_sigmask(signal.SIG_UNBLOCK, STOP_SIGNALS)


@contextlib.contextmanager
def unblocked():
    """Start child processes in here: they then get the normal signal mask instead of sdesk's blocked one."""
    old = signal.pthread_sigmask(signal.SIG_UNBLOCK, STOP_SIGNALS)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def watch(on_stop):
    """Wait for stop signals in a thread; log each one, then call ``on_stop(signo)`` (from that thread).

    Fallback: a plain Python handler for the same signals, for a signal that reaches a thread with the mask open (inside
    ``unblocked()``). It logs without a sender. Must be called from the main thread."""
    def fallback(signo, _frame):
        write(f"stopping: {signal.Signals(signo).name} (sender unknown)")
        on_stop(signo)

    for s in STOP_SIGNALS:
        signal.signal(s, fallback)
    block()

    def loop():
        while True:
            info = signal.sigwaitinfo(STOP_SIGNALS)
            write(describe(info.si_signo, info.si_code, info.si_pid))
            on_stop(info.si_signo)

    threading.Thread(target=loop, name="sdesk-exit-signals", daemon=True).start()
