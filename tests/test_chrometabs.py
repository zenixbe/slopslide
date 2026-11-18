import base64
import json
import socket
import threading
import time

from sdesk import chrometabs, ws


def tab(i, title, active=False, used=0, url=None):
    return {"id": i, "index": i, "title": title, "url": url or f"https://e.example/{i}", "active": active, "lastAccessed": used}


def window(wid, tabs, focused=False):
    return {"id": wid, "focused": focused, "tabs": tabs}


# ---------------------------------------------------------------- choosing the tabs to show
def test_up_to_the_limit_every_tab_is_shown_in_strip_order():
    tabs = [tab(2, "c"), tab(0, "a", active=True), tab(1, "b")]
    shown, more = chrometabs.visible_tabs(tabs)
    assert [t["id"] for t in shown] == [0, 1, 2] and more == 0


def test_over_the_limit_the_active_and_most_recent_tabs_stay_in_strip_order():
    tabs = [tab(i, f"t{i}", used=i * 10) for i in range(10)]
    tabs[2]["active"] = True                                    # long unused, but it is the one on screen
    shown, more = chrometabs.visible_tabs(tabs, limit=5)
    assert [t["id"] for t in shown] == [2, 6, 7, 8, 9] and more == 5
    assert len(chrometabs.visible_tabs(tabs, limit=1)[0]) == 1


def test_without_an_active_tab_the_limit_still_holds():
    tabs = [tab(i, f"t{i}", used=i) for i in range(8)]
    shown, more = chrometabs.visible_tabs(tabs, limit=3)
    assert [t["id"] for t in shown] == [5, 6, 7] and more == 5


# ----------------------------------------------------------- which X window is which Chrome window
def test_windows_are_matched_on_the_title_of_the_active_tab():
    chrome = [window(1, [tab(0, "Inbox", active=True)]), window(2, [tab(0, "Docs", active=True), tab(1, "Other")])]
    got = chrometabs.match_windows({100: "Docs - Google Chrome", 200: "Inbox - Google Chrome"}, chrome)
    assert got[100]["id"] == 2 and got[200]["id"] == 1


def test_a_match_that_was_made_is_kept_when_the_title_changes():
    chrome = [window(1, [tab(0, "Loading…", active=True)])]
    got = chrometabs.match_windows({100: "Something else - Google Chrome"}, chrome, cached={100: 1})
    assert got[100]["id"] == 1
    assert chrometabs.match_windows({100: "x"}, chrome, cached={100: 99}) == {}              # that window is gone


def test_equal_titles_go_to_the_focused_window_first():
    chrome = [window(1, [tab(0, "New Tab", active=True)]), window(2, [tab(0, "New Tab", active=True)], focused=True)]
    got = chrometabs.match_windows({100: "New Tab - Google Chrome", 200: "New Tab - Google Chrome"}, chrome, x_active=200)
    assert got[200]["id"] == 2 and got[100]["id"] == 1                                       # and never the same window twice


def test_extra_words_in_the_window_title_still_match():
    chrome = [window(1, [tab(0, "Radio", active=True)])]
    got = chrometabs.match_windows({100: "Radio - Audio playing - Google Chrome"}, chrome)
    assert got[100]["id"] == 1


def test_a_window_without_a_match_is_left_alone():
    assert chrometabs.match_windows({100: "Unknown - Google Chrome"}, [window(1, [tab(0, "Inbox", active=True)])]) == {}


# --------------------------------------------------------------------------- the server
def connect(port, origin="chrome-extension://abcdef", key="dGhlIHNhbXBsZSBub25jZQ=="):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(("GET /tabs HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n" + (f"Origin: {origin}\r\n" if origin else "") + "\r\n").encode())
    return s


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.01)
    return cond()


def read_http(s):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(1024)
        if not chunk:
            break
        data += chunk
    return data


def test_an_extension_connects_sends_tabs_and_gets_commands():
    seen = []
    server = chrometabs.TabServer(port=0, on_change=lambda: seen.append(1))
    assert server.start()
    try:
        s = connect(server.port)
        assert read_http(s).startswith(b"HTTP/1.1 101")
        assert wait_for(lambda: server.connected)
        msg = {"type": "tabs", "windows": [window(7, [tab(0, "Inbox", active=True)])]}
        s.sendall(ws.client_frame(json.dumps(msg)))
        assert wait_for(lambda: server.snapshot() == msg["windows"]) and seen
        assert server.send({"cmd": "activate", "tabId": 3, "windowId": 7})
        dec = ws.Decoder()                                        # the client side only needs to read unmasked frames
        raw = s.recv(4096)
        assert raw[0] == 0x81 and json.loads(raw[2:]) == {"cmd": "activate", "tabId": 3, "windowId": 7}
        s.close()
        assert wait_for(lambda: not server.connected) and wait_for(lambda: server.snapshot() == [])
    finally:
        server.stop()


def test_a_web_page_is_refused_and_so_is_a_client_without_origin():
    server = chrometabs.TabServer(port=0)
    assert server.start()
    try:
        for origin in ("https://evil.example", "http://localhost:3000", None):
            s = connect(server.port, origin=origin)
            assert read_http(s).startswith(b"HTTP/1.1 403")
            s.close()
        assert not server.connected
    finally:
        server.stop()


def test_garbage_and_wrong_messages_do_not_hurt_the_server():
    server = chrometabs.TabServer(port=0)
    assert server.start()
    try:
        s = connect(server.port)
        read_http(s)
        for junk in ("not json", json.dumps([1, 2]), json.dumps({"type": "tabs", "windows": "no"}),
                     json.dumps({"type": "tabs", "windows": [{"id": 1}, 5, {"id": 2, "tabs": []}]})):
            s.sendall(ws.client_frame(junk))
        time.sleep(0.2)
        assert server.snapshot() in ([], [{"id": 2, "tabs": []}])
        s.sendall(ws.client_frame(json.dumps({"type": "hb"})))
        assert s.recv(64)[0] == 0x81                               # still alive: the heartbeat is answered
    finally:
        server.stop()


def test_icons_sent_by_the_extension_are_kept_for_their_page_only():
    seen = []
    server = chrometabs.TabServer(port=0, on_change=lambda: seen.append(1))
    assert server.start()
    try:
        s = connect(server.port)
        read_http(s)
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        s.sendall(ws.client_frame(json.dumps({"type": "icon", "url": "http://localhost:3000/", "png": base64.b64encode(png).decode()})))
        assert wait_for(lambda: server.icon_png("http://localhost:3000/") == png) and seen
        assert server.icon_png("http://localhost:3001/") is None            # another page: no icon of someone else
        for bad in ("not base64!", base64.b64encode(b"GIF89a").decode(), base64.b64encode(b"\x89PNG" + b"x" * 70000).decode()):
            s.sendall(ws.client_frame(json.dumps({"type": "icon", "url": "http://bad/", "png": bad})))
        time.sleep(0.2)
        assert server.icon_png("http://bad/") is None
    finally:
        server.stop()


def test_a_second_server_on_a_taken_port_just_says_no():
    first = chrometabs.TabServer(port=0)
    assert first.start()
    try:
        assert chrometabs.TabServer(port=first.port).start() is False
    finally:
        first.stop()


def test_sending_with_nobody_connected_reports_it():
    assert chrometabs.TabServer(port=0).send({"cmd": "x"}) is False
