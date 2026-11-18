"""Screenshot the lab display to a PNG:  shot.py out.png [scale]"""
import sys

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

root = Gdk.get_default_root_window()
w, h = root.get_width(), root.get_height()
pix = Gdk.pixbuf_get_from_window(root, 0, 0, w, h)
scale = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
pix.scale_simple(int(w * scale), int(h * scale), GdkPixbuf.InterpType.BILINEAR).savev(sys.argv[1], "png", [], [])
