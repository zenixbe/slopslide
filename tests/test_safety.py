import json
import subprocess

import pytest

from sdesk import dock, safety


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    d = tmp_path / "sdesk"
    monkeypatch.setattr(safety, "STATE_DIR", str(d))
    monkeypatch.setattr(safety, "ORIGINALS", str(d / "originals.json"))
    monkeypatch.setattr(safety, "CLEAN", str(d / "clean"))
    monkeypatch.setattr(safety, "GUARDIAN_PID", str(d / "guardian.pid"))
    return d


def test_originals_survive_a_roundtrip_and_a_missing_or_broken_file_is_empty(state_dir):
    assert safety.load_originals() == {}
    safety.save_originals({123: (10, 20, 300, 400), 456: (-50, 0, 800, 600)})
    assert safety.load_originals() == {123: (10, 20, 300, 400), 456: (-50, 0, 800, 600)}
    (state_dir / "originals.json").write_text("{not json")
    assert safety.load_originals() == {}                        # a half-written file must never crash the guardian


def test_saving_is_atomic_no_temp_file_is_left(state_dir):
    safety.save_originals({1: (0, 0, 10, 10)})
    assert sorted(p.name for p in state_dir.iterdir()) == ["originals.json"]


def test_state_lives_in_the_volatile_runtime_dir_not_in_home():
    import os
    real = safety.os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sdesk")
    assert not real.startswith(os.path.expanduser("~"))         # tmpfs: gone at reboot


def test_a_saved_geometry_is_made_reachable():
    # fully off-screen on the right, huge, and above the screen: all brought back
    x, y, w, h = safety.visible_rect((9000, -300, 7000, 5000), 5120, 1440)
    assert w <= 5120 and h <= 1440 and 0 <= y < 1440 and x < 5120 - 20
    assert safety.visible_rect((100, 50, 800, 600), 5120, 1440) == (100, 50, 800, 600)     # sane ones are untouched
    x, y, w, h = safety.visible_rect((-5000, 10, 800, 600), 5120, 1440)
    assert x + w > 0                                                                         # off the left edge: pulled back


def test_the_guardian_waits_while_sdesk_lives_then_restores_after_a_crash():
    alive = iter([True, True, True, False])
    log = []
    result = safety.guard(42, lambda: log.append("windows"), lambda: log.append("dock"),
                          alive=lambda pid: next(alive), sleep=lambda s: log.append("sleep"))
    assert result == "restored"
    assert log == ["sleep", "sleep", "sleep", "windows", "dock"]           # windows first, then the dock


def test_the_guardian_does_nothing_after_a_clean_exit(state_dir):
    safety.mark_clean()
    log = []
    result = safety.guard(42, lambda: log.append("windows"), lambda: log.append("dock"), alive=lambda pid: False)
    assert result == "clean" and log == []


def test_begin_forgets_an_earlier_clean_exit(state_dir):
    safety.mark_clean()
    assert (state_dir / "clean").exists()
    safety.begin()
    assert not (state_dir / "clean").exists()                              # now a crash would be noticed again


def test_marking_clean_removes_the_saved_geometries(state_dir):
    safety.save_originals({1: (0, 0, 10, 10)})
    safety.mark_clean()
    assert not (state_dir / "originals.json").exists()


def test_session_ending_detection(monkeypatch):
    def fake(out):
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
    for out, expect in (("running\n", False), ("degraded\n", False), ("stopping\n", True), ("offline\n", True)):
        monkeypatch.setattr(safety.subprocess, "run", fake(out))
        assert safety.session_ending() is expect
    monkeypatch.setattr(safety.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 2)))
    assert safety.session_ending() is False                                 # when in doubt: behave normally


# ---------------------------------------------------------------- dock restore modes
@pytest.fixture
def dropin(tmp_path, monkeypatch):
    f = tmp_path / "x.d" / "sdesk-no-dock.conf"
    f.parent.mkdir()
    f.write_text("[Service]\n")
    monkeypatch.setattr(dock, "DROPIN", str(f))
    calls = []
    monkeypatch.setattr(dock, "_systemctl", lambda *a, **k: calls.append(a))
    return f, calls


def test_restoring_while_the_session_ends_only_removes_the_file(dropin):
    f, calls = dropin
    dock.enable("never")
    assert not f.exists() and calls == []                                  # nothing can block a logout or reboot


def test_shutdown_restore_does_not_wait_for_systemctl(dropin):
    f, calls = dropin
    dock.enable("no-block")
    assert not f.exists()
    assert ("restart", "--no-block", dock.UNIT) in calls


def test_normal_restore_waits_and_does_nothing_if_there_is_no_override(dropin):
    f, calls = dropin
    dock.enable("block")
    assert ("restart", dock.UNIT) in calls
    calls.clear()
    dock.enable("block")                                                   # already gone: no pointless dde-shell restart
    assert calls == []


# ---------------------------------------------------- recognising sdesk processes (the panic command signals them)
@pytest.mark.parametrize("cmdline,is_main,is_guardian", [
    (["/x/.venv/bin/python", "-m", "sdesk", "--disable-dock"], True, False),
    (["python", "-m", "sdesk"], True, False),
    (["python3", "-m", "sdesk", "--guardian", "123"], False, True),
    # things that merely mention sdesk must never match:
    (["vim", "sdesk/app.py"], False, False),
    (["bash", "-c", "cd ~/dev/projects/scrolling-desktop && python -m sdesk --help"], False, False),
    (["tail", "-f", "/tmp/sdesk/sdesk.log"], False, False),
    (["python", "-m", "pytest", "tests/test_sdesk.py"], False, False),
    (["claude", "--add-dir", "/home/user/sdesk"], False, False),
    (["python", "-m", "sdesk_other"], False, False),
    ([], False, False),
])
def test_only_a_real_python_m_sdesk_process_counts_as_sdesk(monkeypatch, cmdline, is_main, is_guardian):
    monkeypatch.setattr(safety, "_args", lambda pid: cmdline)
    assert safety.is_sdesk(1) is is_main
    assert safety.is_sdesk(1, guardian=True) is is_guardian


def test_a_dead_or_reused_pid_is_not_alive(monkeypatch):
    monkeypatch.setattr(safety.os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    assert safety.pid_alive(99999) is False
    monkeypatch.setattr(safety.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(safety, "_args", lambda pid: ["firefox"])           # pid reused by something else
    assert safety.pid_alive(99999) is False
