"""Strip model + behaviour: ordering, scrolling, drag-to-reorder, resize, animation.

Toolkit-agnostic. Talks to the outside world through a *backend* object:

    workarea() -> Rect
    list_windows() -> [wid]            manageable windows on the current desktop
    frame_rect(wid) -> Rect | None     outer frame geometry, screen coordinates
    place(wid, Rect)                   move/resize the outer frame
    unmaximize(wid)
    active() -> wid | None
    activate(wid)
    close(wid)
    pointer() -> (x, y, button_down)
    info(wid) -> WinInfo-like (title, wm_class)
"""
import time

from . import layout
from .layout import Rect

PLACEHOLDER = "placeholder"
RESERVED = "reserved"           # empty slot held open for an app that is being launched
RESERVE_W = 1100                # width of that slot (typical new window)
LAUNCH_TIMEOUT = 15             # s to wait for a launched app's window before giving up
MIN_ZONE = 6
MIN_W = 240
KEEP_ON_SCREEN = 1  # px of a far-away window left on screen. A window parked completely outside the screen gets
                    # nudged 17px down by kwin, and snaps back (a visible jump) when it slides in again
EASE = 0.28         # fraction of the remaining distance covered per animation tick
DRAG_START_PX = 10
EDGE_SCROLL_ZONE = 90
EDGE_SCROLL_SPEED = 38
SETTLE_S = 0.25     # ignore geometry for this long after we moved a window
SIZE_RETRIES = 4    # how often we say "this size" again to a window that put its own size back, before giving in


