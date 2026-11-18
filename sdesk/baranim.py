"""Animated one-dimensional layout for the taskbar. Pure Python, no GTK: unit-testable.

Every item has a *current* width that eases towards its *natural* width, and an item's x position is simply the sum of the
current widths before it. So anything that changes size (a longer title, bold text, a window opening or closing, a gap
opening for a dragged item) moves all its neighbours smoothly, and nothing ever changes size suddenly.

Reordering and removal need a little more care and are handled with "offsets" and "dying" items, see below.
"""

SPACER = "\0gap"


class Row:
    def __init__(self, ease=0.30, snap=0.6, tail=(), head=()):
        self.ease = ease
        self.snap = snap
        self.tail = set(tail)        # keys that always sit last (the "+" button); a gap opens before them
        self.head = set(head)        # keys that always sit first (clock, sound): not draggable, never counted as items
        self.order = []              # display order; excludes the dragged item and the spacer, includes dying items
        self.nat = {}                # key -> natural (target) width
        self.cur = {}                # key -> current width
        self.off = {}                # key -> x offset not yet absorbed (set by reorders, decays to 0)
        self.dying = set()           # removed items that are shrinking away; dropped from the row at width 0
        self.dragged = None          # key being dragged (taken out of the row, its slot replaced by the spacer)
        self.gap = {"idx": None, "pos": 0, "cur": 0.0, "nat": 0.0, "off": 0.0}

    # ---------------------------------------------------------------- layout
    def _live(self):
        """Keys that are neither dying nor in the head or tail, in order."""
        return [k for k in self.order if k not in self.dying and k not in self.tail and k not in self.head]

    def _sequence(self, order=None, spacer=True):
        """[(key, width)] in display order, with the spacer in its place."""
        order = self.order if order is None else order
        seq = [(k, self.cur[k]) for k in order]
        gap_w = self.gap["cur"]
        if spacer and (gap_w > 0 or self.gap["idx"] is not None):
            live = [k for k in order if k not in self.dying and k not in self.tail and k not in self.head]
            g = self.gap["idx"] if self.gap["idx"] is not None else self.gap["pos"]
            if g < len(live):
                at = order.index(live[g])
            else:                                       # after the last live item, before the tail
                tails = [i for i, k in enumerate(order) if k in self.tail]
                at = tails[0] if tails else len(order)
            seq.insert(at, (SPACER, gap_w))
        return seq

    def positions(self):
        """{key: (x, width)} as currently rendered (x includes any pending offset)."""
        out, x = {}, 0.0
        for key, w in self._sequence():
            if key != SPACER:
                out[key] = (x + self.off.get(key, 0.0), w)
            x += w
        return out

    def total(self):
        return sum(w for _, w in self._sequence())

    def gap_position(self):
        """(x, width) of the spacer as rendered, or None."""
        x = 0.0
        for key, w in self._sequence():
            if key == SPACER:
                return x + self.gap["off"], w
            x += w
        return None

    def closed_layout(self):
        """{key: (x, w)} of the *natural* layout without the dragged item and without a gap.

        Used to turn a pointer position into a drop index: it does not change while the gap animates open, so the index
        can't oscillate (and the middle of the opened gap sits exactly on the boundary this layout reports)."""
        out, x = {}, 0.0
        for k in self.order:
            if k in self.dying:
                continue
            w = self.nat.get(k, 0.0)
            out[k] = (x, w)
            x += w
        return out, x

    def drop_index(self, x):
        """Index (among the live items) at which a dragged item at closed-layout x would be dropped."""
        layout, _ = self.closed_layout()
        n = 0
        for k in self._live():
            kx, kw = layout[k]
            if kx + kw / 2 < x:
                n += 1
        return n

    # --------------------------------------------------------------- changes
    def _snapshot(self):
        return self.positions(), self.gap_position()

    def _rebase(self, snap):
        """After a structural change (items moved, gap moved): keep every item and the gap exactly where they are drawn,
        and let the difference to their new natural place ease away. This is what makes things glide instead of jump."""
        old_pos, old_gap = snap
        new_pos = self.positions()
        for k in self.order:
            if k in old_pos:
                self.off[k] = old_pos[k][0] - (new_pos[k][0] - self.off.get(k, 0.0))
        gap_now = self.gap_position()
        if old_gap is not None and gap_now is not None:
            self.gap["off"] = old_gap[0] - (gap_now[0] - self.gap["off"])
        elif gap_now is None:
            self.gap["off"] = 0.0

    def set_items(self, keys, nat):
        """Replace the item list. New items grow in from 0, vanished ones shrink away, moved ones glide."""
        snap = self._snapshot()
        old_order = list(self.order)
        for k in keys:
            if k not in self.cur:
                self.cur[k] = 0.0
            self.nat[k] = nat[k]
            self.dying.discard(k)
        new_order = [k for k in keys if k != self.dragged]
        # items that disappeared stay (shrinking) right after whatever preceded them
        for k in old_order:
            if k in new_order or k == self.dragged:
                continue
            self.dying.add(k)
            self.nat[k] = 0.0
            prev = [p for p in old_order[:old_order.index(k)] if p in new_order]
            at = new_order.index(prev[-1]) + 1 if prev else 0
            tails = [i for i, t in enumerate(new_order) if t in self.tail and i >= at]
            if tails and k not in self.tail:
                at = min(at, tails[0])
            at = max(at, sum(1 for t in new_order if t in self.head))        # never in front of the head items
            new_order.insert(at, k)
        self.order = new_order
        self._rebase(snap)
        # drop the dragged item's stale entries if it vanished while dragging
        if self.dragged is not None and self.dragged not in keys:
            self.gap.update(idx=None)
            self.dragged = None

    def pick_up(self, key):
        """Take ``key`` out of the row; its slot becomes a spacer of the same width, so nothing moves."""
        if key not in self.order or key in self.dying or key in self.head or self.dragged is not None:
            return False
        live = self._live()
        idx = live.index(key)
        self.gap.update(idx=idx, pos=idx, cur=self.cur[key], nat=self.nat[key])
        self.order.remove(key)
        self.dragged = key
        self.off.pop(key, None)
        return True

    def set_gap(self, idx):
        """Move the gap: the items in between glide aside instead of jumping."""
        if self.dragged is None:
            return
        idx = max(0, min(idx, len(self._live())))
        if idx == self.gap["idx"]:
            return
        snap = self._snapshot()
        self.gap.update(idx=idx, pos=idx)
        self._rebase(snap)

    def drop(self, idx=None):
        """Put the dragged item into the gap (at ``idx``, default: where the gap is). It takes over the gap's current
        width, so the total never changes at the moment of landing; it then grows to its natural width."""
        key = self.dragged
        if key is None:
            return None
        live = self._live()
        idx = self.gap["idx"] if idx is None else idx
        idx = max(0, min(idx if idx is not None else self.gap["pos"], len(live)))
        at = self.order.index(live[idx]) if idx < len(live) else (
            next((i for i, k in enumerate(self.order) if k in self.tail), len(self.order)))
        snap = self._snapshot()
        self.order.insert(at, key)
        self.cur[key] = self.gap["cur"]
        self.off[key] = 0.0
        self.gap.update(idx=None, cur=0.0, nat=0.0)
        self.dragged = None
        if snap[1] is not None:                   # the dropped item takes over exactly the spot the gap was drawn at
            others = {k: v for k, v in snap[0].items()}
            self._rebase((others, None))
            self.off[key] = snap[1][0] - (self.positions()[key][0] - self.off[key])
        self.gap["off"] = 0.0
        return idx

    # ------------------------------------------------------------- animation
    def step(self):
        """Advance one frame. Returns (still_moving, [keys that are gone for good])."""
        moving, gone = False, []
        for k in list(self.order):
            target = 0.0 if k in self.dying else self.nat.get(k, 0.0)
            c = self.cur.get(k, 0.0)
            if abs(target - c) <= self.snap:
                c = target
            else:
                c += (target - c) * self.ease
                moving = True
            self.cur[k] = c
            o = self.off.get(k, 0.0)
            if o:
                o *= 1 - min(0.9, self.ease * 1.15)
                if abs(o) < 0.5:
                    o = 0.0
                else:
                    moving = True
                self.off[k] = o
            if k in self.dying and c <= 0.0:
                gone.append(k)
        for k in gone:
            self.order.remove(k)
            for d in (self.cur, self.nat, self.off):
                d.pop(k, None)
            self.dying.discard(k)
        g = self.gap
        if g["off"]:
            g["off"] *= 1 - min(0.9, self.ease * 1.15)
            if abs(g["off"]) < 0.5:
                g["off"] = 0.0
            else:
                moving = True
        target = g["nat"] if g["idx"] is not None else 0.0
        if abs(target - g["cur"]) <= self.snap:
            g["cur"] = target
        else:
            g["cur"] += (target - g["cur"]) * self.ease
            moving = True
        return moving, gone
