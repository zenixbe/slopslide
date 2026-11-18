"""Pure layout maths for the scrolling strip. No X, no GTK: easy to unit test.

Strip coordinates: x=0 is the left edge of the whole strip. A window's screen
position is ``area.x + strip_x - scroll``.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    @property
    def cx(self):
        return self.x + self.w / 2

    def intersects(self, other):
        return (self.x < other.right and other.x < self.right
                and self.y < other.bottom and other.y < self.bottom)


def compute(items, area, gap, margin, scroll, vmargin=None):
    """Lay out ``items`` in one row.

    Each item is ``(id, width, height_or_None[, min_top])``. Returns ({id: Rect in screen coordinates}, total strip width).
    ``margin`` is the horizontal margin (strip ends); ``vmargin`` (default: same) the top/bottom one.
    ``min_top`` lets one window sit lower than the others (its top is ``max(vmargin, min_top)``) while every window
    keeps the same bottom edge. Height None means "fill down to the bottom margin".
    """
    vmargin = margin if vmargin is None else vmargin
    rects = {}
    x = margin
    for item in items:
        ident, w, h = item[:3]
        top = max(vmargin, item[3] if len(item) > 3 else 0)
        avail_h = max(1, area.h - top - vmargin)
        height = avail_h if h is None else max(1, min(h, avail_h))
        rects[ident] = Rect(round(area.x + x - scroll), area.y + top, w, height)
        x += w + gap
    total = (x - gap if items else 0) + margin
    return rects, max(total, 2 * margin)


def scroll_bounds(total, view_w, first_cx=None, last_cx=None):
    """Allowed (lowest, highest) scroll.

    Classically the strip may only scroll until its ends touch the screen edges (or sits centred while it is narrower than
    the screen). With ``first_cx`` / ``last_cx`` (strip x of the first / last window's centre) the range is widened so
    that *any* window, even the first or the last one with nothing on one side, can be brought to the screen centre.
    """
    if total <= view_w:
        lo = hi = (total - view_w) / 2
    else:
        lo, hi = 0, total - view_w
    if first_cx is not None:
        lo = min(lo, first_cx - view_w / 2)
    if last_cx is not None:
        hi = max(hi, last_cx - view_w / 2)
    return lo, hi


def clamp_scroll(scroll, total, view_w, first_cx=None, last_cx=None):
    """Keep the strip on screen; centre it while it is narrower than the view (see ``scroll_bounds``)."""
    lo, hi = scroll_bounds(total, view_w, first_cx, last_cx)
    return min(max(scroll, lo), hi)


def center_scroll(strip_x, w, view_w):
    """Scroll that puts the window [strip_x, strip_x+w] in the middle of the view."""
    return strip_x + w / 2 - view_w / 2


def reveal(scroll, strip_x, w, view_w, margin):
    """Smallest scroll change that brings [strip_x, strip_x+w] fully into view."""
    if w + 2 * margin >= view_w:
        return strip_x - margin
    if strip_x - margin < scroll:
        return strip_x - margin
    if strip_x + w + margin > scroll + view_w:
        return strip_x + w + margin - view_w
    return scroll


def find_sidebar_candidate(candidates, screen_w, screen_h):
    """The candidate dict of the floating widget sidebar on the right edge, or None.

    ``candidates``: dicts with ``type`` (first window type), ``states`` (set of state names), ``rect`` (a Rect) and
    ``viewable``. A sidebar is a mapped, always-on-top *utility* window, sitting at the right edge, tall and not huge.
    Deliberately narrow, so that notification layers (a full-height transparent window on the right), our own parked
    windows (normal windows with 1px showing) and ordinary windows never count. If several qualify, the one reaching
    furthest left wins.
    """
    best = None
    for c in candidates:
        r = c["rect"]
        if (c["type"] in ("UTILITY", "TOOLBAR") and c["viewable"] and "HIDDEN" not in c["states"]
                and c["states"] & {"ABOVE", "STAYS_ON_TOP"}
                and r.right >= screen_w - 80 and 120 <= r.w <= 900 and r.h >= screen_h * 0.4):
            if best is None or r.x < best["rect"].x:
                best = c
    return best


def find_sidebar(candidates, screen_w, screen_h):
    """The rectangle of the sidebar, or None (see ``find_sidebar_candidate``)."""
    c = find_sidebar_candidate(candidates, screen_w, screen_h)
    return None if c is None else c["rect"]


def right_inset(candidates, screen_w, screen_h, pad):
    """How much of the screen's right side a floating widget sidebar takes (plus ``pad``), or 0."""
    r = find_sidebar(candidates, screen_w, screen_h)
    return 0 if r is None else max(0, screen_w - r.x + pad)


def fade_alpha(t):
    """Opacity of the wallpaper laid over the windows at ``t`` (0 = where the fade starts, 1 = where the solid gap starts).
    The window's own opacity is 1 - this. It drops fast at first and then eases out, because the eye still reads dark text
    on a dark wallpaper when only half the page has faded: text must be nearly gone before the fade is half way."""
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 2.4


def fade_zone(sidebar, width, screen_w):
    """Rect of the fade strip left of the sidebar (same height), or None if there is no room / nothing to fade."""
    if sidebar is None or width <= 0 or sidebar.x - width < 0:
        return None
    return Rect(sidebar.x - width, sidebar.y, width, sidebar.h)


def cover_zone(sidebar, fade, screen_w):
    """Rect of the whole wallpaper cover: the fade strip *and* everything under the sidebar itself, so that no window is
    ever visible there (not even through gaps in the sidebar). None if there is nothing to cover."""
    z = fade_zone(sidebar, fade, screen_w)
    return None if z is None else Rect(z.x, z.y, z.w + sidebar.w, z.h)


def directly_below(children, xid, sibling):
    """True if ``xid`` sits immediately under ``sibling`` in a bottom-to-top list of window ids."""
    return xid in children and sibling in children and children.index(xid) == children.index(sibling) - 1
