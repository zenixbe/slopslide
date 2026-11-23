"""The Toggl timer of the taskbar: talks to Second Brain's Toggl API. No GTK in here.

Second Brain (``--toggl-url``, default http://127.0.0.1:8769) owns the Toggl token and does the real work:

* ``GET  /api/ws/toggl/current``   the running timer ({"running": {...} | null}); Second Brain caches it for ~30 s;
* ``GET  /api/ws/toggl/projects``  every project (only the ``active`` ones are offered);
* ``GET  /api/ws/toggl/entries``   finished entries, newest first (for "start again" and today's total);
* ``POST /api/ws/toggl/start``     {project_id, description}: stops a running timer first;
* ``POST /api/ws/toggl/stop``      404 when nothing runs;
* ``PATCH /api/ws/toggl/current``  {description?, project_id?}: new, so :meth:`Client.supports_patch` checks for it first.

Toggl entries feed the invoices: nothing here starts, stops or changes a timer by itself. Only :class:`Service` does, and
only when the user asked for it (a click, a key). Seconds are counted locally; ``/current`` is read at most every
``INTERVAL`` seconds and right after an own action, so Toggl's hourly quota is never at risk.
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone

DEFAULT_URL = "http://127.0.0.1:8769"
API = "/api/ws/toggl"
INTERVAL = 45                   # s between two reads of /current
ENTRIES_EVERY = 300             # s between two reads of the finished entries (today's total, "start again")
PROJECTS_EVERY = 900            # s between two reads of the projects (and of /openapi.json: is the PATCH there yet?)
FORGOT_H = 16.0                 # a timer running longer than this is probably forgotten (Second Brain sends its own value)
RECENT = 6                      # "start again" rows in the picker
DESC_MAX = 400                  # Second Brain refuses a longer description


# ------------------------------------------------------------------------------------------------------- formatting
def parse_time(text):
    """Epoch seconds of an ISO time from the API. Without an offset it is UTC (``occurred_at`` is Toggl's start in UTC)."""
    if not text:
        return None
    try:
        d = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def fmt_clock(seconds):
    """Running time as a stopwatch: 0:04:09, 1:23:45, 17:02:00."""
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_hm(seconds):
    """Hours and minutes: 4:33 (today's total, a past entry)."""
    m = max(0, int(seconds)) // 60
    return f"{m // 60}:{m % 60:02d}"


def elapsed(running, now=None):
    """Seconds a running entry has been going, counted locally from its start."""
    start = parse_time((running or {}).get("start"))
    if start is None:
        return float((running or {}).get("hours") or 0) * 3600
    return max(0.0, (time.time() if now is None else now) - start)


def forgot(running, now=None):
    """True when the timer has run so long that it was probably forgotten (``too_long``, or past the threshold by now)."""
    if not running:
        return False
    limit = float(running.get("threshold") or FORGOT_H)
    return bool(running.get("too_long")) or elapsed(running, now) / 3600 > limit


def project_label(p):
    """What a project is called in the bar and the picker: the client, plus the Toggl name when that says more."""
    client = (p.get("client") if "client" in p else p.get("mapped_client")) or ""
    name = p.get("project") if "project" in p else p.get("name")
    name = name or ""
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())       # noqa: E731
    if client and name and norm(name) not in norm(client) and norm(client) not in norm(name):
        return f"{client} · {name}"
    return client or name or "No project"


# --------------------------------------------------------------------------------------------------------- picking
def _key(e):
    return str(e.get("project_id") or ""), " ".join((e.get("description") or "").split()).lower()


def recent(entries, limit=RECENT):
    """The newest entries, one per project + description, as rows to start again. Entries without either are skipped
    (``/start`` needs both)."""
    out, seen = [], set()
    for e in sorted(entries, key=lambda e: str(e.get("occurred_at") or ""), reverse=True):
        k = _key(e)
        if not k[0] or not k[1] or k in seen:
            continue
        seen.add(k)
        out.append({"project_id": k[0], "description": " ".join(e["description"].split()),
                    "project": e.get("project") or "", "client": e.get("client") or "",
                    "when": parse_time(e.get("occurred_at")), "seconds": int(e.get("duration_sec") or 0)})
        if len(out) >= limit:
            break
    return out


def previous(entries, running=None):
    """The entry to go back to: the newest one that is not what runs now (extra 8)."""
    skip = _key(running) if running else None
    return next((r for r in recent(entries, RECENT + 1) if _key(r) != skip), None)


def match(query, *texts):
    """Every word of the query occurs in one of the texts (case-insensitive)."""
    hay = " ".join(t or "" for t in texts).lower()
    return all(w in hay for w in (query or "").lower().split())


