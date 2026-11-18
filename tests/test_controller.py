from types import SimpleNamespace

import pytest

from sdesk import controller as ctl
from sdesk.controller import Controller, PLACEHOLDER
from sdesk.layout import Rect


class FakeBackend:
    def __init__(self, frames, area=Rect(0, 0, 2000, 1000)):
        self.area = area
        self.frames = dict(frames)           # wid -> Rect (the "real" geometry)
        self.active_wid = None
        self.down = False
        self.px = 500
        self.placements = []
        self.hidden_set = set()

    def workarea(self): return self.area
    def list_windows(self): return list(self.frames)
    def frame_rect(self, wid): return self.frames.get(wid)
    def place(self, wid, rect):
        self.frames[wid] = rect              # WM obeys instantly
        self.placements.append((wid, rect))
    def unmaximize(self, wid): pass
    def is_hidden(self, wid): return wid in self.hidden_set
    def frame_top(self, wid): return getattr(self, 'titlebar', 0)
    def right_inset(self, pad): return getattr(self, 'sidebar', 0) and self.sidebar + pad
    def active(self): return self.active_wid
    def activate(self, wid): self.active_wid = wid
    def close(self, wid): self.frames.pop(wid, None)
    def minimize(self, wid): self.minimized = getattr(self, "minimized", []) + [wid]
    def pointer(self): return (self.px, 0, self.down)
    def info(self, wid): return SimpleNamespace(title=f"w{wid}", wm_class="app")


def settle(c, n=200):
    for _ in range(n):
        if not c.tick():
            return
    raise AssertionError("animation never settled")


