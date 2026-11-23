import os
import signal
import subprocess
import sys

from sdesk import exitlog, safety


def _fake_environ(monkeypatch, envs):
    monkeypatch.setattr(safety, "_environ", lambda pid: envs.get(pid))


def test_same_session_needs_the_same_runtime_dir_and_display(monkeypatch):
    mine = {"XDG_RUNTIME_DIR": "/run/user/1000", "DISPLAY": ":0"}
    _fake_environ(monkeypatch, {
        1: {"XDG_RUNTIME_DIR": "/run/user/1000", "DISPLAY": ":0", "OTHER": "x"},   # the real sdesk
        2: {"XDG_RUNTIME_DIR": "/tmp/sdesk-lab/run", "DISPLAY": ":99"},           # a lab sdesk
        3: {"XDG_RUNTIME_DIR": "/run/user/1000", "DISPLAY": ":99"},               # other display only
        4: {"XDG_RUNTIME_DIR": "/tmp/sdesk-lab/run", "DISPLAY": ":0"},            # other runtime dir only
        5: {"DISPLAY": ":0"},                                                     # no runtime dir at all
    })
    assert safety.same_session(1, mine)
    assert not any(safety.same_session(p, mine) for p in (2, 3, 4, 5))
    assert not safety.same_session(6, mine)                                       # unreadable: leave it alone


def test_a_lab_never_targets_the_real_sdesk_and_the_desktop_never_a_lab(monkeypatch):
    real = {"XDG_RUNTIME_DIR": "/run/user/1000", "DISPLAY": ":0"}
    lab = {"XDG_RUNTIME_DIR": "/tmp/sdesk-lab/run", "DISPLAY": ":99"}
    _fake_environ(monkeypatch, {10: real, 20: lab})
    monkeypatch.setattr(safety, "is_sdesk", lambda pid, guardian=False: True)
    assert [p for p in (10, 20) if safety.is_own_sdesk(p, env=lab)] == [20]
    assert [p for p in (10, 20) if safety.is_own_sdesk(p, env=real)] == [10]


def test_environ_of_this_process_is_read():
    env = safety._environ(os.getpid())
    assert env is not None and env.get("PATH") == os.environ.get("PATH")
    assert safety.same_session(os.getpid())


def test_launcher_and_panic_only_signal_sdesk_of_this_session(monkeypatch):
    from sdesk import app, launcher
    monkeypatch.setattr(os, "listdir", lambda path: ["10", "20"] if path == "/proc" else [])
    monkeypatch.setattr(safety, "is_sdesk", lambda pid, guardian=False: not guardian)
    monkeypatch.setattr(safety, "same_session", lambda pid, env=None: pid == 20)
    assert launcher.running() == [20]
    killed = []
    monkeypatch.setattr(app.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(exitlog, "write", lambda text, path=None: None)
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(safety, "load_originals", lambda: {})
    monkeypatch.setattr(app.dock, "restore", lambda mode: None)
    monkeypatch.setattr(safety, "mark_clean", lambda: None)
    app.panic()
    assert killed == [20]


def test_describe_names_the_sender_and_its_parent():
    text = exitlog.describe(signal.SIGTERM, 0, os.getpid())
    assert text.startswith(f"stopping: SIGTERM from pid {os.getpid()} (")
    assert f"its parent {os.getppid()} (" in text
    assert exitlog.describe(signal.SIGHUP, 0x80, 0) == "stopping: SIGHUP from the kernel (si_code 128)"
    assert "(gone)" in exitlog.describe(signal.SIGINT, 0, 2 ** 22 + 1)          # above pid_max: never a process


def test_write_appends_one_timestamped_line(tmp_path):
    log = tmp_path / "sub" / "sdesk.log"
    exitlog.write("stop via launcher", str(log))
    exitlog.write("stopping: SIGTERM", str(log))
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and lines[0].endswith(f"sdesk[{os.getpid()}]: stop via launcher")
    assert lines[0][:4].isdigit() and lines[0][10] == " "


WAITER = r"""
import os, signal, sys, threading, time
from sdesk import exitlog
exitlog.LOG = sys.argv[1]
exitlog.block()
done = threading.Event()
exitlog.watch(lambda signo: done.set())
print("ready", flush=True)
done.wait(10)
child = __import__("subprocess").run(["grep", "SigBlk", "/proc/self/status"], capture_output=True, text=True).stdout
with exitlog.unblocked():
    free = __import__("subprocess").run(["grep", "SigBlk", "/proc/self/status"], capture_output=True, text=True).stdout
print(child.split()[1], free.split()[1], flush=True)
"""


def test_a_real_sigterm_is_logged_with_its_sender(tmp_path):
    log = tmp_path / "sdesk.log"
    p = subprocess.Popen([sys.executable, "-c", WAITER, str(log)], stdout=subprocess.PIPE, text=True,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        assert p.stdout.readline().strip() == "ready"
        os.kill(p.pid, signal.SIGTERM)                       # only ever this test's own child
        blocked, free = p.stdout.readline().split()
        assert p.wait(10) == 0                               # it did not die from the signal: the waiter got it
    finally:
        if p.poll() is None:
            p.kill()
    line = log.read_text().strip()
    assert f"stopping: SIGTERM from pid {os.getpid()} (" in line
    assert int(blocked, 16) & (1 << (signal.SIGTERM - 1))      # children inherit the blocked mask ...
    assert not int(free, 16) & (1 << (signal.SIGTERM - 1))     # ... except inside unblocked()
