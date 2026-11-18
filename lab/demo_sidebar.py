"""A floating sidebar like the 'LLM usage' widget: UTILITY, always on top, sticky, full height, at the right edge."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

W = 356
win = Gtk.Window()
win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
win.set_decorated(False)
win.set_keep_above(True)
win.stick()
win.set_skip_taskbar_hint(True)
win.set_default_size(W, 1440)
win.move(5120 - W, 0)
win.set_title("LLM usage")
box = Gtk.EventBox()
box.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.07, 0.08, 0.10, 1))
lab = Gtk.Label(label="SIDEBAR")
lab.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.8))
box.add(lab)
win.add(box)
win.show_all()
Gtk.main()