class Controller:
    def __init__(self, backend, gap=24, margin=24, scroll_step=480, new_max_frac=1 / 3, vmargin=None,
                 right_edge="auto", edge_pad=16):
        self.b = backend
        self.gap = gap
        self.margin = margin
        self.vmargin = margin if vmargin is None else vmargin   # space above and below the windows
        self.tmin = {}             # wid -> least distance its frame may have from the top, see _update_tops
        self.scroll_step = scroll_step
        self.right_edge = right_edge        # "auto" (find a floating sidebar), "off", or a fixed number of px
        self.edge_pad = edge_pad            # space kept between the strip and such a sidebar
        self.right_inset = 0                # how much of the right side the sidebar takes
        self.full_right = 0                 # right edge of the whole work area (windows are parked up to there)
        self.new_max_frac = new_max_frac   # a window that opens later is at most this fraction of the screen wide

        self.order = []            # committed window order, left to right
        self.size = {}             # wid -> [w, h or None]
        self.original = {}         # wid -> Rect at the time we took it over
        self.cur = {}              # wid -> rendered x (float, animated)
        self.tgt = {}              # wid -> target Rect
        self.placed = {}           # wid -> last Rect we sent to the WM
        self.placed_at = {}        # wid -> monotonic time of that
        self.scroll = 0.0
        self.total = 0

        self.drag = None           # {"wid", "idx", "source": "window"|"bar"}
        self.resizing = None
        self.indicator = None      # Rect of the prospective drop slot, or None
        self.reserved = None       # {"idx", "w", "until"}: slot for the next new window (idx into self.order)
        self.hidden = set()        # minimised / "show desktop": keep their place and taskbar button, but no strip slot
        self.clock = time.time
        self.hidden_since = {}     # wid -> wall-clock time it was minimised (the taskbar nags about long-forgotten ones)
        self.on_minimized = lambda stamps: None   # told whenever those times change, so they can outlive a restart
        self._prepared = set()     # windows already un-maximised and sized
        self.free_rects = {}       # wid -> Rect of the strip layout, before off-screen parking
        self.area = Rect(0, 0, 1920, 1080)

        # hooks set by the app
        self.request_tick = lambda: None
        self.on_change = lambda: None       # order / titles / visibility changed
        self.on_indicator = lambda rect: None
        self.on_zones = lambda zones: None  # clickable gaps: [(insert_idx, Rect)] or []
        self.on_originals = lambda rects: None   # {wid: Rect} before we took the windows over (crash safety)
        self.on_area = lambda area: None    # the usable area changed (its middle is what things are centred on)
        self.on_sidebar = lambda rect: None  # the sidebar's rectangle (or None) changed
        self.sidebar = None                 # Rect of the floating sidebar on the right, if any
        self.edge_extra = 0                 # extra room kept beside the sidebar (the fade zone), asked for by the app
        self._first = True
        self._frozen = False        # no scroll clamping while the user resizes
        self._user_scrolled = False  # once the user scrolls/centres, the strip may go past its ends (see layout.scroll_bounds)
        self._bounds = (None, None)  # strip x of the first / last window's centre
        self._center_pending = None  # a clicked window that was minimised: centre it as soon as it is back
        self._centered = None        # the window nearest the middle of the screen: the one the taskbar highlights
        self._size_tries = {}       # wid -> times we re-sent its size because it did not take it
        self._last_vis = ()
        self._settled = False       # the strip is at rest and its gap zones are shown

    # ------------------------------------------------------------- queries
    def _work_area(self):
        """The work area minus a floating sidebar on the right: what the strip lays itself out in."""
        a = self.b.workarea()
        self.full_right = a.right
        return Rect(a.x, a.y, max(200, a.w - self.right_inset), a.h)

    def set_edge_extra(self, px):
        """Keep ``px`` more room beside the sidebar (where the fade strip is drawn)."""
        if px != self.edge_extra:
            self.edge_extra = px
            self.update_inset()

    def update_inset(self):
        """Look for the sidebar again (it may have moved, resized, appeared or gone) and re-lay-out if it changed."""
        rect = None
        if self.right_edge == "off":
            new = 0
        elif self.right_edge == "auto":
            new = self.b.right_inset(self.edge_pad)
            rect = getattr(self.b, "sidebar_rect", lambda: None)() if new else None
        else:
            new = int(self.right_edge)
        if new:
            new += self.edge_extra
        if rect != self.sidebar:
            self.sidebar = rect
            self.on_sidebar(rect)
        if new != self.right_inset:
            self.right_inset = new
            self.layout()
            self.on_area(self.area)
            self.on_change()

    def _new_window_cap(self):
        return int(self.area.w * self.new_max_frac)

    def _update_tops(self):
        """kwin keeps a mostly-off-screen window's frame at least one of *its own* titlebar heights from the top edge, and
        moves it there if it is closer. Our far-away windows are mostly off-screen, so a window placed higher than that
        sits lower while parked and visibly jumps up when it slides in. So the top is ``max(vmargin, the tallest titlebar of
        any window)`` (kwin then never has anything to correct), the same for every window: all windows share one top and
        one bottom edge, i.e. one height. Returns True if anything changed."""
        changed = False
        for w in self._vis():
            t = self.b.frame_top(w)
            if self.tmin.get(w) != t:
                self.tmin[w] = t
                changed = True
        return changed

    def _top(self, wid=None):
        """Distance of every window's frame from the top of the work area (the same for all, see _update_tops)."""
        return max([self.vmargin] + [self.tmin.get(w, 0) for w in self._vis()])

    def _fill_h(self, wid):
        """Height of a window that fills the strip vertically."""
        return self.area.h - self._top(wid) - self.vmargin

    def _vis(self):
        """Windows that take part in the strip layout (everything not minimised)."""
        return [w for w in self.order if w not in self.hidden]

    def _vidx(self, idx):
        """Index into the visible list that corresponds to index ``idx`` of the full order."""
        return sum(1 for w in self.order[:idx] if w not in self.hidden)

    def view_order(self):
        """Order used for layout: shows the tentative result of a drag."""
        d = self.drag
        if not d:
            order = self._vis()
            if self.reserved:
                order.insert(self._vidx(self.reserved["idx"]), RESERVED)
            return order
        others = [w for w in self.order if w != d["wid"]]
        if d["source"] == "window":                       # idx counts visible windows only
            others = [w for w in others if w not in self.hidden]
            others.insert(d["idx"], PLACEHOLDER)
            return others
        others.insert(d["idx"], d["wid"])                  # taskbar drag: idx counts every button
        return [w for w in others if w not in self.hidden]

    def _items(self, order):
        out = []
        top = self._top()
        for ident in order:
            if ident == RESERVED:
                out.append((ident, self.reserved["w"], None))
                continue
            src = self.drag["wid"] if ident == PLACEHOLDER else ident
            w, h = self.size[src]
            out.append((ident, w, h, top))
        return out

    def _nearest_centre(self):
        """The visible window whose middle is closest to the middle of the screen (the focused one wins a tie)."""
        cands = [w for w in self._vis() if w in self.free_rects]
        if not cands:
            return None
        mid = self.area.x + self.area.w / 2
        focus = self.b.active()
        return min(cands, key=lambda w: (abs(self.free_rects[w].cx - mid), w != focus))

    def snapshot(self):
        """Rows for the taskbar. "active" marks the window nearest the screen centre, so the highlight follows scrolling
        and centring; without any visible window it falls back to the focused one."""
        active = self._centered if self._centered in self.order else self.b.active()
        rows = []
        for wid in self.order:
            info = self.b.info(wid)
            t = self.tgt.get(wid)
            rows.append({
                "wid": wid,
                "title": info.title,
                "wm_class": info.wm_class,
                "active": wid == active,
                "hidden": wid in self.hidden,
                "hidden_for": (self.clock() - self.hidden_since[wid]) if wid in self.hidden_since else 0.0,
                "visible": bool(t and t.intersects(self.area)),
            })
        return rows

    # ------------------------------------------------------- window set
    def refresh_windows(self):
        """Reconcile the strip with the WM's list of manageable windows."""
        self.area = self._work_area()
        wins = self.b.list_windows()
        gone = [w for w in self.order if w not in wins]
        new = [w for w in wins if w not in self.order]
        for w in gone:
            self._forget(w)

        initial = self._first and bool(new)       # the windows that were already open when we started keep their size
        if initial:
            frames = {w: self.b.frame_rect(w) for w in new}
            new.sort(key=lambda w: frames[w].x if frames[w] else 0)
            self._first = False

        anchor = self.b.active()
        for w in new:
            frame = self.b.frame_rect(w)
            if frame is None:
                continue
            self.original[w] = frame
            width = max(MIN_W, min(frame.w, self.area.w - 2 * self.margin))
            if not initial:
                width = max(MIN_W, min(width, self._new_window_cap()))
            self.size[w] = [width, None]
            if self.reserved:
                self.order.insert(min(self.reserved["idx"], len(self.order)), w)
                self.reserved = None
            elif anchor in self.order:
                self.order.insert(self.order.index(anchor) + 1, w)
            else:
                self.order.append(w)
            anchor = w

        hidden = {w for w in self.order if self.b.is_hidden(w)}
        changed = bool(gone or new) or hidden != self.hidden
        self.hidden = hidden
        self._track_minimized()
        changed = self._update_tops() or changed
        for w in self.order:                      # first time a window is actually on screen: take it out of maximised
            if w not in hidden and w not in self._prepared:
                self._prepared.add(w)
                self.b.unmaximize(w)
                changed = True

        if changed:
            self.on_area(self.area)
            if gone or new:
                self.on_originals(dict(self.original))
            self.layout()
            self._show_focused()
            self.on_change()

    def _track_minimized(self):
        """Note when each window went into the taskbar, and forget it once it is back."""
        now, before = self.clock(), dict(self.hidden_since)
        for w in self.hidden:
            self.hidden_since.setdefault(w, now)
        for w in list(self.hidden_since):
            if w not in self.hidden:
                del self.hidden_since[w]
        if self.hidden_since != before:
            self.on_minimized(dict(self.hidden_since))

    def adopt_minimized(self, stamps):
        """After a restart: windows that are still minimised keep the time they were minimised at (the older one wins)."""
        for w, t in (stamps or {}).items():
            if w in self.hidden_since and isinstance(t, (int, float)):
                self.hidden_since[w] = min(self.hidden_since[w], float(t))

    def _forget(self, wid):
        self.order.remove(wid)
        self.hidden_since.pop(wid, None)
        for d in (self.size, self.cur, self.tgt, self.placed, self.placed_at):
            d.pop(wid, None)
        self.hidden.discard(wid)
        self._prepared.discard(wid)
        self.tmin.pop(wid, None)
        self._size_tries.pop(wid, None)
        self.original.pop(wid, None)
        if self.drag and self.drag["wid"] == wid:
            self.drag = None
            self._set_indicator(None)
        if self.resizing == wid:
            self.resizing = None

    # ----------------------------------------------------------- layout
    def layout(self):
        """Recompute target rects from the current state and start animating."""
        self.area = self._work_area()
        order = self.view_order()
        if not order:
            return                      # everything is minimised ("show desktop"): freeze, so it all comes back as it was
        rects, self.total = layout.compute(
            self._items(order), self.area, self.gap, self.margin, self.scroll, self.vmargin)
        items = self._items(order)
        self._bounds = (self.margin + items[0][1] / 2, self.total - self.margin - items[-1][1] / 2)
        if not self._frozen:
            self.scroll = self._clamp(self.scroll)
        rects, self.total = layout.compute(
            self._items(order), self.area, self.gap, self.margin, self.scroll, self.vmargin)

        skip = self.drag["wid"] if self.drag and self.drag["source"] == "window" else None
        keep = set()
        self.free_rects = {k: v for k, v in rects.items() if k not in (PLACEHOLDER, RESERVED)}
        self._settled = False
        self.on_zones([])          # stale while things move; tick() restores them when settled
        for ident, rect in rects.items():
            if ident in (PLACEHOLDER, RESERVED):
                continue
            keep.add(ident)
            if ident == skip or ident == self.resizing:
                self.tgt.pop(ident, None)
                continue
            x = max(self.area.x - rect.w + KEEP_ON_SCREEN, min(rect.x, self.full_right - KEEP_ON_SCREEN))
            self.tgt[ident] = Rect(x, rect.y, rect.w, rect.h)
            self.cur.setdefault(ident, x)
        for ident in list(self.tgt):
            if ident not in keep:
                del self.tgt[ident]

        key = None
        if self.drag:
            key = PLACEHOLDER if self.drag["source"] == "window" else self.drag["wid"]
        elif self.reserved:
            key = RESERVED
        self._set_indicator(rects.get(key) if key else None)
        centered = self._nearest_centre()
        if centered != self._centered:
            self._centered = centered
            self.on_change()                      # the taskbar highlight moves with the strip
        self.request_tick()

    def _set_indicator(self, rect):
        if rect != self.indicator:
            self.indicator = rect
            self.on_indicator(rect)

    def tick(self):
        """Advance the animation one frame. Returns True while still moving."""
        moving = False
        now = time.monotonic()
        for wid, t in self.tgt.items():
            c = self.cur.get(wid, t.x)
            if abs(t.x - c) < 1:
                c = t.x
            else:
                c += (t.x - c) * EASE
                moving = True
            self.cur[wid] = c
            rect = Rect(round(c), t.y, t.w, t.h)
            if self.placed.get(wid) != rect:
                self.b.place(wid, rect)
                self.placed[wid] = rect
                self.placed_at[wid] = now
        if not moving:
            vis = tuple(w for w, t in self.tgt.items() if t.intersects(self.area))
            if vis != self._last_vis:       # taskbar highlights visible windows
                self._last_vis = vis
                self.on_change()
            self._settled = True
            self.on_zones(self.gap_zones())
        return moving

    def refresh_zones(self):
        """Publish the gap zones again if the strip is at rest: a window lying over a gap (a tool window, a dialog) may
        have appeared, moved, been resized or gone. The backend skips the work if nothing changed."""
        if self._settled:
            self.on_zones(self.gap_zones())

    # ------------------------------------------------- add-a-window slot
    def reserve(self, idx, timeout=None):
        """Hold an empty slot open at ``idx`` for the next window that appears."""
        if self.drag or self.resizing is not None:
            return
        idx = max(0, min(idx, len(self.order)))
        w = max(MIN_W, min(RESERVE_W, self.area.w - 2 * self.margin, self._new_window_cap()))
        self.reserved = {"idx": idx, "w": w, "until": self._until(timeout)}
        self.layout()
        before = [x for x in self.order[:idx] if x not in self.hidden]
        slot_x = self.margin + sum(self.size[x][0] for x in before) + self.gap * len(before)
        self.scroll_to(layout.reveal(self.scroll, slot_x, w, self.area.w, self.margin))

    def reserve_timeout(self, seconds=LAUNCH_TIMEOUT):
        """The app is launching: keep the slot for ``seconds``, then give up."""
        if self.reserved:
            self.reserved["until"] = self._until(seconds)

    def release(self):
        if self.reserved:
            self.reserved = None
            self.layout()

    @staticmethod
    def _until(timeout):
        return None if timeout is None else time.monotonic() + timeout

    def gap_zones(self):
        """Right-clickable "insert here" regions: [(index, Rect)].

        One per gap between windows, plus a ``margin``-wide strip just outside
        the first and last window. Deliberately not the empty desktop around
        a short strip, so desktop icons stay usable.
        """
        vis = self._vis()
        if self.drag or self.resizing is not None or self.reserved or not vis:
            return []
        a, n, fr = self.area, len(vis), self.free_rects
        zones = []
        for i in range(n + 1):
            if i == 0:
                right = fr[vis[0]].x
                left = right - self.margin
            elif i == n:
                left = fr[vis[-1]].right
                right = left + self.margin
            else:
                left, right = fr[vis[i - 1]].right, fr[vis[i]].x
            left, right = max(left, a.x), min(right, a.right)
            if right - left >= MIN_ZONE:
                idx = self.order.index(vis[i]) if i < n else len(self.order)   # insert before the window to the right
                zones.append((idx, Rect(left, a.y, right - left, a.h)))
        holes = getattr(self.b, "gap_obstacles", lambda strip: [])(set(self.order)) if zones else []
        return layout.clip_zones(zones, holes, MIN_ZONE, MIN_ZONE)

    def resize_target(self, idx):
        """(window, side) the user means by dragging the gap at insert index ``idx``: the window left of the gap, by its
        right edge (the ones to its right are pushed along); at the very start the first window, by its left edge. None if
        there is no visible window."""
        vis = self._vis()
        if not vis:
            return None
        i = self._vidx(max(0, min(idx, len(self.order))))
        return (vis[0], "left") if i == 0 else (vis[min(i, len(vis)) - 1], "right")

    # ---------------------------------------------------------- scrolling
    def _clamp(self, value):
        # until the user scrolls or centres something, the strip behaves as before (centred while it is narrow)
        first, last = self._bounds if self._user_scrolled else (None, None)
        return layout.clamp_scroll(value, self.total, self.area.w, first, last)

    def scroll_to(self, value):
        self.scroll = self._clamp(value)
        self.layout()

    def scroll_by(self, px):
        self._user_scrolled = True
        self.scroll_to(self.scroll + px)

    def center(self, wid):
        """Scroll so the window is in the middle of the screen, even if it is the first or last one. False if it is
        not on the strip right now."""
        xs = self._strip_xs()
        if wid not in xs:
            return False
        self._user_scrolled = True
        self.scroll_to(layout.center_scroll(xs[wid], self.size[wid][0], self.area.w))
        return True

    def scroll_step_dir(self, direction):
        """direction: -1 = towards the left of the strip, +1 = right."""
        self.scroll_by(direction * self.scroll_step)

    def reveal(self, wid):
        xs = self._strip_xs()
        if wid not in xs:               # unknown, or minimised and not on the strip right now
            return
        self.scroll_to(layout.reveal(
            self.scroll, xs[wid], self.size[wid][0], self.area.w, self.margin))

    def _strip_xs(self):
        xs, x = {}, self.margin
        for wid in self._vis():
            xs[wid] = x
            x += self.size[wid][0] + self.gap
        return xs

    def focus(self, wid):
        """Taskbar click: activate the window and bring it to the middle of the screen."""
        self.b.activate(wid)
        if not self.center(wid):
            self._center_pending = wid          # minimised: centre it once it is back on the strip

    def click(self, wid):
        """Taskbar click. On the window that is both the highlighted item and the focused one it minimises that window (the
        usual dock behaviour); on anything else it focuses and centres it. A highlighted window that does not have the
        focus is focused first: its click must not make it disappear."""
        highlighted = self._centered if self._centered in self.order else self.b.active()
        if wid == highlighted and wid == self.b.active() and wid not in self.hidden:
            self.b.minimize(wid)
        else:
            self.focus(wid)

    def _show_focused(self):
        """After the strip changed or focus moved: centre a window the user just clicked, otherwise just reveal it."""
        pend = self._center_pending
        if pend is not None and pend not in self.order:
            pend = self._center_pending = None
        if pend is not None and self.center(pend):
            self._center_pending = None
            return
        active = self.b.active()
        if active in self.order and not self.drag:
            self.reveal(active)

    def active_changed(self):
        self._show_focused()
        self.on_change()

    # ------------------------------------------------------- reordering
    def reorder(self, wid, idx, visible=False):
        """Commit: put ``wid`` at ``idx`` among the other windows.

        ``idx`` counts every window (taskbar drops) or, with ``visible=True``, only the ones on the strip (desktop drops).
        """
        if wid not in self.order:
            return
        others = [w for w in self.order if w != wid]
        if visible:
            vo = [w for w in others if w not in self.hidden]
            if idx < len(vo):
                pos = others.index(vo[idx])
            else:
                pos = others.index(vo[-1]) + 1 if vo else len(others)
        else:
            pos = max(0, min(idx, len(others)))
        others.insert(pos, wid)
        self.order = others
        self.drag = None
        self._set_indicator(None)
        self.reveal(wid)           # also re-lays out; show the user where it went
        self.on_change()

    def bar_preview(self, wid, idx):
        """Taskbar drag in progress: show where the window would land."""
        if wid not in self.order:
            return
        self.reserved = None
        self.drag = {"wid": wid, "idx": idx, "source": "bar"}
        self.layout()

    def clear_preview(self):
        if self.drag and self.drag["source"] == "bar":
            self.drag = None
            self._set_indicator(None)
            self.layout()

    def index_for_center(self, wid, cx_screen):
        """Drop index for a window whose centre is at ``cx_screen``.

        Measured against the full strip (dragged window still in its own
        slot), so a small nudge never reorders and the result doesn't depend
        on the preview layout that is currently displayed.
        """
        cx = cx_screen - self.area.x + self.scroll
        xs = self._strip_xs()
        return sum(1 for w in self._vis()
                   if w != wid and xs[w] + self.size[w][0] / 2 < cx)

    # ---------------------------------------- direct manipulation (poll)
    def poll(self):
        """Called ~30x/second: detects window drags and resizes made with the mouse."""
        _, _, down = self.b.pointer()
        r = self.reserved
        if r and r["until"] is not None and time.monotonic() > r["until"]:
            self.release()                 # the launched app never opened a window
        if self.drag and self.drag["source"] == "window":
            self._drag_step(down)
            return
        if self.resizing is not None:
            self._resize_step(down)
            return
        if not down:
            return
        wid = self.b.active()
        if wid not in self.order or wid in self.hidden or wid in self.tgt and self._animating(wid):
            return
        placed = self.placed.get(wid)
        frame = self.b.frame_rect(wid)
        if placed is None or frame is None:
            return
        if time.monotonic() - self.placed_at.get(wid, 0) < SETTLE_S:
            return
        if abs(frame.w - placed.w) > 3 or abs(frame.h - placed.h) > 3:
            self.resizing = wid
            self._resize_step(down)
        elif abs(frame.x - placed.x) > DRAG_START_PX or abs(frame.y - placed.y) > DRAG_START_PX:
            self.reserved = None
            self.drag = {"wid": wid, "idx": self.order.index(wid), "source": "window"}
            self.cur[wid] = frame.x
            self._drag_step(down)

    def _animating(self, wid):
        t = self.tgt.get(wid)
        return t is not None and abs(self.cur.get(wid, t.x) - t.x) >= 1

    def _drag_step(self, down):
        d = self.drag
        frame = self.b.frame_rect(d["wid"])
        px, _, _ = self.b.pointer()
        if not down:
            self.reorder(d["wid"], d["idx"], visible=True)
            return
        if frame is None:
            return
        # auto-scroll when the pointer sits near the left/right screen edge
        if px < self.area.x + EDGE_SCROLL_ZONE:
            self.scroll = self._clamp(self.scroll - EDGE_SCROLL_SPEED)
        elif px > self.area.right - EDGE_SCROLL_ZONE:
            self.scroll = self._clamp(self.scroll + EDGE_SCROLL_SPEED)
        idx = self.index_for_center(d["wid"], frame.cx)
        d["idx"] = idx
        self.layout()

    def _resize_step(self, down):
        wid = self.resizing
        frame = self.b.frame_rect(wid)
        if frame is None:
            self.resizing = None
            self._frozen = False
            return
        avail_h = self._fill_h(wid)
        w = max(MIN_W, min(frame.w, self.area.w - 2 * self.margin))
        self.size[wid] = [w, None]               # the height is not the user's to change: every window fills the strip
        self.cur[wid] = frame.x
        if down:
            # Pin the strip so the slot stays under the window being dragged,
            # even when the strip is centred and its total width is changing.
            self._frozen = True
            self.scroll = self.area.x + self._strip_xs()[wid] - frame.x
            self.layout()
        else:
            self.resizing = None
            self._frozen = False
            self.layout()          # now snap the resized window into its slot
            self.on_change()

    def reconcile(self):
        """Quick check (~8x/s): windows must end up the size we gave them.

        Many apps open at their own size, or restore a saved size a moment after they appeared, which would leave a new
        window shorter or taller than the strip (and wider than the new-window cap). So when a window's real size differs
        from what we placed, we place it again, a few times. Only a window that keeps refusing (its minimum or maximum
        size forbids it) is taken as it is, and the layout follows it."""
        _, _, down = self.b.pointer()
        if down or self.drag or self.resizing is not None:
            return
        now = time.monotonic()
        changed = resend = False
        for wid in self.order:
            placed = self.placed.get(wid)
            if (placed is None or wid in self.hidden or now - self.placed_at.get(wid, 0) < 0.2
                    or self._animating(wid)):
                continue
            frame = self.b.frame_rect(wid)
            if frame is None:
                continue
            w_off, h_off = abs(frame.w - placed.w) > 3, abs(frame.h - placed.h) > 3
            if not (w_off or h_off):
                self._size_tries.pop(wid, None)
                continue
            if self._size_tries.get(wid, 0) < SIZE_RETRIES:
                self._size_tries[wid] = self._size_tries.get(wid, 0) + 1
                self.placed.pop(wid, None)               # forces the next tick to send the size again
                resend = True
                continue
            avail_h = self._fill_h(wid)                  # it will not take it: live with what it gives us
            self.size[wid] = [frame.w, None if frame.h >= avail_h - 2 else frame.h]
            self._size_tries.pop(wid, None)
            changed = True
        if changed:
            self.layout()
        elif resend:
            self.request_tick()

    # ------------------------------------------------------------ exit
    def restore_all(self):
        for wid, rect in self.original.items():
            try:
                self.b.place(wid, rect)
            except Exception:
                pass
