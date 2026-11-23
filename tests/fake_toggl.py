"""A stand-in for Second Brain's Toggl API, for the tests and the lab. Never talks to Toggl or to the real Second Brain.

It follows the contract of zenixbe/secondbrain#1: GET /current has ``fetched_at``; PATCH /current takes
{description?, project_id?} and answers like GET /current (404 when nothing runs, 400 on a bad body); POST /start stops a
running timer first. ``patch=False`` plays the Second Brain of today, which has no PATCH yet.

Lab control (not part of the real API): ``POST /__fake`` with any of
{"fail": 502|0, "patch": bool, "running": null | {"project_id", "description", "hours"}}.

All names are invented.
"""
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLIENTS = ["Bakkerij Noord", "Bakfietsen Oost", "Atelier Vos", "Brouwerij De Kade", "Camping Zonneveld", "Dierenarts Lindt",
           "Fietsen Peeters", "Garage Meulen", "Hotel Duinzicht", "Kapsalon Iris", "Loodgieter Smet", "Makelaar Baert",
           "Notaris Claes", "Optiek Ruys", "Pizzeria Bella", "Schrijnwerk Aerts", "Tandarts Wauters", "Yoga Studio Licht",
           "Zeilclub Oostende", "Zorgcentrum Hof"]
TOPICS = ["website", "webshop", "campagne", "nieuwsbrief", "logo"]


