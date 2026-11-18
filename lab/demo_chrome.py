"""A stand-in for Chrome plus its sdesk extension, for the lab and the README recordings. No real Chrome, no real tabs.

    demo_chrome.py [port]

It opens a window of class google-chrome that draws a tab strip of invented tabs, and it talks to sdesk exactly like
chrome-extension/background.js does: it sends the tabs and their icons, and when sdesk says {"cmd": "activate"} it switches
tab (the window title follows). Closing the window ends it.
"""
import base64
import json
import os
import socket
import sys
import threading
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sdesk import ws  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 47631
TABS = [  # id, title, url, icon colour, letter
    (1, "Inbox", "https://mail.example/inbox", (0.85, 0.30, 0.25), "M"),
    (2, "Dashboard", "https://app.example/dashboard", (0.20, 0.50, 0.85), "D"),
    (3, "Pricing - Acme", "https://acme.example/pricing", (0.95, 0.60, 0.10), "A"),
    (4, "Docs: Getting started", "https://docs.example/start", (0.30, 0.70, 0.45), "D"),
    (5, "Design review", "https://design.example/review", (0.60, 0.35, 0.80), "R"),
    (6, "Weather", "https://weather.example/", (0.25, 0.65, 0.85), "W"),
    (7, "Recipes", "https://recipes.example/", (0.80, 0.40, 0.55), "R"),
]
state = {"active": 2, "used": {t[0]: 100 - i for i, t in enumerate(TABS)}, "sock": None}


def icon_png(colour, letter):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 32, 32)
    cr = cairo.Context(surf)
    cr.arc(16, 16, 15, 0, 6.3)
    cr.set_source_rgb(*colour)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(18)
    ext = cr.text_extents(letter)
    cr.move_to(16 - ext.width / 2 - ext.x_bearing, 16 - ext.height / 2 - ext.y_bearing)
    cr.show_text(letter)
    import io
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return base64.b64encode(buf.getvalue()).decode()


def snapshot():
    tabs = [{"id": i, "index": n, "title": t, "url": u, "active": i == state["active"], "lastAccessed": state["used"][i]}
            for n, (i, t, u, _c, _l) in enumerate(TABS)]
    return {"type": "tabs", "windows": [{"id": 1, "focused": True, "tabs": tabs}]}


def send(msg):
    s = state["sock"]
    if s is not None:
        try:
            s.sendall(ws.client_frame(json.dumps(msg)))
        except OSError:
            state["sock"] = None


def server_frame(buf):
    """(payload, rest) of the first complete unmasked text frame in ``buf``; (None, buf) if there is none yet."""
    if len(buf) < 2:
        return None, buf
    n, at = buf[1] & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None, buf
        n, at = int.from_bytes(buf[2:4], "big"), 4
    if len(buf) < at + n:
        return None, buf
    return buf[at:at + n], buf[at + n:]


def connect_loop(window):
    while True:
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=3)
            s.sendall((f"GET /tabs HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       "Origin: chrome-extension://demo\r\nSec-WebSocket-Key: ZGVtbyBjaHJvbWUgZXh0ZW5zaW9u\r\n"
                       "Sec-WebSocket-Version: 13\r\n\r\n").encode())
            data = b""
            while b"\r\n\r\n" not in data:
                data += s.recv(1024)
            s.settimeout(None)
            state["sock"] = s
            for (i, _t, u, c, l) in TABS:
                send({"type": "icon", "url": u, "png": icon_png(c, l)})
            send(snapshot())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while True:                                  # frames from the server are not masked
                    payload, buf = server_frame(buf)
                    if payload is None:
                        break
                    try:
                        msg = json.loads(payload)
                    except ValueError:
                        continue
                    if msg.get("cmd") == "activate":
                        GLib.idle_add(activate, window, msg["tabId"])
        except OSError:
            pass
        state["sock"] = None
        time.sleep(1)


def title_of(tab_id):
    return next(t for t in TABS if t[0] == tab_id)[1]


def activate(window, tab_id):
    state["active"] = tab_id
    state["used"][tab_id] = int(time.time())
    window.set_title(f"{title_of(tab_id)} - Google Chrome")
    window.queue_draw()
    send(snapshot())
    return False


def draw(widget, cr):
    w, h = widget.get_allocated_width(), widget.get_allocated_height()
    cr.set_source_rgb(0.87, 0.89, 0.93)
    cr.paint()
    cr.set_source_rgb(0.99, 0.99, 1)
    cr.rectangle(0, 92, w, h - 92)
    cr.fill()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    x = 14
    for (i, t, _u, c, _l) in TABS:
        tw = 150
        if x + tw > w - 20:
            break
        on = i == state["active"]
        cr.set_source_rgb(0.99, 0.99, 1) if on else cr.set_source_rgba(1, 1, 1, 0.35)
        cr.rectangle(x, 12, tw, 38)
        cr.fill()
        cr.arc(x + 20, 31, 8, 0, 6.3)
        cr.set_source_rgb(*c)
        cr.fill()
        cr.set_source_rgb(0.15, 0.17, 0.22)
        cr.set_font_size(13)
        cr.move_to(x + 36, 36)
        cr.show_text(t[:16])
        x += tw + 4
    cr.set_source_rgb(0.99, 0.99, 1)
    cr.rectangle(0, 50, w, 42)
    cr.fill()
    cr.set_source_rgb(0.92, 0.93, 0.96)
    cr.rectangle(70, 56, w - 140, 30)
    cr.fill()
    cr.set_source_rgb(0.35, 0.38, 0.45)
    cr.set_font_size(13)
    cr.move_to(86, 76)
    cr.show_text(next(t[2] for t in TABS if t[0] == state["active"]).replace("https://", ""))
    cr.set_source_rgb(0.15, 0.17, 0.22)
    cr.set_font_size(34)
    cr.move_to(70, 190)
    cr.show_text(title_of(state["active"]))
    for n in range(7):                                    # a page of invented content
        cr.set_source_rgb(0.90, 0.92, 0.96)
        cr.rectangle(70, 240 + n * 46, w * 0.55 - n * 40, 18)
        cr.fill()
    return False


def main():
    win = Gtk.Window(title=f"{title_of(state['active'])} - Google Chrome")
    win.set_wmclass("google-chrome", "Google-chrome")
    win.set_default_size(1100, 800)
    area = Gtk.DrawingArea()
    area.connect("draw", draw)
    win.add(area)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    threading.Thread(target=connect_loop, args=(win,), daemon=True).start()
    Gtk.main()


main()
