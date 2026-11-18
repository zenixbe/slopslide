"""Small pure geometry helpers (no X, no GTK)."""
import math


def rounded_rects(x, y, w, h, r):
    """Rectangles (x, y, w, h) that exactly cover a rounded rectangle, compactly.

    The corner rows each get their own width, everything between is one rectangle, so a 40px tall box with 6px corners is
    13 rectangles instead of 40. Used for X shape regions and kwin's blur-behind region.
    """
    w, h = int(w), int(h)
    if w < 1 or h < 1:
        return []
    r = max(0, min(int(r), w // 2, h // 2))

    def inset(row):                               # distance of the row's ends from the box edge, for rows inside a corner
        d = r - row - 0.5
        return math.ceil(r - math.sqrt(max(0.0, r * r - d * d)))

    out = []
    for row in range(r):
        i = inset(row)
        out.append((x + i, y + row, w - 2 * i, 1))
    if h - 2 * r > 0:
        out.append((x, y + r, w, h - 2 * r))
    for row in range(r):
        i = inset(r - 1 - row)
        out.append((x + i, y + h - r + row, w - 2 * i, 1))
    return out
