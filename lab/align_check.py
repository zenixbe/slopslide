"""Compare the bar's own pixels *between* its boxes with the true wallpaper: they must be identical (no boxy effect).

Looks at the strip of the bar window above the boxes (the top SLOT_PAD rows), which only ever shows the wallpaper copy.
usage: align_check.py   (prints the largest per-channel difference and how many pixels differ)
"""
import sys

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402
from Xlib import display  # noqa: E402
from Xlib.ext import shape  # noqa: E402

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import set_wallpaper  # noqa: E402

d = display.Display()
root_w = d.screen().root
bar_id = None
for xid in root_w.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), 0).value:
    w = d.create_resource_object("window", xid)
    name = w.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
    if name is not None and bytes(name.value) == b"sdesk-taskbar":
        bar_id = w
o = d.screen().root.translate_coords(bar_id, 0, 0)
rects = [(r.x, r.y, r.width, r.height) for r in shape.get_rectangles(bar_id, shape.SK.Bounding).rectangles]
ex, ey, ew, eh = rects[0]
root = Gdk.get_default_root_window()
SKIP = 3                                             # ignore the very top rows (kwin/AA edge) and test 4 rows
y0 = o.y + 1
pix = Gdk.pixbuf_get_from_window(root, o.x + ex, y0, ew, 4)
true = set_wallpaper.make()
data, stride = bytes(true.get_data()), true.get_stride()
px = pix.get_pixels()
rs, ch = pix.get_rowstride(), pix.get_n_channels()
worst, bad = 0, 0
for row in range(4):
    for col in range(ew):
        r, gg, b = px[row * rs + col * ch: row * rs + col * ch + 3]
        off = (y0 + row) * stride + (o.x + ex + col) * 4          # cairo RGB24 little endian: B G R x
        tb, tg, tr = data[off], data[off + 1], data[off + 2]
        diff = max(abs(r - tr), abs(gg - tg), abs(b - tb))
        worst = max(worst, diff)
        bad += diff > 6
print(f"bar extents x={o.x+ex} w={ew}: worst channel difference {worst}, pixels off by more than 6: {bad} of {4*ew}")