def make_projects(n_active=97, n_inactive=3):
    out = []
    for i in range(n_active + n_inactive):
        client = CLIENTS[i % len(CLIENTS)]
        topic = TOPICS[(i // len(CLIENTS)) % len(TOPICS)]
        out.append({"id": str(100000 + i), "name": f"{client.split()[-1].lower()} {topic}", "mapped_client": client,
                    "billing": "direct", "rate": 50.0, "active": 0 if i >= n_active else 1, "track": 1,
                    "last_entry": f"2026-11-{(i % 22) + 1:02d}T09:00:00"})
    return out


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def make_entries(projects, now=None):
    """A few finished entries: some today, some earlier, with doubles (same project + description)."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).astimezone().replace(hour=8, minute=30, second=0, microsecond=0).timestamp()
    p = projects
    rows = [(p[0], "Nieuwsbrief november", today, 5400), (p[1], "Productfoto's webshop", today + 6000, 3600),
            (p[0], "Nieuwsbrief november", today - 86400, 6000), (p[2], "Logo-varianten", today - 2 * 86400, 7200),
            (p[3], "Etiketten bier", today - 3 * 86400, 2700), (p[4], "Reservatiemodule", today - 4 * 86400, 9900),
            (p[5], "Afsprakenformulier", today - 5 * 86400, 1800)]
    return [{"id": f"e{i}", "external_id": str(9000 + i), "occurred_at": _iso(start), "description": desc,
             "duration_sec": secs, "duration_h": round(secs / 3600, 2), "project": proj["name"], "project_id": proj["id"],
             "client": proj["mapped_client"], "billable": True, "billed": False}
            for i, (proj, desc, start, secs) in enumerate(rows) if start < now]


class FakeToggl:
    def __init__(self, patch=True, projects=None, entries=None):
        self.patch = patch
        self.fail = 0                       # answer every API call with this status (502 = Toggl down)
        self.projects = projects if projects is not None else make_projects()
        self.entries = entries if entries is not None else make_entries(self.projects)
        self.running = None                 # {"id", "project_id", "description", "start" (epoch)}
        self.calls = []                     # (method, path, body)
        self.lock = threading.Lock()
        self._server = None
        self._next = 1

    # ---------------------------------------------------------------- state
    def describe(self):
        r = self.running
        if not r:
            return None
        p = next((p for p in self.projects if p["id"] == r["project_id"]), {})
        hours = (time.time() - r["start"]) / 3600
        return {"id": r["id"], "project_id": r["project_id"], "project": p.get("name", ""),
                "client": p.get("mapped_client", ""), "rate": p.get("rate"), "description": r["description"],
                "start": datetime.fromtimestamp(r["start"], timezone.utc).isoformat(), "billable": True,
                "hours": round(hours, 2), "too_long": hours > 16.0, "threshold": 16.0}

    def set_running(self, project_id, description, hours=0.0):
        self.running = {"id": str(self._next), "project_id": str(project_id), "description": description,
                        "start": time.time() - hours * 3600}
        self._next += 1

    def _finish(self):
        r, self.running = self.running, None
        secs = int(time.time() - r["start"])
        p = next((p for p in self.projects if p["id"] == r["project_id"]), {})
        e = {"id": "f" + r["id"], "external_id": r["id"], "occurred_at": _iso(r["start"]), "description": r["description"],
             "duration_sec": secs, "duration_h": round(secs / 3600, 2), "project": p.get("name", ""),
             "project_id": r["project_id"], "client": p.get("mapped_client", ""), "billable": True, "billed": False}
        self.entries.insert(0, e)
        return {"id": r["id"], "description": r["description"], "project": e["project"], "client": e["client"],
                "hours": e["duration_h"], "seconds": secs, "too_long": secs / 3600 > 16}

    def openapi(self):
        current = {"get": {}}
        if self.patch:
            current["patch"] = {}
        return {"paths": {"/api/ws/toggl/current": current, "/api/ws/toggl/start": {"post": {}},
                          "/api/ws/toggl/stop": {"post": {}}}}

    # ---------------------------------------------------------------- HTTP
    def handle(self, method, path, query, body):
        """(status, json) for one request."""
        with self.lock:
            self.calls.append((method, path, body))
            if path == "/__fake":
                if "fail" in body:
                    self.fail = int(body["fail"] or 0)
                if "patch" in body:
                    self.patch = bool(body["patch"])
                if "running" in body:
                    r = body["running"]
                    if r:
                        self.set_running(r["project_id"], r["description"], float(r.get("hours", 0)))
                    else:
                        self.running = None
                return 200, {"ok": True}
            if path == "/openapi.json":
                return 200, self.openapi()
            if self.fail:
                return self.fail, {"detail": f"Toggl weigerde het: fake error {self.fail}"}
            fetched = datetime.now(timezone.utc).isoformat()
            if path == "/api/ws/toggl/current" and method == "GET":
                return 200, {"running": self.describe(), "fetched_at": fetched}
            if path == "/api/ws/toggl/current" and method == "PATCH":
                if not self.patch:
                    return 405, {"detail": "Method Not Allowed"}
                if not self.running:
                    return 404, {"detail": "er loopt geen timer"}
                if not isinstance(body, dict) or not ({"description", "project_id"} & set(body)):
                    return 400, {"detail": "niets te wijzigen"}
                if "description" in body:
                    if not str(body["description"]).strip():
                        return 400, {"detail": "geef een omschrijving"}
                    if len(str(body["description"]).strip()) > 400:
                        return 400, {"detail": "omschrijving is langer dan 400 tekens"}
                    self.running["description"] = str(body["description"]).strip()
                if "project_id" in body:
                    if not any(p["id"] == str(body["project_id"]) for p in self.projects):
                        return 400, {"detail": "onbekend project"}
                    self.running["project_id"] = str(body["project_id"])
                return 200, {"running": self.describe(), "fetched_at": fetched}
            if path == "/api/ws/toggl/projects":
                return 200, {"projects": self.projects}
            if path == "/api/ws/toggl/entries":
                rows = sorted(self.entries, key=lambda e: e["occurred_at"], reverse=True)
                month = query.get("month", "")
                rows = [e for e in rows if e["occurred_at"].startswith(month)]
                limit = int(query.get("limit", 200))
                return 200, {"total": len(rows), "limit": limit, "offset": 0, "entries": rows[:limit]}
            if path == "/api/ws/toggl/start" and method == "POST":
                pid, desc = str(body.get("project_id") or ""), str(body.get("description") or "").strip()
                if not pid:
                    return 400, {"detail": "kies een project"}
                if not desc:
                    return 400, {"detail": "geef een omschrijving"}
                if not any(p["id"] == pid for p in self.projects):
                    return 400, {"detail": f"project {pid} is hier niet bekend"}
                stopped = self._finish() if self.running else None
                self.set_running(pid, desc)
                return 200, {"started": self.describe(), "stopped": stopped}
            if path == "/api/ws/toggl/stop" and method == "POST":
                if not self.running:
                    return 404, {"detail": "er liep geen timer"}
                return 200, self._finish()
            return 404, {"detail": "Not Found"}

    def serve(self, port=0):
        """Start on 127.0.0.1:port (0 = any free port) in a thread; returns the base URL."""
        fake = self

        class H(BaseHTTPRequestHandler):
            def _go(self, method):
                path, _, qs = self.path.partition("?")
                query = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
                except ValueError:
                    body = {}
                status, data = fake.handle(method, path, query, body)
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._go("GET")

            def do_POST(self):
                self._go("POST")

            def do_PATCH(self):
                self._go("PATCH")

            def log_message(self, *a):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def shutdown(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


if __name__ == "__main__":
    import sys
    fake = FakeToggl(patch="--no-patch" not in sys.argv)
    port = int(next((a for a in sys.argv[1:] if a.isdigit()), "18769"))
    print("fake Toggl API on", fake.serve(port), flush=True)
    while True:
        time.sleep(3600)
