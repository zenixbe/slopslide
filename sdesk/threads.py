"""The states of the AI threads of Claude Code (desktop app, terminal) and of T3 Code, for the taskbar circles. No GTK in here.

Only the states the programs keep themselves are read, nothing is guessed from the screen:

* Claude Code writes one file for each live session, ``~/.claude/sessions/<pid>.json``, with ``status`` ("busy", "idle" or
  "waiting") and the ``pid``. A file of a process that is gone is ignored. Sessions that T3 Code started
  (``entrypoint`` t3-code) are left out: T3's own database knows them, and counting twice would be wrong. So are the
  headless ones of the SDK (``entrypoint`` sdk-*).
* T3 Code keeps a SQLite database, ``~/.t3/userdata/statev2.sqlite``: a run with status "running" is a working thread, and
  a "running" thread that has an open runtime request (a question for the user) is waiting for you.

Three states are shown: ``busy`` (working), ``waiting`` (waits for you) and ``done`` (was busy or waiting, is idle now,
and you did not look at it yet). "Done" is not a state the programs keep, so :class:`Tracker` makes it from the change.
"""
import glob
import json
import os
import sqlite3
import time

CLAUDE_DIR = "~/.claude/sessions"
T3_DB = "~/.t3/userdata/statev2.sqlite"
ACTIVE = ("busy", "waiting")
STATES = ("waiting", "done", "busy")                  # the order the circles are shown in
RECENT_S = 24 * 3600                                  # a run or request older than this is a leftover, not a live thread


def _alive(pid, proc_start):
    """The process exists and is the same one that wrote the file (its start time matches): a reused pid does not count."""
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
    except (OSError, ValueError, IndexError):
        return False
    return str(proc_start) == fields[19] if proc_start is not None and len(fields) > 19 else True


def read_claude(directory=CLAUDE_DIR, alive=_alive):
    """{session id: (name, state)} of the live Claude Code sessions."""
    found = {}
    for path in glob.glob(os.path.join(os.path.expanduser(directory), "*.json")):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "sessionId" not in d:
            continue
        entry = str(d.get("entrypoint", ""))
        if entry == "t3-code" or entry.startswith("sdk"):
            continue
        if not alive(d.get("pid"), d.get("procStart")):
            continue
        state = d.get("status") if d.get("status") in ACTIVE else "idle"
        found[d["sessionId"]] = (str(d.get("name") or d.get("cwd") or "session"), state)
    return found


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def read_t3(db=T3_DB, now=None):
    """{thread id: (title, state)} of the T3 Code threads that work or wait. {} if the database cannot be read."""
    path = os.path.expanduser(db)
    if not os.path.exists(path):
        return {}
    since = _iso((time.time() if now is None else now) - RECENT_S)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.3)
    except sqlite3.Error:
        return {}
    try:
        running = {r[0] for r in con.execute(
            "SELECT DISTINCT thread_id FROM orchestration_v2_projection_runs "
            "WHERE status = 'running' AND completed_at IS NULL AND requested_at > ?", (since,))}
        asking = {r[0] for r in con.execute(
            "SELECT DISTINCT thread_id FROM orchestration_v2_projection_runtime_requests "
            "WHERE resolved_at IS NULL AND status NOT IN ('resolved', 'answered', 'cancelled', 'expired', 'completed') "
            "AND created_at > ?", (since,))}
        found = {}
        for tid, title in con.execute(
                "SELECT thread_id, title FROM orchestration_v2_projection_threads "
                "WHERE archived_at IS NULL AND deleted_at IS NULL"):
            if tid in running:
                found[tid] = (title or "thread", "waiting" if tid in asking else "busy")
        return found
    except sqlite3.Error:
        return {}
    finally:
        con.close()


class Tracker:
    """Remembers the last states of each source and which threads finished since you last looked."""

    def __init__(self):
        self._last = {}                               # source -> {id: (name, state)}
        self._done = {}                               # source -> {id: name}

    def update(self, source, current, gone_means_done=False):
        """``current`` is {id: (name, state)}. A thread that was busy or waiting and is idle now is done. One that is no
        longer listed is done too if ``gone_means_done`` (T3 lists only working threads), else it ended: forgotten."""
        last = self._last.get(source)
        done = self._done.setdefault(source, {})
        if last is not None:
            for tid, (name, state) in current.items():
                if state in ACTIVE:
                    done.pop(tid, None)
                elif last.get(tid, ("", "idle"))[1] in ACTIVE:
                    done[tid] = name
            for tid, (name, state) in last.items():
                if tid not in current and state in ACTIVE and gone_means_done:
                    done[tid] = name
        if not gone_means_done:
            for tid in [t for t in done if t not in current]:
                del done[tid]
        self._last[source] = dict(current)

    def acknowledge(self, source):
        """You looked: the done circle goes away."""
        self._done.get(source, {}).clear()

    def summary(self, source):
        """{"waiting": [names], "done": [names], "busy": [names]}"""
        cur = self._last.get(source, {})
        return {"waiting": [n for n, s in cur.values() if s == "waiting"],
                "done": list(self._done.get(source, {}).values()),
                "busy": [n for n, s in cur.values() if s == "busy"]}


def poll(tracker, claude_dir=CLAUDE_DIR, t3_db=T3_DB):
    """Read both sources, update the tracker, return {"claude": summary, "t3": summary}."""
    tracker.update("claude", read_claude(claude_dir))
    tracker.update("t3", read_t3(t3_db), gone_means_done=True)
    return {s: tracker.summary(s) for s in ("claude", "t3")}
