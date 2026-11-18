import json
import sqlite3
import time

from sdesk import threads


def session(tmp_path, pid, status, entry="claude-desktop", name=None, sid=None):
    d = {"pid": pid, "sessionId": sid or f"s{pid}", "cwd": "/x", "entrypoint": entry, "status": status, "name": name or f"n{pid}",
         "procStart": "1"}
    (tmp_path / f"{pid}.json").write_text(json.dumps(d))


def alive_only(*pids):
    return lambda pid, start: pid in pids


def test_claude_sessions_give_their_own_status(tmp_path):
    session(tmp_path, 1, "busy")
    session(tmp_path, 2, "idle")
    session(tmp_path, 3, "waiting")
    got = threads.read_claude(str(tmp_path), alive=alive_only(1, 2, 3))
    assert {k: v[1] for k, v in got.items()} == {"s1": "busy", "s2": "idle", "s3": "waiting"}


def test_dead_t3_and_headless_sessions_are_left_out(tmp_path):
    session(tmp_path, 1, "busy")
    session(tmp_path, 2, "busy", entry="t3-code")        # T3's database knows this one
    session(tmp_path, 3, "busy", entry="sdk-ts")         # headless
    session(tmp_path, 4, "busy")                          # its process is gone
    (tmp_path / "junk.json").write_text("{not json")
    got = threads.read_claude(str(tmp_path), alive=alive_only(1, 2, 3))
    assert list(got) == ["s1"]


def test_the_real_process_check_uses_the_start_time():
    import os
    pid = os.getpid()
    start = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19]
    assert threads._alive(pid, start)
    assert not threads._alive(pid, str(int(start) + 1))      # same pid, another process: no
    assert not threads._alive(99999999, "1")


def make_t3(path, runs=(), requests=(), titles=None):
    con = sqlite3.connect(path)
    con.executescript("""
        create table orchestration_v2_projection_threads (thread_id, title, archived_at, deleted_at);
        create table orchestration_v2_projection_runs (thread_id, status, requested_at, completed_at);
        create table orchestration_v2_projection_runtime_requests (thread_id, status, created_at, resolved_at);""")
    for tid, title in (titles or {}).items():
        con.execute("insert into orchestration_v2_projection_threads values (?,?,null,null)", (tid, title))
    con.executemany("insert into orchestration_v2_projection_runs values (?,?,?,?)", runs)
    con.executemany("insert into orchestration_v2_projection_runtime_requests values (?,?,?,?)", requests)
    con.commit()
    con.close()


def test_t3_running_threads_work_and_those_with_an_open_question_wait(tmp_path):
    db = str(tmp_path / "t3.sqlite")
    now = threads._iso(time.time())
    old = threads._iso(time.time() - 3 * 86400)
    make_t3(db, titles={"a": "Work", "b": "Ask", "c": "Finished", "d": "Stale"},
            runs=[("a", "running", now, None), ("b", "running", now, None), ("c", "completed", now, now),
                  ("d", "running", old, None)],
            requests=[("b", "pending", now, None), ("c", "resolved", now, now)])
    assert threads.read_t3(db) == {"a": ("Work", "busy"), "b": ("Ask", "waiting")}


def test_t3_without_database_or_with_a_broken_one_is_just_empty(tmp_path):
    assert threads.read_t3(str(tmp_path / "none.sqlite")) == {}
    bad = tmp_path / "bad.sqlite"
    bad.write_text("this is not a database")
    assert threads.read_t3(str(bad)) == {}


def test_a_thread_that_stops_working_becomes_done_until_you_look():
    t = threads.Tracker()
    t.update("claude", {"a": ("A", "busy"), "b": ("B", "idle")})
    assert t.summary("claude") == {"waiting": [], "done": [], "busy": ["A"]}
    t.update("claude", {"a": ("A", "idle"), "b": ("B", "idle")})
    assert t.summary("claude")["done"] == ["A"]                      # B was never busy: not done
    t.acknowledge("claude")
    assert t.summary("claude")["done"] == []
    t.update("claude", {"a": ("A", "idle"), "b": ("B", "idle")})
    assert t.summary("claude")["done"] == []                         # and it does not come back by itself


def test_the_first_look_has_nothing_done_and_new_work_clears_done():
    t = threads.Tracker()
    t.update("claude", {"a": ("A", "idle")})
    assert t.summary("claude")["done"] == []
    t.update("claude", {"a": ("A", "busy")})
    t.update("claude", {"a": ("A", "waiting")})
    assert t.summary("claude")["waiting"] == ["A"]
    t.update("claude", {"a": ("A", "idle")})
    assert t.summary("claude")["done"] == ["A"]
    t.update("claude", {"a": ("A", "busy")})                        # it works again: not done any more
    assert t.summary("claude")["done"] == []


def test_a_closed_claude_session_is_not_done_but_a_t3_thread_that_left_the_list_is():
    t = threads.Tracker()
    t.update("claude", {"a": ("A", "busy")})
    t.update("claude", {})
    assert t.summary("claude")["done"] == []
    t.update("t3", {"x": ("X", "busy")}, gone_means_done=True)
    t.update("t3", {}, gone_means_done=True)
    assert t.summary("t3")["done"] == ["X"]
    t.update("t3", {}, gone_means_done=True)
    assert t.summary("t3")["done"] == ["X"]                          # stays until acknowledged
