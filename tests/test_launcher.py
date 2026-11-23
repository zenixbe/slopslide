from sdesk import launcher


def test_toggle_starts_when_stopped_and_stops_when_running(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "start", lambda: calls.append("start") or 0)
    monkeypatch.setattr(launcher, "stop", lambda: calls.append("stop") or 0)
    monkeypatch.setattr(launcher, "running", lambda: [])
    launcher.main(["toggle"])
    monkeypatch.setattr(launcher, "running", lambda: [123])
    launcher.main(["toggle"])
    assert calls == ["start", "stop"]


def test_start_does_nothing_when_it_is_already_running(monkeypatch):
    said = []
    monkeypatch.setattr(launcher, "running", lambda: [123])
    monkeypatch.setattr(launcher, "notify", said.append)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")))
    assert launcher.start() == 0 and said == ["Already running."]


def test_stop_does_nothing_when_it_is_not_running(monkeypatch):
    said = []
    monkeypatch.setattr(launcher, "running", lambda: [])
    monkeypatch.setattr(launcher, "notify", said.append)
    assert launcher.stop() == 0 and said == ["Not running."]


def test_this_process_is_never_taken_for_sdesk():
    import os
    assert os.getpid() not in launcher.running()


def test_an_unknown_command_is_refused(capsys):
    assert launcher.main(["bogus"]) == 2


def test_user_args_are_read_from_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert launcher.user_args() == []                                       # no file: no extra options
    (tmp_path / "sdesk").mkdir()
    (tmp_path / "sdesk" / "args").write_text("# my headsets\n--sound-match logitech\n--bar 'top'  # comment\n")
    assert launcher.user_args() == ["--sound-match", "logitech", "--bar", "top"]


def test_start_passes_the_user_args_to_sdesk(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "sdesk").mkdir()
    (tmp_path / "sdesk" / "args").write_text("--sound-match logitech\n")
    monkeypatch.setattr(launcher, "LOG", str(tmp_path / "sdesk.log"))
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "notify", lambda text: None)
    spawned = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **k: spawned.append(cmd))
    state = iter([[]] + [[123]] * 50)
    monkeypatch.setattr(launcher, "running", lambda: next(state))
    assert launcher.start() == 0
    assert spawned[0][-5:] == ["-m", "sdesk", "--disable-dock", "--sound-match", "logitech"]
    assert spawned[0][:len(launcher.own_scope())] == launcher.own_scope()


def test_sdesk_gets_its_own_scope_only_when_a_user_manager_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert launcher.own_scope() == []                                   # no user bus here (a lab): start plainly
    (tmp_path / "bus").touch()
    scope = launcher.own_scope()
    assert scope[:3] == ["systemd-run", "--user", "--scope"]            # out of the caller's cgroup (issue #18)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.own_scope() == []