@pytest.fixture
def setup(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ctl.time, "monotonic", lambda: clock[0])
    b = FakeBackend({1: Rect(900, 50, 500, 700), 2: Rect(100, 50, 500, 700), 3: Rect(500, 50, 500, 700)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows()
    settle(c)
    clock[0] += 5
    return b, c, clock


def test_initial_order_follows_screen_position(setup):
    _, c, _ = setup
    assert c.order == [2, 3, 1]


def test_windows_sit_side_by_side_with_margins(setup):
    b, c, _ = setup
    r = [b.frames[w] for w in c.order]
    assert all(x.y == 30 and x.h == 940 for x in r)           # vertical margin
    assert all(r[i + 1].x - r[i].right == 20 for i in range(2))   # gap


def test_new_window_opens_next_to_active_one(setup):
    b, c, _ = setup
    b.active_wid = 2
    b.frames[4] = Rect(0, 0, 500, 500)
    c.refresh_windows()
    assert c.order == [2, 4, 3, 1]


def test_closed_window_is_removed_and_layout_closes_gap(setup):
    b, c, _ = setup
    b.frames.pop(3)
    c.refresh_windows(); settle(c)
    assert c.order == [2, 1]
    assert b.frames[1].x - b.frames[2].right == 20


def centre(r, area):
    return r.x + r.w / 2, area.x + area.w / 2


def test_scrolling_moves_windows_and_stops_with_the_first_or_last_window_centred(monkeypatch):
    b = FakeBackend({i: Rect(i * 10, 0, 800, 500) for i in range(1, 6)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    x0 = b.frames[c.order[0]].x
    c.scroll_step_dir(+1); settle(c)
    assert b.frames[c.order[0]].x < x0
    c.scroll_by(10 ** 6); settle(c)
    got, want = centre(b.frames[c.order[-1]], b.area)            # all the way right: the LAST window sits in the middle
    assert abs(got - want) <= 1
    c.scroll_by(-10 ** 6); settle(c)
    got, want = centre(b.frames[c.order[0]], b.area)             # all the way left: the FIRST window sits in the middle
    assert abs(got - want) <= 1


def test_far_away_windows_are_parked_but_never_completely_off_screen(monkeypatch):
    # kwin moves a window that is completely outside the screen (17px down), which shows as a jump when it slides back in
    b = FakeBackend({i: Rect(i, 0, 900, 500) for i in range(1, 12)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    assert all(r.x < b.area.right and r.right > b.area.x for r in b.frames.values())
    assert max(r.x for r in b.frames.values()) >= b.area.right - 2 * ctl.KEEP_ON_SCREEN      # and they do sit at the edge
    c.scroll_by(10 ** 6); settle(c)
    assert all(r.x < b.area.right and r.right > b.area.x for r in b.frames.values())


def test_focus_reveals_window(monkeypatch):
    b = FakeBackend({i: Rect(i, 0, 900, 500) for i in range(1, 8)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    last = c.order[-1]
    c.focus(last); settle(c)
    r = b.frames[last]
    assert r.x >= 0 and r.right <= b.area.right


def test_taskbar_reorder_reorders_desktop(setup):
    b, c, _ = setup
    c.reorder(1, 0); settle(c)
    assert c.order == [1, 2, 3]
    xs = [b.frames[w].x for w in c.order]
    assert xs == sorted(xs)


def test_bar_preview_shows_indicator_then_commit(setup):
    b, c, _ = setup
    c.bar_preview(1, 0); settle(c)
    assert c.indicator is not None
    assert c.indicator == b.frames[1]          # indicator hugs the prospective slot
    assert c.order == [2, 3, 1]                # not committed yet
    c.clear_preview(); settle(c)
    assert c.indicator is None
    assert b.frames[1].x > b.frames[3].x


def test_dragging_a_window_opens_a_placeholder_and_reorders_on_drop(setup):
    b, c, clock = setup
    b.active_wid = 2
    c.poll()                                   # nothing pressed
    assert c.drag is None
    b.down = True
    f = b.frames[2]
    b.frames[2] = Rect(f.x + 1100, f.y + 5, f.w, f.h)   # user drags window 2 to the right
    c.poll()
    assert c.drag and c.drag["wid"] == 2
    assert c.indicator is not None
    assert c.drag["idx"] == 2                  # past 3 and 1
    settle(c)
    # siblings slid to make room: 3 and 1 are no longer at their old spots
    assert b.frames[3].x < f.x + 1100
    b.down = False
    c.poll(); settle(c)
    assert c.order == [3, 1, 2]
    assert c.indicator is None and c.drag is None
    xs = [b.frames[w].x for w in c.order]
    assert xs == sorted(xs) and b.frames[2].y == 30


def test_drag_dropped_in_place_keeps_order(setup):
    b, c, _ = setup
    b.active_wid = 3
    b.down = True
    f = b.frames[3]
    b.frames[3] = Rect(f.x + 40, f.y + 60, f.w, f.h)
    c.poll()
    b.down = False
    c.poll(); settle(c)
    assert c.order == [2, 3, 1]
    assert b.frames[3].y == 30                 # snapped back into its slot


def test_user_resize_updates_slot_and_pushes_neighbours(setup):
    b, c, _ = setup
    b.active_wid = 2
    b.down = True
    f = b.frames[2]
    b.frames[2] = Rect(f.x, f.y, f.w + 300, f.h - 100)   # drag bottom-right corner
    c.poll()
    assert c.resizing == 2
    settle(c)
    assert b.frames[3].x - (f.x + f.w + 300) == 20       # neighbour kept its gap
    b.down = False
    c.poll(); settle(c)
    assert c.resizing is None
    assert c.size[2] == [800, None]                      # the width is remembered, the height is not the user's to change
    assert b.frames[2].w == 800 and b.frames[2].h == b.frames[1].h   # the window is back at the common height


class PickyBackend(FakeBackend):
    """An app that does not simply obey: ``rules[wid](rect) -> rect`` says what it makes of a requested geometry."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rules = {}

    def place(self, wid, rect):
        rect = self.rules[wid](rect) if wid in self.rules else rect
        super().place(wid, rect)


def run_reconcile(c, clock, times=8):
    for _ in range(times):
        clock[0] += 1.0                         # let the "we just moved it" grace period pass
        c.reconcile()
        settle(c)


def picky_setup(monkeypatch, frames, **ctrl):
    clock = [100.0]
    monkeypatch.setattr(ctl.time, "monotonic", lambda: clock[0])
    b = PickyBackend(frames)
    c = Controller(b, gap=20, margin=30, **ctrl)
    c.refresh_windows(); settle(c)
    clock[0] += 5
    return b, c, clock


def test_an_app_that_insists_on_a_minimum_width_is_adopted_only_after_it_refused_repeatedly(monkeypatch):
    b, c, clock = picky_setup(monkeypatch, {1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700), 3: Rect(1200, 0, 500, 700)})
    b.rules[3] = lambda r: Rect(r.x, r.y, max(r.w, 700), r.h)           # min width 700
    b.frames[3] = Rect(b.frames[3].x, b.frames[3].y, 700, b.frames[3].h)
    clock[0] += 5
    c.reconcile()
    assert c.size[3][0] == 500 and c._size_tries[3] == 1                # first: we just say the size again
    run_reconcile(c, clock)
    assert c.size[3][0] == 700                                          # it never complied: now the layout follows it
    assert b.frames[3].x - b.frames[2].right == 20                      # neighbours still close up around it


def test_a_new_window_that_puts_its_own_height_back_is_made_to_fill_the_strip(monkeypatch):
    b, c, clock = picky_setup(monkeypatch, {1: Rect(0, 0, 500, 700)})
    state = {"restored": False}

    def rule(r):                                                         # toolkit restores its saved height once
        if not state["restored"]:
            state["restored"] = True
            return Rect(r.x, r.y, r.w, 300)
        return r

    b.rules[9] = rule
    b.frames[9] = Rect(0, 0, 500, 400)
    c.refresh_windows(); settle(c)
    assert b.frames[9].h == 300                                          # it had its way ... for now
    run_reconcile(c, clock, 3)
    full = b.area.h - c._top(9) - c.vmargin
    assert b.frames[9].h == full and c.size[9][1] is None               # ... and was put back to the full height
    assert b.frames[9].bottom == b.frames[1].bottom                      # bottoms line up again


def test_a_new_window_that_restores_a_wider_size_is_kept_to_the_cap(monkeypatch):
    b, c, clock = picky_setup(monkeypatch, {1: Rect(0, 0, 500, 700)})   # area 2000 -> cap 666
    state = {"restored": False}

    def rule(r):
        if not state["restored"]:
            state["restored"] = True
            return Rect(r.x, r.y, 1500, r.h)
        return r

    b.rules[9] = rule
    b.frames[9] = Rect(0, 0, 1500, 800)
    c.refresh_windows(); settle(c)
    run_reconcile(c, clock, 3)
    assert c.size[9][0] == 666 and b.frames[9].w == 666


def test_an_app_with_a_maximum_height_keeps_that_height_after_the_retries(monkeypatch):
    b, c, clock = picky_setup(monkeypatch, {1: Rect(0, 0, 500, 700)})
    b.rules[9] = lambda r: Rect(r.x, r.y, r.w, min(r.h, 400))           # can never be taller than 400
    b.frames[9] = Rect(0, 0, 500, 400)
    c.refresh_windows(); settle(c)
    run_reconcile(c, clock)
    assert c.size[9][1] == 400 and b.frames[9].h == 400                  # accepted, no endless fighting
    n = len(b.placements)
    run_reconcile(c, clock, 3)
    assert len(b.placements) == n                                        # and left alone from then on


def test_a_window_that_complies_is_never_re_sent(setup):
    b, c, clock = setup
    n = len(b.placements)
    clock[0] += 5
    c.reconcile()
    assert len(b.placements) == n and not c._size_tries


def test_reorder_scrolls_moved_window_into_view():
    b = FakeBackend({i: Rect(i, 0, 900, 500) for i in range(1, 8)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    c.scroll_by(10 ** 6); settle(c)                      # looking at the far right
    c.reorder(c.order[-1], 0); settle(c)                  # bar: move last window to front
    first = b.frames[c.order[0]]
    assert first.x >= 0 and first.right <= b.area.right


# ------------------------------------------------------------ add-a-window slot
def test_reserve_opens_a_slot_and_release_closes_it(setup):
    b, c, _ = setup
    c.reserve(1); settle(c)
    assert c.order == [2, 3, 1]                       # not a window yet
    assert c.indicator is not None and c.indicator.w == min(ctl.RESERVE_W, int(b.area.w / 3))
    assert b.frames[3].x - b.frames[2].right == 20 + c.indicator.w + 20      # slot sits between 2 and 3
    c.release(); settle(c)
    assert c.indicator is None
    assert b.frames[3].x - b.frames[2].right == 20


def test_new_window_lands_in_the_reserved_slot(setup):
    b, c, _ = setup
    b.active_wid = 1                                   # focus elsewhere: slot must win over "after active"
    c.reserve(1); settle(c)
    b.frames[9] = Rect(0, 0, 640, 480)
    c.refresh_windows(); settle(c)
    assert c.order == [2, 9, 3, 1]
    assert c.reserved is None and c.indicator is None


def test_unused_reservation_expires(setup):
    b, c, clock = setup
    c.reserve(2)
    c.reserve_timeout(15); settle(c)
    clock[0] += 10; c.poll()
    assert c.reserved is not None
    clock[0] += 10; c.poll(); settle(c)
    assert c.reserved is None and c.indicator is None


def test_reservation_without_timeout_waits_for_the_menu(setup):
    _, c, clock = setup
    c.reserve(0)
    clock[0] += 3600; c.poll()
    assert c.reserved is not None                     # menu still open


def test_gap_zones_sit_exactly_between_windows(setup):
    b, c, _ = setup
    zones = dict(c.gap_zones())
    assert sorted(zones) == [0, 1, 2, 3]
    f = {w: b.frames[w] for w in c.order}
    assert zones[1].x == f[2].right and zones[1].right == f[3].x       # the gap itself
    assert zones[2].x == f[3].right and zones[2].right == f[1].x
    assert zones[0].right == f[2].x and zones[0].w == 30                # margin-wide edge strips
    assert zones[3].x == f[1].right and zones[3].w == 30
    assert all(z.y == 0 and z.h == b.area.h for z in zones.values())


def test_no_gap_zones_while_busy(setup):
    _, c, _ = setup
    c.reserve(1)
    assert c.gap_zones() == []
    c.release()
    c.bar_preview(1, 0)
    assert c.gap_zones() == []


def test_zones_are_published_when_layout_settles(setup):
    _, c, _ = setup
    seen = []
    c.on_zones = seen.append
    c.reorder(1, 0); settle(c)
    assert seen[0] == [] and seen[-1] and len(seen[-1]) == 4         # hidden while moving, back when settled


# ------------------------------------------------- minimised windows / show desktop
def hide(b, c, *wids):
    b.hidden_set |= set(wids)
    c.refresh_windows(); settle(c)


def show(b, c, *wids):
    b.hidden_set -= set(wids)
    c.refresh_windows(); settle(c)


def test_minimised_window_keeps_its_button_and_place_but_gives_up_its_slot(setup):
    b, c, _ = setup
    hide(b, c, 3)
    assert c.order == [2, 3, 1]                               # still known, still in order
    assert [r["wid"] for r in c.snapshot()] == [2, 3, 1]      # still has a taskbar button
    assert {r["wid"]: r["hidden"] for r in c.snapshot()} == {2: False, 3: True, 1: False}
    assert b.frames[1].x - b.frames[2].right == 20            # strip closed up around it
    show(b, c, 3)
    assert c.order == [2, 3, 1]
    assert b.frames[3].x > b.frames[2].x and b.frames[1].x > b.frames[3].x   # back between 2 and 1


def test_show_desktop_freezes_everything_and_it_all_returns_as_it_was(setup):
    b, c, _ = setup
    c.scroll_by(40); settle(c)
    before = {w: b.frames[w] for w in c.order}
    scroll = c.scroll
    n = len(b.placements)
    hide(b, c, 1, 2, 3)                                       # every window minimised at once
    assert c.order == [2, 3, 1]
    assert len(b.placements) == n                             # nothing was moved
    assert c.scroll == scroll
    assert all(r["hidden"] for r in c.snapshot())             # buttons stay
    show(b, c, 1, 2, 3)
    assert {w: b.frames[w] for w in c.order} == before        # exactly where they were


def test_windows_already_minimised_at_startup_get_a_button_but_no_slot():
    b = FakeBackend({1: Rect(0, 50, 500, 700), 2: Rect(600, 50, 500, 700), 3: Rect(1200, 50, 500, 700)})
    b.hidden_set = {2}
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    assert c.order == [1, 2, 3] and c.hidden == {2}
    assert b.frames[3].x - b.frames[1].right == 20            # no empty hole where window 2 would be
    assert 2 not in c.placed                                  # a minimised window is never moved


def test_dropping_between_windows_with_a_minimised_one_in_the_middle(setup):
    b, c, _ = setup
    hide(b, c, 3)                                             # order 2, [3 hidden], 1
    c.reorder(1, 0, visible=True)                             # desktop drop: before the first visible window
    assert c.order == [1, 2, 3]
    c.reorder(1, 1, visible=True)                             # after the only other visible window
    assert c.order == [2, 1, 3]                               # lands right after the only other visible window


def test_taskbar_drop_counts_every_button_including_minimised(setup):
    b, c, _ = setup
    hide(b, c, 3)
    c.reorder(1, 1)                                           # between the 1st and 2nd button, minimised or not
    assert c.order == [2, 1, 3]


def test_add_slot_and_gap_zones_map_back_to_the_full_order(setup):
    b, c, _ = setup
    hide(b, c, 3)                                             # visible: 2, 1
    zones = dict(c.gap_zones())
    assert sorted(zones) == [0, 2, 3]                         # gap between 2 and 1 inserts before 1 (= index 2)
    c.reserve(2); settle(c)
    b.frames[9] = Rect(0, 0, 500, 500)
    c.refresh_windows(); settle(c)
    assert c.order == [2, 3, 9, 1]


def test_restoring_a_minimised_window_reveals_it(setup):
    b, c, _ = setup
    c.scroll_by(10 ** 6); settle(c)
    hide(b, c, 2)
    b.active_wid = 2
    show(b, c, 2)
    r = b.frames[2]
    assert r.x >= 0 and r.right <= b.area.right               # scrolled into view


# ----------------------------------------------------- titlebar-height top margin (kwin constraint)
def test_a_windows_top_is_at_least_its_titlebar_height():
    b = FakeBackend({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700)})
    b.titlebar = 41                                           # kwin pushes mostly-off-screen frames to y >= 41
    c = Controller(b, gap=20, margin=24)                      # vmargin defaults to margin (24)
    c.refresh_windows(); settle(c)
    for r in b.frames.values():
        assert r.y == 41                                      # lowered to the titlebar height, so kwin has nothing to fix
        assert r.bottom == b.area.bottom - 24                 # but the bottom margin is the normal one
    assert b.frames[2].x - b.frames[1].right == 20            # horizontal margins and gaps are unchanged


def test_windows_with_different_titlebars_all_get_the_same_top_and_bottom():
    class TB(FakeBackend):
        def frame_top(self, wid): return {1: 1, 2: 51, 3: 0}[wid]     # client-side, server-side, none
    b = TB({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700), 3: Rect(1200, 0, 500, 700)})
    c = Controller(b, gap=20, margin=24, vmargin=12)
    c.refresh_windows(); settle(c)
    assert [b.frames[w].y for w in (1, 2, 3)] == [51, 51, 51]         # the tallest titlebar sets the top for everybody
    assert len({r.bottom for r in b.frames.values()}) == 1            # bottoms line up: one height for all
    assert len({r.h for r in b.frames.values()}) == 1
    assert b.frames[1].bottom == b.area.bottom - 12


def test_a_small_vmargin_is_honoured_when_no_titlebar_needs_more():
    b = FakeBackend({1: Rect(0, 0, 500, 700)})
    c = Controller(b, gap=20, margin=24, vmargin=8)
    c.refresh_windows(); settle(c)
    assert b.frames[1].y == 8 and b.frames[1].bottom == b.area.bottom - 8


def test_a_bigger_user_margin_wins_over_the_titlebar_height():
    b = FakeBackend({1: Rect(0, 0, 500, 700)})
    b.titlebar = 41
    c = Controller(b, gap=20, margin=60)
    c.refresh_windows(); settle(c)
    assert c.vmargin == 60 and b.frames[1].y == 60


def test_without_a_titlebar_the_margin_is_used_as_is(setup):
    b, c, _ = setup
    assert c.vmargin == 30 and all(r.y == 30 for r in b.frames.values())


def test_a_minimised_window_with_a_tall_titlebar_does_not_count_until_it_is_back():
    class TB(FakeBackend):
        def frame_top(self, wid): return {1: 41, 2: 51}[wid]      # 2 has the tall one
    b = TB({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700)})
    b.hidden_set = {2}
    c = Controller(b, gap=20, margin=24, vmargin=12)
    c.refresh_windows(); settle(c)
    assert b.frames[1].y == 41                                # only visible windows' titlebars count
    assert 2 not in c.placed                                  # the minimised one is not placed at all
    b.hidden_set = set()
    c.refresh_windows(); settle(c)
    assert b.frames[1].y == 51 and b.frames[2].y == 51        # restored: the common top follows it, heights stay equal
    assert b.frames[1].h == b.frames[2].h
    assert b.frames[1].bottom == b.frames[2].bottom


# ------------------------------------------------------------ new windows are capped to a third of the screen
def test_a_window_that_opens_later_is_capped_to_a_third_of_the_screen(setup):
    b, c, _ = setup                                           # area is 2000 wide -> cap 666
    b.frames[9] = Rect(0, 0, 1500, 800)
    c.refresh_windows(); settle(c)
    assert c.size[9][0] == 666 and b.frames[9].w == 666


def test_smaller_new_windows_keep_their_size(setup):
    b, c, _ = setup
    b.frames[9] = Rect(0, 0, 400, 800)
    c.refresh_windows(); settle(c)
    assert c.size[9][0] == 400


def test_windows_open_at_startup_are_not_capped():
    b = FakeBackend({1: Rect(0, 0, 1500, 800), 2: Rect(0, 0, 1200, 800)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    assert sorted(c.size[w][0] for w in c.order) == [1200, 1500]


def test_the_cap_is_configurable_and_resizing_by_hand_can_exceed_it(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ctl.time, "monotonic", lambda: clock[0])
    b = FakeBackend({1: Rect(0, 0, 500, 800)})
    c = Controller(b, gap=20, margin=30, new_max_frac=0.25)
    c.refresh_windows(); settle(c)
    b.frames[2] = Rect(0, 0, 1500, 800)
    c.refresh_windows(); settle(c)
    assert c.size[2][0] == 500                                # 25% of 2000
    clock[0] += 5                                             # let the "we just moved it" grace period pass
    b.active_wid = 2; b.down = True
    f = b.frames[2]; b.frames[2] = Rect(f.x, f.y, 1400, f.h)  # the user drags it wider
    c.poll(); b.down = False; c.poll(); settle(c)
    assert c.size[2][0] == 1400                               # the cap only sets the initial size


def test_the_add_window_slot_respects_the_cap():
    b = FakeBackend({1: Rect(0, 0, 500, 800)})
    c = Controller(b, gap=20, margin=30, new_max_frac=0.2)    # 400 px
    c.refresh_windows(); settle(c)
    c.reserve(1)
    assert c.reserved["w"] == 400


# ------------------------------------------------------------ centring (taskbar click / manual scroll)
def strip3():
    b = FakeBackend({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700), 3: Rect(1200, 0, 500, 700)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    return b, c


def test_clicking_the_first_window_centres_it_although_nothing_is_to_its_left():
    b, c = strip3()
    c.focus(c.order[0]); settle(c)
    assert abs(centre(b.frames[c.order[0]], b.area)[0] - centre(b.frames[c.order[0]], b.area)[1]) <= 1
    assert b.active_wid == c.order[0]


def test_clicking_the_last_window_centres_it_although_nothing_is_to_its_right():
    b, c = strip3()
    c.focus(c.order[-1]); settle(c)
    got, want = centre(b.frames[c.order[-1]], b.area)
    assert abs(got - want) <= 1


def test_clicking_a_middle_window_centres_it():
    b, c = strip3()
    c.focus(c.order[0]); settle(c)
    c.focus(c.order[1]); settle(c)
    got, want = centre(b.frames[c.order[1]], b.area)
    assert abs(got - want) <= 1


def test_a_narrow_strip_stays_centred_until_the_user_scrolls_or_clicks():
    b, c = strip3()
    xs = [b.frames[w].x for w in c.order]
    left, right = xs[0], b.area.w - b.frames[c.order[-1]].right
    assert abs(left - right) <= 1                                 # the whole strip is centred, as before


def test_wheel_scrolling_can_also_bring_the_first_and_last_windows_to_the_middle():
    b, c = strip3()
    for _ in range(40):
        c.scroll_step_dir(-1)
    settle(c)
    assert abs(centre(b.frames[c.order[0]], b.area)[0] - centre(b.frames[c.order[0]], b.area)[1]) <= 1
    for _ in range(80):
        c.scroll_step_dir(+1)
    settle(c)
    got, want = centre(b.frames[c.order[-1]], b.area)
    assert abs(got - want) <= 1


def test_clicking_a_minimised_window_centres_it_once_it_is_back():
    b, c = strip3()
    hide(b, c, 1)
    c.focus(1)                                                    # kwin restores it; meanwhile it is not on the strip
    assert c._center_pending == 1
    show(b, c, 1)
    got, want = centre(b.frames[1], b.area)
    assert abs(got - want) <= 1 and c._center_pending is None


def test_windows_that_open_on_their_own_are_only_revealed_not_centred():
    b = FakeBackend({i: Rect(i, 0, 800, 500) for i in range(1, 6)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    b.frames[9] = Rect(0, 0, 500, 500)
    b.active_wid = 9
    c.refresh_windows(); settle(c)
    r = b.frames[9]
    assert r.x >= 0 and r.right <= b.area.right                   # visible
    assert abs(centre(r, b.area)[0] - centre(r, b.area)[1]) > 5   # but not forced into the middle


# --------------------------------------------- the taskbar highlights the window nearest the centre
def active_flags(c):
    return {r["wid"]: r["active"] for r in c.snapshot()}


def test_the_highlight_follows_the_window_nearest_the_screen_centre():
    b, c = strip3()
    first, mid, last = c.order
    assert active_flags(c) == {first: False, mid: True, last: False}     # whole strip centred: the middle window is nearest
    c.focus(first); settle(c)
    assert active_flags(c)[first] and not active_flags(c)[mid]
    c.focus(last); settle(c)
    assert active_flags(c)[last] and not active_flags(c)[first]


def test_scrolling_by_hand_moves_the_highlight_when_another_window_becomes_nearer():
    b = FakeBackend({i: Rect(i, 0, 800, 500) for i in range(1, 6)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    seen = []
    for _ in range(30):
        c.scroll_step_dir(+1); settle(c)
        flags = active_flags(c)
        seen.append([w for w, on in flags.items() if on][0])
    assert len(set(seen)) >= 4                                         # it travelled along the strip ...
    assert seen == sorted(seen, key=c.order.index)                     # ... in order, never jumping back
    assert seen[-1] == c.order[-1]                                     # ends on the last window


def test_exactly_one_window_is_highlighted_and_the_focused_one_wins_a_tie():
    b = FakeBackend({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)                                     # two windows, equally far from the centre
    b.active_wid = 2
    c.active_changed()
    assert sum(active_flags(c).values()) == 1 and active_flags(c)[2]
    b.active_wid = 1
    c.active_changed()
    assert active_flags(c)[1]


def test_a_minimised_window_is_never_the_highlighted_one(setup):
    b, c, _ = setup
    centred = [w for w, on in active_flags(c).items() if on][0]
    hide(b, c, centred)
    assert not active_flags(c)[centred]
    assert sum(active_flags(c).values()) == 1


def test_the_taskbar_is_told_when_the_highlight_moves():
    b, c = strip3()
    calls = []
    c.on_change = lambda: calls.append(1)
    c.focus(c.order[0]); settle(c)
    assert calls                                                       # centring another window moved the highlight
    calls.clear()
    c.scroll_by(1); settle(c)                                          # 1px: still the same window
    assert not calls


# --------------------------------------------------------------- a floating sidebar on the right edge
def sidebar_strip(sidebar=400, **kw):
    b = FakeBackend({i: Rect(i * 10, 0, 500, 700) for i in range(1, 7)})          # area 2000 wide
    b.sidebar = sidebar
    c = Controller(b, gap=20, margin=30, edge_pad=16, **kw)
    c.refresh_windows(); settle(c)
    return b, c


def test_windows_end_before_the_sidebar_minus_the_padding():
    b, c = sidebar_strip()
    assert c.right_inset == 0                                                     # not looked at yet
    c.update_inset(); settle(c)
    assert c.right_inset == 416 and c.area.w == 2000 - 416
    c.reveal(c.order[-1]); settle(c)
    last = b.frames[c.order[-1]]
    assert last.right == c.area.right - c.margin                                  # the outermost window aligns to the edge
    assert last.right <= 2000 - 416                                               # i.e. before the sidebar, never under it


def test_centring_uses_the_middle_of_the_usable_area_not_of_the_screen():
    b, c = sidebar_strip()
    c.update_inset(); settle(c)
    c.focus(c.order[2]); settle(c)
    r = b.frames[c.order[2]]
    assert abs((r.x + r.w / 2) - (c.area.x + c.area.w / 2)) <= 1
    assert abs((r.x + r.w / 2) - 1000) > 100                                      # and so not the middle of the screen


def test_parked_windows_hide_under_the_sidebar_instead_of_showing_in_the_padding():
    b, c = sidebar_strip()
    c.update_inset(); settle(c)
    c.scroll_by(-10 ** 6); settle(c)
    parked_right = [r for r in b.frames.values() if r.x >= c.area.right]
    assert parked_right and all(r.x == 2000 - ctl.KEEP_ON_SCREEN for r in parked_right)     # at the screen edge, under the widget


def test_the_sidebar_appearing_and_disappearing_re_lays_the_strip_out():
    b, c = sidebar_strip(sidebar=0)
    c.update_inset(); settle(c)
    assert c.right_inset == 0 and c.area.w == 2000
    seen = []
    c.on_area = seen.append
    b.sidebar = 300
    c.update_inset(); settle(c)
    assert c.right_inset == 316 and seen and seen[-1].w == 2000 - 316
    b.sidebar = 0
    c.update_inset(); settle(c)
    assert c.right_inset == 0 and seen[-1].w == 2000


def test_right_edge_off_ignores_the_sidebar_and_a_number_is_used_as_given():
    b, c = sidebar_strip(right_edge="off")
    c.update_inset()
    assert c.right_inset == 0
    b, c = sidebar_strip(right_edge=250)
    c.update_inset()
    assert c.right_inset == 250                                                   # exact, no padding added


def test_an_unchanged_sidebar_does_not_cause_needless_relayouts():
    b, c = sidebar_strip()
    c.update_inset(); settle(c)
    n = len(b.placements)
    c.update_inset(); c.update_inset(); settle(c)
    assert len(b.placements) == n


# ----------------------------------------- room for the fade strip, and telling the app where the sidebar is
class SideBackend(FakeBackend):
    def sidebar_rect(self):
        return Rect(1600, 0, 400, 1000) if getattr(self, "sidebar", 0) else None


def side_strip(extra=0, sidebar=400):
    b = SideBackend({i: Rect(i * 10, 0, 500, 700) for i in range(1, 5)})
    b.sidebar = sidebar
    c = Controller(b, gap=20, margin=30, edge_pad=16)
    c.edge_extra = extra
    c.refresh_windows(); settle(c)
    return b, c


def test_the_soft_edge_gets_its_own_room_next_to_the_sidebar():
    b, c = side_strip(extra=64)
    c.update_inset(); settle(c)
    assert c.right_inset == 400 + 16 + 64                         # sidebar + padding + soft edge
    c.reveal(c.order[-1]); settle(c)
    assert b.frames[c.order[-1]].right <= 2000 - 480              # the aligned window stops before the soft edge: never faded


def test_without_a_sidebar_there_is_no_extra_room_for_a_fade():
    b, c = side_strip(extra=64, sidebar=0)
    c.update_inset()
    assert c.right_inset == 0


def test_the_app_is_told_where_the_sidebar_is_and_when_it_goes_or_moves():
    b, c = side_strip()
    seen = []
    c.on_sidebar = seen.append
    c.update_inset(); c.update_inset()
    assert seen == [Rect(1600, 0, 400, 1000)]                     # once, not on every look
    b.sidebar = 0
    c.update_inset()
    assert seen[-1] is None
    b.sidebar = 400
    c.update_inset()
    assert seen[-1] == Rect(1600, 0, 400, 1000) and len(seen) == 3


def test_changing_the_fade_width_re_lays_the_strip_out():
    b, c = side_strip(extra=0)
    c.update_inset(); settle(c)
    assert c.right_inset == 416
    c.set_edge_extra(64); settle(c)
    assert c.right_inset == 480 and c.area.w == 2000 - 480
    c.set_edge_extra(0); settle(c)
    assert c.right_inset == 416                                   # e.g. the compositor was switched off


# ------------------------------------------------ dragging a gap resizes the window left of it
def test_dragging_a_gap_resizes_the_window_left_of_it_by_its_right_edge():
    b = FakeBackend({i: Rect(i * 10, 0, 300, 700) for i in range(1, 4)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    a, bb, cc = c.order
    assert c.resize_target(1) == (a, "right")
    assert c.resize_target(2) == (bb, "right")
    assert c.resize_target(3) == (cc, "right")                    # the margin after the last window
    assert c.resize_target(0) == (a, "left")                      # the margin before the first one


def test_a_minimised_window_does_not_take_the_gap_resize():
    b = FakeBackend({i: Rect(i * 10, 0, 300, 700) for i in range(1, 4)})
    c = Controller(b, gap=20, margin=30)
    c.refresh_windows(); settle(c)
    a, bb, cc = c.order
    c.hidden.add(bb)
    assert c.resize_target(2) == (a, "right")                     # the gap before cc is next to a, bb is out of the strip
    c.hidden.update(c.order)
    assert c.resize_target(1) is None


def test_new_windows_open_at_the_common_height_whatever_height_they_ask_for():
    b = FakeBackend({1: Rect(0, 0, 500, 300), 2: Rect(600, 0, 500, 900), 3: Rect(1200, 0, 500, 100)})
    c = Controller(b, gap=20, margin=24, vmargin=12)
    c.refresh_windows(); settle(c)
    assert len({(r.y, r.h) for r in b.frames.values()}) == 1
    assert all(sz[1] is None for sz in c.size.values())


# ------------------------------------------------ clicking the active taskbar item minimises it
def test_clicking_the_active_item_minimises_it_and_any_other_item_focuses_it(setup):
    b, c, _ = setup
    first, second = c.order[0], c.order[1]
    c.focus(first); settle(c)
    assert b.active_wid == first and c._centered == first
    c.click(first)
    assert getattr(b, "minimized", []) == [first]                 # active and highlighted: minimised, not focused again
    c.click(second)
    assert b.minimized == [first] and b.active_wid == second      # another item: focused (and centred) as before


def test_clicking_a_highlighted_but_unfocused_item_focuses_it_instead_of_minimising(setup):
    b, c, _ = setup
    first, second = c.order[0], c.order[1]
    c.focus(first); settle(c)
    b.active_wid = second                                         # focus went elsewhere, the strip still shows `first` centred
    assert c._centered == first
    c.click(first)
    assert getattr(b, "minimized", []) == [] and b.active_wid == first


# ------------------------------------------------ how long a window has been minimised
def test_the_time_a_window_has_been_minimised_is_tracked_until_it_is_back():
    now = [1000.0]
    seen = []
    b = FakeBackend({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700)})
    c = Controller(b, gap=20, margin=24)
    c.clock = lambda: now[0]
    c.on_minimized = seen.append
    c.refresh_windows(); settle(c)
    assert c.hidden_since == {}
    b.hidden_set = {2}
    c.refresh_windows()
    now[0] += 1800
    row = {r["wid"]: r for r in c.snapshot()}
    assert row[2]["hidden_for"] == 1800 and row[1]["hidden_for"] == 0
    assert seen[-1] == {2: 1000.0}
    b.hidden_set = set()
    c.refresh_windows()
    assert c.hidden_since == {} and seen[-1] == {}


def test_a_restart_keeps_the_older_minimised_time():
    now = [5000.0]
    b = FakeBackend({1: Rect(0, 0, 500, 700), 2: Rect(600, 0, 500, 700)})
    b.hidden_set = {1}
    c = Controller(b, gap=20, margin=24)
    c.clock = lambda: now[0]
    c.refresh_windows()
    c.adopt_minimized({1: 100.0, 2: 50.0, 9: 1.0})        # 2 is not minimised any more, 9 is gone
    assert c.hidden_since == {1: 100.0}
    c.adopt_minimized({1: 9000.0})                          # a later time never replaces an earlier one
    assert c.hidden_since == {1: 100.0}


def test_minimised_times_survive_through_the_state_file(tmp_path, monkeypatch):
    from sdesk import safety
    monkeypatch.setattr(safety, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(safety, "MINIMIZED", str(tmp_path / "minimized.json"))
    safety.save_minimized({12: 123.5, 7: 9.0})
    assert safety.load_minimized() == {12: 123.5, 7: 9.0}
    (tmp_path / "minimized.json").write_text("garbage")
    assert safety.load_minimized() == {}
