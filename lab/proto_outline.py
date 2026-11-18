"""Which property makes Deepin's compositor draw an outline around a window?  proto_outline.py full|tight rgba|opaque shape|noshape"""
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from Xlib import X, display  # noqa: E402
from Xlib.ext import shape  # noqa: E402

size, vis, shp = sys.argv[1:4]
SW, Y0, H = 5120, 760, 52              # mid-screen, away from the real bar
ROWW, ROWX = 700, 2000
W = SW if size == "full" else ROWW
WX = 0 if size == "full" else ROWX
win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
win.set_decorated(False)
win.set_app_paintable(True)
win.set_type_hint(Gdk.WindowTypeHint.DOCK)
win.set_keep_above(True)
win.stick()
win.set_title("proto-outline")
win.set_default_size(W, H)
win.set_size_request(W, H)
scr = win.get_screen()
if vis == "rgba":
    win.set_visual(scr.get_rgba_visual())


def draw(_w, cr):
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0.2, 0.25, 0.3, 1 if vis == "opaque" else 0.0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    x = (ROWX if size == "full" else 0)
    for dx in (10, 172, 374):
        cr.rectangle(x + dx, 6, 150 if dx == 10 else 190 if dx == 172 else 120, 40)
        cr.set_source_rgba(0.9, 0.9, 0.9, 1 if vis == "opaque" else 0.5)
        cr.fill()
    return False


win.connect("draw", draw)
root0 = Gdk.get_default_root_window()
base_left = Gdk.pixbuf_get_from_window(root0, 100, Y0 - 6, 400, H + 12)              # far left of the future window
base_row = Gdk.pixbuf_get_from_window(root0, ROWX, Y0 - 6, ROWW, H + 12)
win.move(WX, Y0)
win.show_all()


def setup():
    d = display.Display()
    w = d.create_resource_object("window", win.get_window().get_xid())
    if shp == "shape":
        rect = [((ROWX if size == "full" else 0), 0, ROWW, H)]
        w.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, rect)
        w.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, rect)
    d.sync()
    return False


def rows_diff(now, base, x0, x1, nrows):
    rs, ch = now.get_rowstride(), now.get_n_channels()
    a, b = now.get_pixels(), base.get_pixels()
    out = {}
    for r in range(nrows):
        m = max(max(abs(a[r * rs + x * ch + i] - b[r * rs + x * ch + i]) for i in range(3)) for x in range(x0, x1, 7))
        if m > 6:
            out[r - 6] = m                                        # row relative to the window's top edge
    return out


def check():
    root = Gdk.get_default_root_window()
    far = rows_diff(Gdk.pixbuf_get_from_window(root, 100, Y0 - 6, 400, H + 12), base_left, 0, 400, H + 12)
    inrow = rows_diff(Gdk.pixbuf_get_from_window(root, ROWX, Y0 - 6, ROWW, H + 12), base_row, 160, 172, H + 12)   # gap between boxes
    print(f"{size:5s} {vis:6s} {shp:8s} | far from the row (x=100..500): {sorted(far) if far else 'untouched'}"
          f" | gap between boxes: rows {sorted(inrow) if inrow else 'untouched'}")
    Gtk.main_quit()
    return False


GLib.timeout_add(300, setup)
GLib.timeout_add(1600, check)
Gtk.main()