def search_projects(projects, query="", limit=None):
    """Active projects that match the query: names that start with it first, then the most recently used."""
    found = [p for p in projects if p.get("active", 1) and match(query, p.get("name"), p.get("mapped_client"))]
    q = (query or "").strip().lower()

    def rank(p):
        starts = q and any(str(p.get(k) or "").lower().startswith(q) for k in ("name", "mapped_client"))
        last = _neg(str(p["last_entry"])) if p.get("last_entry") else "\U0010ffff"     # never used: last
        return (0 if starts else 1, last, project_label(p).lower())
    found.sort(key=rank)
    return found[:limit] if limit else found


def _neg(text):
    """Sort key that puts a later ISO time first."""
    return "".join(chr(0x10FFFF - ord(c)) for c in text)


# ------------------------------------------------------------------------------------------------- today, work hours
def today_seconds(entries, running=None, now=None):
    """Seconds tracked today (local time): finished entries that started today plus the running timer's part of today."""
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now).astimezone()
    midnight = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    total = 0
    for e in entries:
        t = parse_time(e.get("occurred_at"))
        if t is not None and midnight <= t < midnight + 86400:
            total += int(e.get("duration_sec") or 0)
    if running:
        start = parse_time(running.get("start"))
        start = now - elapsed(running, now) if start is None else start
        total += max(0, now - max(start, midnight))
    return total


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class WorkHours:
    """When you are supposed to have a timer running: "mon-fri 09:00-18:00" (days and/or times; "off" = never)."""
    days: frozenset = frozenset(range(5))
    start: int = 9 * 60          # minutes after midnight
    end: int = 18 * 60

    @classmethod
    def parse(cls, text):
        text = (text or "").strip().lower()
        if text in ("", "off", "never", "none"):
            return None
        days, start, end = set(), 9 * 60, 18 * 60
        for part in text.replace(",", " ").split():
            if ":" in part or part[:1].isdigit():
                a, b = part.split("-", 1)
                start, end = cls._minutes(a), cls._minutes(b)
            else:
                a, _, b = part.partition("-")
                if a not in DAYS or (b and b not in DAYS):
                    raise ValueError(f"unknown day in {text!r}: use mon..sun")
                i, j = DAYS.index(a), DAYS.index(b or a)
                days.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        if end <= start:
            raise ValueError(f"work hours end before they start: {text!r}")
        return cls(frozenset(days) if days else frozenset(range(5)), start, end)

    @staticmethod
    def _minutes(hhmm):
        h, _, m = hhmm.partition(":")
        h, m = int(h), int(m or 0)
        if not (0 <= h <= 24 and 0 <= m < 60):
            raise ValueError(f"bad time {hhmm!r}")
        return h * 60 + m

    def contains(self, when):
        """``when``: a local datetime."""
        minutes = when.hour * 60 + when.minute
        return when.weekday() in self.days and self.start <= minutes < self.end


def should_nag(running, hours, now_local, online=True):
    """Shake "Start timer": it is work time, no timer runs and we know that for sure (Second Brain answered)."""
    return bool(online and hours is not None and not running and hours.contains(now_local))


# ------------------------------------------------------------------------------------------------------------ client
class TogglError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message

    def __str__(self):
        return self.message


