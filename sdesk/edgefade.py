"""Windows fold away under a strip of wallpaper beside a floating sidebar, and are never visible under it.

One click-through *dock-type* window holds a copy of the wallpaper. It starts `gap + fade` pixels left of the sidebar: over
the first `fade` pixels the wallpaper is blended in from nothing to opaque (a short soft edge), and from there on it is fully
opaque: a window sliding right dissolves briefly and then disappears under the gap, leaving plain wallpaper before the
sidebar. Because it also covers the sidebar's own area, a window can never show through the gaps of a translucent or rounded
sidebar either.

Why a dock window: kwin paints unmanaged (override-redirect) windows above everything, whatever their X stacking, so such a
cover would hide the sidebar. kwin's own layers do the right thing: dock windows are above normal windows and below
always-on-top windows (the sidebar), without any restacking.

It needs a compositor (a window can only be translucent with one) and never takes input. It lives in sdesk's process: if
sdesk ends for any reason the cover is gone with it.
"""
import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import layout  # noqa: E402

STOPS = 64


def looks_unusable(data, samples=240):
    """True if the captured wallpaper is clearly not a wallpaper: every sampled pixel the same *light* colour, which is what an
    unpainted window looks like (white, or the light grey of a GTK window). Blending that in would paint a pale band next to
    the sidebar, so it is better to draw no cover at all. A solid dark wallpaper is fine."""
    if not data or len(data) < 4:
        return True
    step = max(4, (len(data) // 4 // samples) * 4)
    seen = {bytes(data[i:i + 3]) for i in range(0, len(data) - 3, step)}
    if len(seen) != 1:
        return False                                  # real wallpapers vary at least a little
    b, g, r = next(iter(seen))
    return 0.299 * r + 0.587 * g + 0.114 * b > 170


class EdgeFade:
    def __init__(self, xwm, width, fade=0, exclude=None):
        self.xwm = xwm
        self.width = width                  # px of solid gap before the sidebar; 0 = off
        self.fade = fade                    # px of soft edge left of the gap
        self.exclude = exclude if exclude is not None else set()   # window ids sdesk must ignore (shared with the app)
        self.win = None
        self.sidebar = None
        self.zone = None
        self._surface = self._data = None

    # ---------------------------------------------------------------- mode
    @staticmethod
    def compositor_running():
        screen = Gdk.Screen.get_default()
        return bool(screen and screen.is_composited() and screen.get_rgba_visual())

    @property
    def active(self):
        """True when the cover can be drawn at all (a width is set and a compositor runs)."""
        return self.width > 0 and self.compositor_running()

    # -------------------------------------------------------------- strip
    def update(self, sidebar):
        """The sidebar's rectangle (or None) is known/changed: place, capture and show the strip, or hide it."""
        self.sidebar = sidebar
        self.zone = layout.cover_zone(sidebar, self.width + self.fade, self.xwm.screen_size()[0]) if self.active else None
        if self.zone is None:
            self._hide()
            return
        z = self.zone
        self._ensure_window()
        self.win.set_size_request(z.w, z.h)                    # a non-resizable window takes its size from the request
        self.win.set_default_size(z.w, z.h)
        self.win.move(z.x, z.y)
        self.refresh()
        if not self.win.get_visible():
            self.win.show_all()
        self.win.get_window().input_shape_combine_region(cairo.Region(cairo.RectangleInt(0, 0, 0, 0)), 0, 0)  # click-through
        self.win.get_window().set_keep_above(False)

    def refresh(self):
        """(Re)read the wallpaper under the strip; call when the wallpaper changed."""
        z = self.zone
        if z is None or self.win is None:
            return False
        data = self.xwm.desktop_image(z.x, z.y, z.w, z.h)
        if data is None or looks_unusable(data):                  # cannot see the wallpaper: better no fade than a wrong one
            self._surface = self._data = None
            self._hide()
            return False
        self._data = bytearray(data)
        self._surface = cairo.ImageSurface.create_for_data(self._data, cairo.FORMAT_RGB24, z.w, z.h, z.w * 4)
        self.win.queue_draw()
        return False

    def _ensure_window(self):
        if self.win is not None:
            return
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("sdesk-edge-fade")
        win.set_decorated(False)
        win.set_type_hint(Gdk.WindowTypeHint.DOCK)               # above normal windows, below always-on-top ones (the sidebar)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_accept_focus(False)
        win.set_resizable(False)
        win.stick()
        win.set_app_paintable(True)
        win.set_visual(win.get_screen().get_rgba_visual())
        win.connect("draw", self._draw)
        self.win = win
        win.realize()
        self.exclude.add(win.get_window().get_xid())              # never mistake it for a sidebar or a window to tile

    def _draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        if self._surface is None or self.zone is None:
            return False
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_source_surface(self._surface, 0, 0)
        if self.fade <= 0:
            cr.paint()
            return False
        grad = cairo.LinearGradient(0, 0, self.fade, 0)
        for i in range(STOPS + 1):
            t = i / STOPS
            grad.add_color_stop_rgba(t, 0, 0, 0, layout.fade_alpha(t))
        cr.save()
        cr.rectangle(0, 0, self.fade, self.zone.h)
        cr.clip()
        cr.mask(grad)                                                # the soft edge
        cr.restore()
        cr.rectangle(self.fade, 0, self.zone.w - self.fade, self.zone.h)
        cr.set_source_surface(self._surface, 0, 0)
        cr.fill()                                                    # the solid rest: gap and sidebar area
        return False

    def keep_stacked(self):
        """Nothing to do: kwin's dock layer keeps the cover between the windows and the sidebar. Kept so callers need not
        know; it only makes sure the cover is not left visible when there is no longer any sidebar."""
        if self.win is not None and self.win.get_visible() and self.zone is None:
            self._hide()

    def _hide(self):
        if self.win is not None and self.win.get_visible():
            self.win.hide()

    def shutdown(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None
