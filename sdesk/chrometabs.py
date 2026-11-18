"""The tabs of Chrome's windows, as reported by sdesk's Chrome extension. No GTK in here.

Chrome tells other programs nothing about its tabs, so a small extension (``chrome-extension/``) does: it sends the list of
windows and tabs to this server whenever anything changes, and it carries out "switch to this tab". They talk over a
WebSocket on 127.0.0.1 (this computer only). Only connections that come from a browser extension are accepted; a web page
(whose origin is http or https) is refused, so no website can feed the taskbar.

Which X window is which Chrome window is not told either. They are matched by title: an X window is called after its active
tab. The match, once made, is kept, because Chrome's window ids do not change while the window lives.
"""
import base64
import binascii
import json
import socket
import threading

from . import favicons, ws

DEFAULT_PORT = 47631
MAX_TABS_SHOWN = 5
MAX_ICON_BYTES = 64 * 1024            # an icon bigger than this is not a favicon
MAX_ICONS = 300


def visible_tabs(tabs, limit=MAX_TABS_SHOWN):
    """(tabs to show, how many are left out): the active tab and the most recently used ones, in tab-strip order."""
    if len(tabs) <= limit:
        return sorted(tabs, key=lambda t: t.get("index", 0)), 0
    active = [t for t in tabs if t.get("active")][:1]
    rest = sorted((t for t in tabs if t not in active), key=lambda t: (-(t.get("lastAccessed") or 0), t.get("index", 0)))
    shown = active + rest[:max(0, limit - len(active))]
    return sorted(shown, key=lambda t: t.get("index", 0)), len(tabs) - len(shown)


def active_title(window):
    for t in window.get("tabs", []):
        if t.get("active"):
            return t.get("title") or ""
    return ""


def match_windows(x_titles, chrome_windows, cached=None, x_active=None):
    """{X window id: Chrome window dict}. ``x_titles``: {wid: X window title}. ``cached``: {wid: chrome window id} from before.

    A cached pair is kept as long as that Chrome window exists. New ones are matched on the active tab's title; when several
    Chrome windows show the same title, the focused one goes to the X window that has the focus, the rest in list order."""
    by_id = {w["id"]: w for w in chrome_windows}
    result, claimed = {}, set()
    for wid, cid in (cached or {}).items():
        if wid in x_titles and cid in by_id and cid not in claimed:
            result[wid] = by_id[cid]
            claimed.add(cid)
    for wid, title in x_titles.items():
        if wid in result:
            continue
        want = favicons.page_title(title)
        cands = [w for w in chrome_windows if w["id"] not in claimed and active_title(w) == want]
        if not cands:                                    # Chrome may add words to the window title ("- Audio playing")
            cands = [w for w in chrome_windows if w["id"] not in claimed and want and active_title(w)
                     and want.startswith(active_title(w))]
        if not cands:
            continue
        pick = next((w for w in cands if w.get("focused") and wid == x_active), cands[0])
        result[wid] = pick
        claimed.add(pick["id"])
    return result


class TabServer:
    """Listens on 127.0.0.1 for the extension. ``windows`` is the latest list it sent; ``on_change`` is called (from a worker
    thread) whenever it changes."""

    def __init__(self, port=DEFAULT_PORT, on_change=lambda: None, host="127.0.0.1"):
        self.host, self.port, self.on_change = host, port, on_change
        self.windows = []
        self._icons = {}                                             # page url -> PNG bytes, as Chrome shows them in its tab strip
        self._clients = []
        self._lock = threading.Lock()
        self._sock = None
        self._stop = threading.Event()

    # ---- lifecycle
    def start(self):
        """False if the port is taken (another sdesk, say): the taskbar then simply has no tabs."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            return False
        sock.listen(4)
        self.port = sock.getsockname()[1]
        self._sock = sock
        threading.Thread(target=self._accept_loop, daemon=True, name="chrome-tabs").start()
        return True

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            for c in self._clients:
                try:
                    c.close()
                except OSError:
                    pass

    @property
    def connected(self):
        with self._lock:
            return bool(self._clients)

    def snapshot(self):
        with self._lock:
            return list(self.windows)

    def icon_png(self, url):
        """The icon Chrome itself shows for a page (the extension sends it once for each address), or None."""
        with self._lock:
            return self._icons.get(url)

    def send(self, message):
        """A command for the extension, e.g. {"cmd": "activate", "tabId": 5, "windowId": 2}. False if nobody is connected."""
        frame = ws.encode_frame(json.dumps(message))
        with self._lock:
            clients = list(self._clients)
        ok = False
        for c in clients:
            try:
                c.sendall(frame)
                ok = True
            except OSError:
                pass
        return ok

    # ---- one connection
    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True, name="chrome-tabs-conn").start()

    def _handshake(self, conn):
        data = b""
        while len(data) < 16384:
            parsed = ws.parse_request(data)
            if parsed:
                break
            chunk = conn.recv(4096)
            if not chunk:
                return None
            data += chunk
        else:
            return None
        path, headers = parsed
        origin = headers.get("origin", "")
        if not origin.startswith("chrome-extension://"):            # a web page, or something else: not welcome
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return None
        conn.sendall(ws.handshake_response(headers))
        return data[data.find(b"\r\n\r\n") + 4:]

    def _serve(self, conn):
        conn.settimeout(90)                                          # the extension sends a heartbeat every 20 s
        try:
            leftover = self._handshake(conn)
        except (ws.ProtocolError, OSError):
            leftover = None
        if leftover is None:
            conn.close()
            return
        with self._lock:
            self._clients.append(conn)
        decoder = ws.Decoder()
        try:
            data = leftover
            while True:
                for op, payload in decoder.feed(data) if data else []:
                    if op == ws.OP_CLOSE:
                        return
                    if op == ws.OP_PING:
                        conn.sendall(ws.encode_frame(payload, ws.OP_PONG))
                    elif op == ws.OP_TEXT:
                        self._message(conn, payload)
                data = conn.recv(65536)
                if not data:
                    return
        except (ws.ProtocolError, OSError, ValueError):
            return
        finally:
            with self._lock:
                if conn in self._clients:
                    self._clients.remove(conn)
                gone = not self._clients and bool(self.windows)
                if gone:
                    self.windows = []                                # Chrome closed: nothing to show any more
            try:
                conn.close()
            except OSError:
                pass
            if gone:
                self.on_change()

    def _message(self, conn, payload):
        try:
            msg = json.loads(payload.decode("utf-8", "replace"))
        except ValueError:
            return                                                   # junk: ignore it, keep the connection
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "hb":                                  # heartbeat: answer, it keeps the extension awake too
            conn.sendall(ws.encode_frame(json.dumps({"type": "hb"})))
        elif msg.get("type") == "tabs" and isinstance(msg.get("windows"), list):
            windows = [w for w in msg["windows"] if isinstance(w, dict) and isinstance(w.get("tabs"), list) and "id" in w]
            with self._lock:
                changed = windows != self.windows
                self.windows = windows
            if changed:
                self.on_change()
        elif msg.get("type") == "icon" and isinstance(msg.get("url"), str) and isinstance(msg.get("png"), str):
            try:
                png = base64.b64decode(msg["png"], validate=True)
            except (binascii.Error, ValueError):
                return
            if not png.startswith(b"\x89PNG") or len(png) > MAX_ICON_BYTES:
                return
            with self._lock:
                if len(self._icons) >= MAX_ICONS:
                    self._icons.clear()
                changed = self._icons.get(msg["url"]) != png
                self._icons[msg["url"]] = png
            if changed:
                self.on_change()
