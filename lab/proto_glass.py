"""Prototype: translucent boxes with kwin blur-behind, as an override-redirect (unmanaged) or a managed window.
usage: proto_glass.py managed|unmanaged OUT.png"""
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import cairo  # noqa: E402
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from Xlib import Xatom, display  # noqa: E402

mode, out = sys.argv[1], sys.argv[2]
import os
X0, Y0, W, H = int(os.environ.get("PX", 2000)), int(os.environ.get("PY", 1330)), 700, 52
win = Gtk.Window(type=Gtk.WindowType.POPUP if mode == "unmanaged" else Gtk.WindowType.TOPLEVEL)
win.set_decorated(False)
win.set_app_paintable(True)
win.set_default_size(W, H)
win.set_size_request(W, H)
if mode == "managed":
    win.set_type_hint(Gdk.WindowTypeHint.DOCK)
    win.set_title("proto-managed")
scr = win.get_screen()
print("composited:", scr.is_composited())
win.set_visual(scr.get_rgba_visual())
boxes = [(10, 6, 150, 40), (172, 6, 190, 40), (374, 6, 120, 40)]


def draw(_w, cr):
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    for x, y, w, h in boxes:
        r = 6
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0); cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416); cr.arc(x + r, y + r, r, 3.1416, 4.7124); cr.close_path()
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(1)
        cr.stroke()
    return False


win.connect("draw", draw)
root0 = Gdk.get_default_root_window()
baseline = Gdk.pixbuf_get_from_window(root0, X0 - 100, Y0 - 40, W + 200, H + 80)     # before our window exists
win.move(X0, Y0)
win.show_all()


def setup():
    d = display.Display()
    w = d.create_resource_object("window", win.get_window().get_xid())
    data = []
    for x, y, bw, bh in boxes:                                  # blur-behind region: the boxes (rectangular here)
        data += [x, y, bw, bh]
    w.change_property(d.intern_atom("_KDE_NET_WM_BLUR_BEHIND_REGION"), Xatom.CARDINAL, 32, data)
    d.sync()
    print("blur property set")
    return False


def shot():
    root = Gdk.get_default_root_window()
    now = Gdk.pixbuf_get_from_window(root, X0 - 100, Y0 - 40, W + 200, H + 80)
    now.savev(out, "png", [], [])
    # compare the gap between box 1 and box 2 (window x 162..170) and the area right of the boxes against the baseline
    rs, ch = now.get_rowstride(), now.get_n_channels()
    a, b = now.get_pixels(), baseline.get_pixels()
    def diff(x, y):
        o = (y + 40) * rs + (x + 100) * ch
        return max(abs(a[o + i] - b[o + i]) for i in range(3))
    print("rows (window y) where the empty gap between boxes differs from the baseline by >6:")
    bad = [(y, max(diff(x, y) for x in range(163, 170))) for y in range(-6, H + 6)]
    bad = [t for t in bad if t[1] > 6]
    print("   ", bad if bad else "none: nothing is drawn there (no outline, fully transparent)")
    print("right of the boxes (x 520..690), rows -3..+55:",
          [(y, max(diff(x, y) for x in range(520, 690, 10))) for y in (-2, -1, 0, 1, 26, H - 2, H - 1, H, H + 1) if max(diff(x, y) for x in range(520, 690, 10)) > 6] or "none")
    Gtk.main_quit()
    return False


GLib.timeout_add(300, setup)
GLib.timeout_add(1500, shot)
Gtk.main()
