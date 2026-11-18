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