class Client:
    """Second Brain's Toggl API. Every call blocks: call it from :class:`Service`'s thread, never from GTK's."""

    def __init__(self, base=DEFAULT_URL, timeout=8.0):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _call(self, method, path, body=None, query=None):
        url = self.base + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode() or "{}").get("detail")
            except (ValueError, AttributeError, OSError):
                detail = None
            raise TogglError(e.code, str(detail or f"Second Brain answered {e.code}")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise TogglError(0, f"Second Brain is not reachable ({reason})") from None
        try:
            return json.loads(raw.decode() or "null")
        except ValueError:
            raise TogglError(0, "Second Brain sent something that is not JSON") from None

    def current(self):
        return (self._call("GET", API + "/current") or {}).get("running")

    def projects(self):
        return [p for p in (self._call("GET", API + "/projects") or {}).get("projects", []) if p.get("active", 1)]

    def entries(self, limit=200, month=""):
        query = {"limit": limit}
        if month:
            query["month"] = month
        return (self._call("GET", API + "/entries", query=query) or {}).get("entries", [])

    def supports_patch(self):
        """Has Second Brain got ``PATCH /api/ws/toggl/current`` yet? Read from its OpenAPI description."""
        paths = (self._call("GET", "/openapi.json") or {}).get("paths", {})
        return "patch" in paths.get(API + "/current", {})

    def start(self, project_id, description, billable=None):
        body = {"project_id": str(project_id), "description": description}
        if billable is not None:
            body["billable"] = bool(billable)
        return self._call("POST", API + "/start", body)

    def stop(self):
        """The stopped entry, or None if nothing was running."""
        try:
            return self._call("POST", API + "/stop", {})
        except TogglError as e:
            if e.status == 404:
                return None
            raise

    def patch_current(self, description=None, project_id=None):
        body = {k: v for k, v in (("description", description), ("project_id", project_id)) if v is not None}
        return (self._call("PATCH", API + "/current", body) or {}).get("running")


# ----------------------------------------------------------------------------------------------------------- service
@dataclass(frozen=True)
class State:
    """Everything the bar and the card show. A new object on every change (the GTK side only reads it)."""
    running: dict = None
    projects: tuple = ()
    entries: tuple = ()
    known: bool = False             # /current answered at least once: ``running`` is real
    error: str = ""                 # the last read failed: why (the bar shows it)
    action_error: str = ""          # the last start/stop/save failed: why (the card shows it)
    busy: str = ""                  # an action is under way ("start", "stop", "save")
    patch: bool = False             # Second Brain can change the running entry
    fetched: float = 0.0            # time.time() of the last good /current


class Service:
    """A thread that reads the API now and then and carries out what the user asked, one thing at a time.

    ``on_update(state)`` is called from that thread: wrap it in ``GLib.idle_add``.
    """

    def __init__(self, client, on_update, interval=None):
        self.client, self.on_update = client, on_update
        self.interval = INTERVAL if interval is None else interval
        self.state = State()
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self._next = {"current": 0.0, "entries": 0.0, "projects": 0.0}

    # -------------------------------------------------------------- called from GTK
    def start(self):
        self._thread = threading.Thread(target=self._run, name="sdesk-toggl", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._q.put(None)

    def refresh(self):
        """Read /current now (e.g. the card opens)."""
        self._q.put(("refresh",))

    def start_timer(self, project_id, description):
        self._q.put(("start", str(project_id), " ".join((description or "").split())))

    def stop_timer(self):
        self._q.put(("stop",))

    def save_description(self, description):
        self._q.put(("save", " ".join((description or "").split())))

    # -------------------------------------------------------------- the thread
    def _set(self, **kw):
        self.state = replace(self.state, **kw)
        try:
            self.on_update(self.state)
        except Exception:                       # the bar must never take the thread down
            import traceback
            traceback.print_exc()

    def _run(self):
        while not self._stop.is_set():
            self._poll_due()
            wait = max(0.5, min(self._next.values()) - time.monotonic())
            try:
                job = self._q.get(timeout=wait)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                self._do(job)
            except Exception as e:              # a bug here must not end the thread
                import traceback
                traceback.print_exc()
                self._set(busy="", action_error=f"sdesk error: {e}")

    def _poll_due(self):
        now = time.monotonic()
        if now >= self._next["projects"]:
            self._next["projects"] = now + PROJECTS_EVERY
            self._read_projects()
        if now >= self._next["entries"]:
            self._next["entries"] = now + ENTRIES_EVERY
            self._read_entries()
        if now >= self._next["current"]:
            self._read_current()

    def _read_current(self):
        self._next["current"] = time.monotonic() + self.interval
        try:
            running = self.client.current()
        except TogglError as e:
            self._next["current"] = time.monotonic() + min(self.interval, 20)     # retry a bit sooner while it is down
            self._set(error=str(e))
            return
        self._set(running=running, known=True, error="", fetched=time.time())

    def _read_entries(self):
        try:
            entries = self.client.entries(limit=200)              # newest first: covers today and "start again"
            self._set(entries=tuple(entries))
        except TogglError as e:
            self._next["entries"] = time.monotonic() + 30
            self._set(error=str(e))

    def _read_projects(self):
        try:
            projects = self.client.projects()
            self._set(projects=tuple(projects))
        except TogglError as e:
            self._next["projects"] = time.monotonic() + 30
            self._set(error=str(e))
            return
        try:
            self._set(patch=self.client.supports_patch())
        except TogglError:
            pass

    def _do(self, job):
        kind = job[0]
        if kind == "refresh":
            self._read_current()
            return
        self._set(busy=kind, action_error="")
        try:
            if kind == "start":
                _, pid, desc = job
                res = self.client.start(pid, desc)
                if isinstance(res, dict) and res.get("started"):
                    self._set(running=res["started"], known=True)
            elif kind == "stop":
                self.client.stop()
                self._set(running=None, known=True)
            elif kind == "save":
                if not self.state.patch:
                    raise TogglError(0, "needs Second Brain update")
                running = self.client.patch_current(description=job[1])
                self._set(running=running, known=True)
        except TogglError as e:
            if e.status == 404 and kind == "save":
                e = TogglError(404, "no timer is running any more")
            self._set(busy="", action_error=str(e))
        else:
            self._set(busy="")
        self._read_current()                                        # Second Brain empties its cache after an action
        self._read_entries()
        self._next["entries"] = time.monotonic() + ENTRIES_EVERY
