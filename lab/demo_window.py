"""A plain coloured GTK window for lab testing:  demo_window.py <name> <class> <hex-colour>"""
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

name, cls, colour = sys.argv[1], sys.argv[2], sys.argv[3]
w = Gtk.Window(title=name)
w.set_wmclass(cls, cls)
w.set_default_size(1100, 800)
label = Gtk.Label(label=name)
label.override_font(__import__("gi.repository.Pango", fromlist=["x"]).FontDescription("sans 40"))
box = Gtk.EventBox()
box.override_background_color(Gtk.StateFlags.NORMAL, __import__("gi.repository.Gdk", fromlist=["x"]).RGBA(*[int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)], 1))
box.add(label)
w.add(box)
w.connect("destroy", Gtk.main_quit)
w.show_all()
if len(sys.argv) > 4:                       # a "stubborn" app: puts its own size back a moment after opening (and again later)
    from gi.repository import GLib
    for ms in (int(sys.argv[4]), int(sys.argv[4]) + 1800):
        GLib.timeout_add(ms, lambda: (w.resize(1100, 420), False)[1])
Gtk.main()
