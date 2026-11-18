"""A tiny local control socket, so other tools (the launcher) can list sdesk's windows and focus one.

One JSON object per connection, one JSON answer. The socket lives in $XDG_RUNTIME_DIR (a 0700 directory) with mode 0600,
so only the same user can talk to it. Commands: {"cmd": "ping"}, {"cmd": "windows"}, {"cmd": "focus", "wid": N}.
"""
import json
import os
import socket
import threading

SOCKET = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/sdesk-{os.getuid()}", "sdesk.sock")


def windows_reply(rows, focused=None):
    """The answer to "windows": the strip in order, with what a launcher needs to show and pick them."""
    return {"ok": True, "focused": focused, "windows": [
        {"wid": r["wid"], "title": r["title"], "wm_class": r["wm_class"],
         "centered": bool(r["active"]), "hidden": bool(r["hidden"])} for r in rows]}


def handle(request, snapshot, focus, focused=lambda: None):
    """Pure command dispatch: ``snapshot()`` -> controller rows, ``focus(wid)`` focuses and centres a window."""
    cmd = request.get("cmd")
    if cmd == "ping":
        return {"ok": True}
    if cmd == "windows":
        return windows_reply(snapshot(), focused())
    if cmd == "focus":
        wid = request.get("wid")
        if not isinstance(wid, int) or wid not in [r["wid"] for r in snapshot()]:
            return {"ok": False, "error": "unknown window"}
        focus(wid)
        return {"ok": True}
    return {"ok": False, "error": "unknown command"}


class Server:
    """Listens in a thread; every request is run by ``run_on_main`` (the GTK main loop in the app)."""

    def __init__(self, handler, run_on_main, path=None, timeout=3.0):
        self.handler, self.run_on_main = handler, run_on_main
        self.path = path or SOCKET
        self.timeout = timeout
        self.sock = None
        self._running = False

    def start(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        try:
            os.remove(self.path)                    # a stale socket from a crashed run
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        os.chmod(self.path, 0o600)
        self.sock.listen(8)
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="sdesk-ipc").start()

    def _loop(self):
        while self._running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            conn.settimeout(self.timeout)
            data = b""
            while b"\n" not in data and len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            try:
                request = json.loads(data.decode("utf-8") or "{}")
                if not isinstance(request, dict):
                    raise ValueError
            except ValueError:
                reply = {"ok": False, "error": "bad request"}
            else:
                done, box = threading.Event(), {}

                def run():
                    try:
                        box["reply"] = self.handler(request)
                    except Exception as e:           # never let a bad request take the main loop down
                        box["reply"] = {"ok": False, "error": str(e)}
                    done.set()
                    return False

                self.run_on_main(run)
                reply = box.get("reply") if done.wait(self.timeout) else {"ok": False, "error": "timeout"}
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self):
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        try:
            os.remove(self.path)
        except OSError:
            pass


def request(payload, path=None, timeout=1.0):
    """Client side: send one command to a running sdesk. Returns the reply dict, or None if sdesk is not there."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(path or SOCKET)
        s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        return json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        s.close()
