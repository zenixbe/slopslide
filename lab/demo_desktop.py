"""A DESKTOP-type window painting a coloured 'wallpaper', like Deepin's desktop window does."""
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

W, H = 5120, 1440
win = Gtk.Window()
win.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
win.set_decorated(False)
win.set_default_size(W, H)
win.move(0, 0)
win.set_title("Desktop")


def draw(_w, cr):
    g = cairo.LinearGradient(0, 0, W, H)
    g.add_color_stop_rgb(0, 0.04, 0.30, 0.34)
    g.add_color_stop_rgb(1, 0.10, 0.55, 0.50)
    cr.set_source(g)
    cr.paint()
    for i in range(40):                                   # some structure so the blend is easy to see
        x, y = (i * 997) % W, (i * 389) % H
        cr.set_source_rgba(0.9, 0.9, 0.6, 0.25)
        cr.arc(x, y, 40 + (i % 5) * 18, 0, 6.3)
        cr.fill()
    return False


win.connect("draw", draw)
win.show_all()
Gtk.main()
