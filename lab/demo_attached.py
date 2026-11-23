"""A stand-in for the T3 task tree (issue #24): a 360 px UTILITY window, transient for the window with WM_CLASS <class>.
It never moves itself, like the real tree while sdesk holds the attached-window contract.
    demo_attached.py <parent-class> [hex-colour]"""
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402

cls = sys.argv[1]
colour = sys.argv[2] if len(sys.argv) > 2 else "e0a030"
w = Gtk.Window(title="tasktree stand-in")
w.set_wmclass("llm-usage-tasktree", "LLM-usage-tasktree")
w.set_type_hint(Gdk.WindowTypeHint.UTILITY)
w.set_skip_taskbar_hint(True)
w.set_skip_pager_hint(True)
w.set_default_size(360, 900)
box = Gtk.EventBox()
box.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(*[int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)], 1))
lbl = Gtk.Label(label="TREE")
box.add(lbl)
w.add(box)
w.connect("destroy", Gtk.main_quit)
w.show_all()


def attach():
    out = subprocess.run(["xdotool", "search", "--class", cls], capture_output=True, text=True).stdout.split()
    if not out:
        return True
    parent = GdkX11.X11Window.foreign_new_for_display(Gdk.Display.get_default(), int(out[0]))
    w.get_window().set_transient_for(parent)
    return False


GLib.timeout_add(500, attach)
Gtk.main()
