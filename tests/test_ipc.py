import os
import stat

import pytest

from sdesk import ipc

ROWS = [{"wid": 11, "title": "A", "wm_class": "a", "active": False, "hidden": False},
        {"wid": 22, "title": "B", "wm_class": "b", "active": True, "hidden": False},
        {"wid": 33, "title": "C", "wm_class": "c", "active": False, "hidden": True}]


def test_ping_and_unknown_commands():
    assert ipc.handle({"cmd": "ping"}, lambda: ROWS, None) == {"ok": True}
    assert ipc.handle({"cmd": "rm -rf"}, lambda: ROWS, None)["ok"] is False
    assert ipc.handle({}, lambda: ROWS, None)["ok"] is False


def test_windows_lists_the_strip_in_order_with_the_centred_one_and_hidden_ones_marked():
    r = ipc.handle({"cmd": "windows"}, lambda: ROWS, None, focused=lambda: 11)
    assert [w["wid"] for w in r["windows"]] == [11, 22, 33]
    assert [w["centered"] for w in r["windows"]] == [False, True, False]
    assert [w["hidden"] for w in r["windows"]] == [False, False, True]
    assert r["focused"] == 11


def test_focus_only_accepts_known_window_ids():
    got = []
    assert ipc.handle({"cmd": "focus", "wid": 22}, lambda: ROWS, got.append) == {"ok": True}
    assert got == [22]
    for bad in (999, "22", None, 22.0, [22]):
        assert ipc.handle({"cmd": "focus", "wid": bad}, lambda: ROWS, got.append)["ok"] is False
    assert got == [22]                                                    # nothing else was focused


@pytest.fixture
def server(tmp_path):
    path = str(tmp_path / "run" / "sdesk.sock")
    s = ipc.Server(lambda req: ipc.handle(req, lambda: ROWS, lambda wid: None), run_on_main=lambda fn: fn(), path=path)
    s.start()
    yield s, path
    s.stop()


def test_the_socket_is_private_to_the_user(server):
    _, path = server
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700


def test_a_real_roundtrip_over_the_socket(server):
    _, path = server
    assert ipc.request({"cmd": "ping"}, path=path) == {"ok": True}
    r = ipc.request({"cmd": "windows"}, path=path)
    assert [w["title"] for w in r["windows"]] == ["A", "B", "C"]


def test_garbage_does_not_crash_the_server(server):
    import socket
    _, path = server
    for junk in (b"not json\n", b"[1,2]\n", b"\xff\xfe\n", b"{}\n"):
        s = socket.socket(socket.AF_UNIX)
        s.connect(path)
        s.sendall(junk)
        assert b'"ok": false' in s.recv(4096)
        s.close()
    assert ipc.request({"cmd": "ping"}, path=path) == {"ok": True}        # still alive


def test_a_missing_sdesk_gives_none_not_an_exception(tmp_path):
    assert ipc.request({"cmd": "ping"}, path=str(tmp_path / "nothing.sock")) is None


def test_stopping_removes_the_socket(server):
    s, path = server
    s.stop()
    assert not os.path.exists(path)
