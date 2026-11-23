"""The Toggl logic and client. Every network call goes to tests/fake_toggl.py, never to the real Second Brain."""
import threading
import time
from datetime import datetime, timezone

import pytest

from sdesk import toggl as t
from tests.fake_toggl import FakeToggl


def utc(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()


def local(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


# ------------------------------------------------------------------------------------------------------- formatting
def test_parse_time_naive_is_utc_and_z_works():
    assert t.parse_time("2026-10-06T12:30:55") == utc(2026, 10, 6, 12, 30, 55)
    assert t.parse_time("2026-10-06T12:30:55Z") == utc(2026, 10, 6, 12, 30, 55)
    assert t.parse_time("2026-10-06T14:30:55+02:00") == utc(2026, 10, 6, 12, 30, 55)
    assert t.parse_time("") is None and t.parse_time("nonsense") is None


def test_fmt_clock_and_hm():
    assert t.fmt_clock(0) == "0:00:00"
    assert t.fmt_clock(249) == "0:04:09"
    assert t.fmt_clock(5025) == "1:23:45"
    assert t.fmt_clock(61320) == "17:02:00"
    assert t.fmt_clock(-5) == "0:00:00"
    assert t.fmt_hm(16380) == "4:33"
    assert t.fmt_hm(59) == "0:00"


def test_elapsed_counts_from_start_or_falls_back_to_hours():
    start = utc(2026, 11, 23, 10)
    assert t.elapsed({"start": "2026-11-23T10:00:00+00:00"}, now=start + 90) == 90
    assert t.elapsed({"start": "", "hours": 1.5}, now=start) == 5400
    assert t.elapsed({"start": "2026-11-23T10:00:00Z"}, now=start - 10) == 0          # clock behind: never negative


def test_forgot_uses_too_long_or_the_threshold():
    start = "2026-11-23T00:00:00Z"
    now = utc(2026, 11, 23, 15)
    assert not t.forgot(None)
    assert not t.forgot({"start": start, "threshold": 16.0}, now)
    assert t.forgot({"start": start, "threshold": 16.0}, now + 3600 + 1)                # past 16 h by now
    assert t.forgot({"start": start, "too_long": True}, now)
    assert t.forgot({"start": start, "threshold": 10}, now)


def test_project_label():
    assert t.project_label({"name": "ellen", "mapped_client": "Ellen Blondeel"}) == "Ellen Blondeel"
    assert t.project_label({"name": "abcrent", "mapped_client": "ABC-Rent"}) == "ABC-Rent"
    assert t.project_label({"name": "noord website", "mapped_client": "Bakkerij Noord"}) == "Bakkerij Noord · noord website"
    assert t.project_label({"project": "x", "client": ""}) == "x"
    assert t.project_label({}) == "No project"


# --------------------------------------------------------------------------------------------------------- picking
ENTRIES = [
    {"occurred_at": "2026-11-20T09:00:00", "project_id": "1", "description": "Nieuwsbrief  november", "duration_sec": 60},
    {"occurred_at": "2026-11-22T09:00:00", "project_id": "1", "description": "nieuwsbrief november", "duration_sec": 120},
    {"occurred_at": "2026-11-21T09:00:00", "project_id": "2", "description": "Nieuwsbrief november", "duration_sec": 30},
    {"occurred_at": "2026-11-23T09:00:00", "project_id": "", "description": "no project", "duration_sec": 30},
    {"occurred_at": "2026-11-23T08:00:00", "project_id": "3", "description": "  ", "duration_sec": 30},
    {"occurred_at": "2026-11-19T09:00:00", "project_id": "3", "description": "Logo", "duration_sec": 30},
]


def test_recent_dedupes_on_project_and_description_newest_first():
    rows = t.recent(ENTRIES)
    assert [(r["project_id"], r["description"]) for r in rows] == [
        ("1", "nieuwsbrief november"), ("2", "Nieuwsbrief november"), ("3", "Logo")]
    assert rows[0]["seconds"] == 120                                    # the newest of the doubles
    assert len(t.recent(ENTRIES, limit=2)) == 2


def test_previous_skips_what_runs_now():
    assert t.previous(ENTRIES)["project_id"] == "1"
    running = {"project_id": "1", "description": "Nieuwsbrief November"}
    assert t.previous(ENTRIES, running)["project_id"] == "2"
    assert t.previous([], running) is None


def test_search_projects_filters_inactive_and_ranks_prefix_first():
    projects = [{"name": "noord website", "mapped_client": "Bakkerij Noord", "active": 1, "last_entry": "2026-11-01"},
                {"name": "bak campagne", "mapped_client": "Bakfietsen Oost", "active": 1, "last_entry": "2026-10-01"},
                {"name": "oud", "mapped_client": "Bakker Oud", "active": 0},
                {"name": "vos logo", "mapped_client": "Atelier Vos", "active": 1, "last_entry": "2026-11-20"}]
    assert [p["name"] for p in t.search_projects(projects, "bak")] == ["noord website", "bak campagne"]
    assert [p["name"] for p in t.search_projects(projects, "bak oost")] == ["bak campagne"]
    assert [p["name"] for p in t.search_projects(projects, "")] == ["vos logo", "noord website", "bak campagne"]
    assert len(t.search_projects(projects, "", limit=1)) == 1
    never = {"name": "nieuw", "mapped_client": "Aaa Nieuw", "active": 1}
    assert t.search_projects([never, *projects], "")[-1] is never           # never used: last, not first


# ------------------------------------------------------------------------------------------------- today, work hours
def test_today_seconds_counts_local_today_and_the_running_part():
    now = local(2026, 11, 23, 15, 0)
    morning = datetime.fromtimestamp(local(2026, 11, 23, 9, 0), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    yesterday = datetime.fromtimestamp(local(2026, 11, 22, 9, 0), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    entries = [{"occurred_at": morning, "duration_sec": 3600}, {"occurred_at": yesterday, "duration_sec": 7200}]
    running = {"start": datetime.fromtimestamp(local(2026, 11, 23, 14, 30), timezone.utc).isoformat()}
    assert t.today_seconds(entries, None, now) == 3600
    assert t.today_seconds(entries, running, now) == 3600 + 1800
    since_yesterday = {"start": datetime.fromtimestamp(local(2026, 11, 22, 23, 0), timezone.utc).isoformat()}
    assert t.today_seconds([], since_yesterday, now) == 15 * 3600              # only the part after midnight


def test_work_hours_parse_and_contains():
    wh = t.WorkHours.parse("mon-fri 09:00-17:30")
    assert wh.contains(datetime(2026, 11, 23, 9, 0))           # a Monday
    assert not wh.contains(datetime(2026, 11, 23, 8, 59))
    assert not wh.contains(datetime(2026, 11, 23, 17, 30))
    assert not wh.contains(datetime(2026, 11, 22, 10, 0))      # Sunday
    assert t.WorkHours.parse("off") is None and t.WorkHours.parse("") is None
    assert t.WorkHours.parse("sat 10-14").days == frozenset({5})
    assert t.WorkHours.parse("fri-mon 8:00-12:00").days == frozenset({4, 5, 6, 0})
    assert t.WorkHours.parse("mon,wed 9-17").days == frozenset({0, 2})
    assert t.WorkHours.parse("8:30-16:00").days == frozenset(range(5))
    for bad in ("funday 9-17", "mon 17-9", "mon 25:00-26:00"):
        with pytest.raises(ValueError):
            t.WorkHours.parse(bad)


def test_should_nag_only_in_work_hours_without_timer_and_when_known():
    wh = t.WorkHours.parse("mon-fri 09:00-18:00")
    mon10 = datetime(2026, 11, 23, 10, 0)
    assert t.should_nag(None, wh, mon10)
    assert not t.should_nag({"id": "1"}, wh, mon10)
    assert not t.should_nag(None, wh, datetime(2026, 11, 23, 20, 0))
    assert not t.should_nag(None, None, mon10)
    assert not t.should_nag(None, wh, mon10, online=False)


# ------------------------------------------------------------------------------------------------------------ client
@pytest.fixture
def fake():
    f = FakeToggl()
    f.url = f.serve()
    yield f
    f.shutdown()


def test_client_reads(fake):
    c = t.Client(fake.url)
    assert c.current() is None
    assert len(c.projects()) == 97                         # inactive ones are left out
    assert c.entries(limit=3) and len(c.entries(limit=3)) == 3
    assert c.supports_patch()
    fake.patch = False
    assert not c.supports_patch()


def test_client_start_stop_patch(fake):
    c = t.Client(fake.url)
    pid = fake.projects[0]["id"]
    res = c.start(pid, "Nieuwsbrief")
    assert res["started"]["description"] == "Nieuwsbrief" and res["stopped"] is None
    assert c.current()["project_id"] == pid
    res = c.start(fake.projects[1]["id"], "Logo")                   # a running timer is stopped first
    assert res["stopped"]["description"] == "Nieuwsbrief"
    assert c.patch_current(description="Logo v2")["description"] == "Logo v2"
    assert fake.calls[-1] == ("PATCH", "/api/ws/toggl/current", {"description": "Logo v2"})
    assert c.stop()["description"] == "Logo v2"
    assert c.stop() is None                                         # 404: nothing ran
    with pytest.raises(t.TogglError) as e:
        c.patch_current(description="x")
    assert e.value.status == 404


def test_client_errors(fake):
    c = t.Client(fake.url)
    with pytest.raises(t.TogglError) as e:
        c.start("", "x")
    assert e.value.status == 400 and str(e.value) == "kies een project"
    fake.fail = 502
    with pytest.raises(t.TogglError) as e:
        c.current()
    assert e.value.status == 502 and "fake error" in str(e.value)
    down = t.Client("http://127.0.0.1:9", timeout=1)
    with pytest.raises(t.TogglError) as e:
        down.current()
    assert e.value.status == 0 and "not reachable" in str(e.value)


# ----------------------------------------------------------------------------------------------------------- service
class Recorder:
    def __init__(self):
        self.states, self.cond = [], threading.Condition()

    def __call__(self, state):
        with self.cond:
            self.states.append(state)
            self.cond.notify_all()

    def wait(self, pred, timeout=5):
        end = time.monotonic() + timeout
        with self.cond:
            while not (self.states and pred(self.states[-1])):
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"timed out; last state: {self.states[-1] if self.states else None}")
                self.cond.wait(left)
            return self.states[-1]


def test_service_reads_then_acts_on_request(fake):
    rec = Recorder()
    svc = t.Service(t.Client(fake.url), rec, interval=60)
    svc.start()
    try:
        s = rec.wait(lambda s: s.known and s.projects and s.entries and s.patch)
        assert s.running is None and s.error == ""
        assert not any(m != "GET" for m, _p, _b in fake.calls)           # reading never writes
        svc.start_timer(fake.projects[2]["id"], "  Logo   varianten ")
        s = rec.wait(lambda s: s.running and not s.busy)
        assert s.running["description"] == "Logo varianten"
        svc.save_description("Logo varianten v2")
        s = rec.wait(lambda s: s.running and s.running["description"] == "Logo varianten v2" and not s.busy)
        svc.stop_timer()
        s = rec.wait(lambda s: s.running is None and not s.busy and s.entries[0]["description"] == "Logo varianten v2")
        writes = [(m, p) for m, p, _b in fake.calls if m != "GET"]
        assert writes == [("POST", "/api/ws/toggl/start"), ("PATCH", "/api/ws/toggl/current"), ("POST", "/api/ws/toggl/stop")]
    finally:
        svc.stop()


def test_service_without_patch_refuses_to_save(fake):
    fake.patch = False
    fake.set_running(fake.projects[0]["id"], "x")
    rec = Recorder()
    svc = t.Service(t.Client(fake.url), rec, interval=60)
    svc.start()
    try:
        rec.wait(lambda s: s.known and s.projects)
        svc.save_description("y")
        s = rec.wait(lambda s: s.action_error)
        assert s.action_error == "needs Second Brain update"
        assert not any(m == "PATCH" for m, _p, _b in fake.calls)
    finally:
        svc.stop()


def test_service_survives_second_brain_down(fake):
    fake.fail = 502
    rec = Recorder()
    svc = t.Service(t.Client(fake.url), rec, interval=60)
    svc.start()
    try:
        s = rec.wait(lambda s: s.error)
        assert not s.known and "fake error 502" in s.error
        svc.start_timer(fake.projects[0]["id"], "x")
        s = rec.wait(lambda s: s.action_error and not s.busy)
        assert "502" in s.action_error
        fake.fail = 0
        svc.refresh()
        s = rec.wait(lambda s: s.known and not s.error)
        assert s.running is None
    finally:
        svc.stop()
