"""GTK3 taskbar (one button per window, in strip order) and the drop-zone overlay."""
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
import math  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402

import cairo  # noqa: E402
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import audio, battery, chrometabs, favicons, toggl  # noqa: E402
from .baranim import Row  # noqa: E402
from .menu import shorten, window_label  # noqa: E402
from .shapes import rounded_rects  # noqa: E402
from . import theme as themes  # noqa: E402

# All colours come from the current theme (sdesk/theme.py, issue #22): T.accent / T.accent_edge were ACCENT /
# ACCENT_EDGE, and T.text(a), T.shade(a), T.tint(a), T.line(a) were white or black with alpha ``a``.
DND_TARGET = "sdesk/window"

CSS = b"window#sdesk-bar { background: transparent; }"


class DropOverlay(Gtk.Window):
    """Click-through frame that outlines the slot a dragged window will land in."""

    BORDER = 5

    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_accept_focus(False)
        self.set_keep_above(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self._composited = bool(visual and screen.is_composited())
        if self._composited:
            self.set_visual(visual)
        self.connect("draw", self._draw)
        self.connect("realize", lambda w: w.get_window().input_shape_combine_region(
            cairo.Region(cairo.RectangleInt(0, 0, 0, 0)), 0, 0))
        self._size = (0, 0)

    def show_rect(self, rect):
        if rect is None:
            self.hide()
            return
        if (rect.w, rect.h) != self._size:
            self._size = (rect.w, rect.h)
            self.resize(rect.w, rect.h)
            self._apply_shape(rect.w, rect.h)
        self.move(rect.x, rect.y)
        if not self.get_visible():
            self.show_all()

    def _apply_shape(self, w, h):
        if self._composited or not self.get_realized():
            return
        # no compositor: cut the middle out so the frame never hides the content
        b = self.BORDER
        region = cairo.Region(cairo.RectangleInt(0, 0, w, h))
        region.subtract(cairo.Region(cairo.RectangleInt(b, b, max(0, w - 2 * b), max(0, h - 2 * b))))
        self.get_window().shape_combine_region(region, 0, 0)

    def do_realize(self):
        Gtk.Window.do_realize(self)
        self._apply_shape(*self._size)

    def _draw(self, widget, cr):
        w, h = widget.get_allocated_width(), widget.get_allocated_height()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        b = self.BORDER
        r = 8
        T = themes.current()
        self._round(cr, b / 2, b / 2, w - b, h - b, r)
        if self._composited:
            cr.set_source_rgba(*T.accent, 0.16)
            cr.fill_preserve()
        else:
            cr.set_source_rgba(*T.accent, 1)
            cr.fill_preserve()
        cr.set_source_rgba(*T.accent, 0.95)
        cr.set_line_width(b)
        cr.stroke()
        return True

    @staticmethod
    def _round(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()


def blur(pix, strength=12):
    """Cheap blur: shrink hard, enlarge smoothly. Good enough for frosted glass."""
    w, h = pix.get_width(), pix.get_height()
    small = pix.scale_simple(max(2, w // strength), max(2, h // strength), GdkPixbuf.InterpType.BILINEAR)
    small = small.scale_simple(max(2, w // (strength * 2)), max(2, h // (strength * 2)), GdkPixbuf.InterpType.BILINEAR)
    return small.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _a(rgba, k):
    """``rgba`` with its alpha times ``k`` (a fade)."""
    return (rgba[0], rgba[1], rgba[2], rgba[3] * k)


class Item:
    """What the bar needs to know to draw one box."""

    def __init__(self, key, wid=None, kind=None):
        self.key, self.wid = key, wid
        self.kind = kind or ("add" if key == "add" else "window")      # window | add | clock | toggl | battery | volume | sound
        self.title = self.wm_class = ""
        self.tip = ""                                # the full window title: shown as the tooltip
        self.tabs = None                             # a Chrome window's tabs [{id, title, full, url, active, icon}] when known
        self.tab_more = 0                            # tabs left out of that list ("+ N")
        self.seg_w = []                              # natural width of each tab button (and the "+ N" one)
        self.chrome_window = None                    # Chrome's own id of the window
        self.since = None                            # wall-clock time it was minimised, or None
        self.shake_t0 = None                         # monotonic start of a "you forgot me" shake, or None
        self.shake = self.nag = 0.0                  # its current sideways offset (px) and how strongly it flashes (0..1)
        self.thread = None                           # "claude" / "t3": the program whose thread badges this box carries
        self.icon = None
        self.logo = None                             # the program's own icon (Chrome's logo): what a tab without icon shows
        self.active = self.hidden = False
        self.act = self.hov = self.dim = 0.0        # eased 0..1 versions of active / hovered / minimised


class Taskbar(Gtk.Window):
    """A row of separate glass boxes drawn by hand in one drawing area.

    Everything is animated (see baranim.Row): widths ease, a gap opens where a dragged box will land, hover/active colours
    fade. The window itself never moves or resizes: it spans the screen at a fixed place and is clipped (X shape) to the
    row, so what it has painted always lines up with the wallpaper copy behind it.

    Two looks, chosen at start (and whenever you switch Deepin's window effects on or off):
    * no compositor: nothing can be see-through, so the glass is painted over a copy of the wallpaper;
    * compositor: the window has an alpha channel, the gaps are really transparent, the boxes are translucent and kwin's
      blur effect frosts what is behind them (``_KDE_NET_WM_BLUR_BEHIND_REGION``). The alpha channel also matters because
      Deepin's compositor outlines every *opaque* window, which showed as full-width lines above and below the bar.
    """
    RADIUS = 6                  # corner radius of every box
    SLOT_PAD = 6                # space between two boxes is 2 x this
    PAD_X = 12                  # inner padding of a box
    ICON = 24
    ICON_GAP = 8
    TAB_PAD = 9                 # inside a tab button, left and right
    TAB_ICON = 18
    TAB_GAP = 6
    TAB_TEXT_MAX = 108          # px: fifteen characters or so
    MORE_W = 42                 # the "+ N" button
    ADD_W = 36                  # width of the "+" box
    SLIDER_W = 130              # the volume slider (knob room included)
    SLIDER_INSET = 10           # the track starts this far inside its slot, so the knob never gets clipped
    TILT = -4                   # degrees: the drag ghost
    DOT = 16                    # px: a thread badge (wider for two digits)
    DOT_GAP = 5
    DOT_TOKENS = {"waiting": "warn", "done": "ok", "busy": "info"}     # theme tokens of the thread badges
    DOT_NAMES = {"waiting": "waiting for you", "done": "done", "busy": "working"}

    BAT_ICON = 18               # px: the mouse / keyboard outline of a battery
    BAT_GAP = 5                 # icon to number
    BAT_SEP = 14                # between two devices
    BAT_BOLT = 10               # the charging bolt (plus 3 px after it)
    BAT_SIDE = 7                # the small L / R
    BAT_KEYS = ("mouse", "left", "right")

    @staticmethod
    def _bat_color(st):
        T = themes.current()
        return {"normal": T.text(0.82), "low": (*T.warn, 1.0), "charging": (*T.accent_edge, 1.0),
                "off": T.text(0.30)}[st]

    def __init__(self, controller, xwm, height=52, position="bottom", gap=8, sound=True, nag_after=1800, nag_every=300,
                 battery=False, toggl=False):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.c = controller
        self.xwm = xwm
        self.height = height
        self.position = position
        self.gap = gap                  # floats this far off the screen edge
        self.sw, self.sh = xwm.screen_size()
        self.y = self.sh - height - gap if position == "bottom" else gap
        self._band = None               # blurred wallpaper behind the bar (what the glass shows)
        self._band_raw = None           # the same, sharp: painted between the boxes so the bar looks transparent there
        self._band_y = 0
        self._icons = {}
        self.items = {"add": Item("add"), "clock": Item("clock", kind="clock")}
        self.items["clock"].title = time.strftime("%H:%M")
        self.heads = ["clock"]
        self.toggl = None                             # toggl.State, or None while nothing was read yet
        self.toggl_open = False                       # its card is open: the item is lit
        self.toggl_hours = None                       # toggl.WorkHours: when "Start timer" shakes if no timer runs
        self.toggl_target_h = 8.0                     # today's target in hours (0 = none)
        self.toggl_nag_every = 600                    # seconds between two shakes
        self.toggl_hotkeys = ""                       # shown in the tooltip ("Super+T")
        self._toggl_last_nag = 0.0
        self._toggl_sig = None
        self.on_toggl_click = lambda x, y: None       # left click: open the card above (x, y)
        self.on_toggl_stop = lambda: None             # middle click while a timer runs
        if toggl:
            self.items["toggl"] = Item("toggl", kind="toggl")
            self.heads.append("toggl")
        self.batteries = {}                           # key (mouse | left | right) -> battery.Reading
        if battery:
            self.items["battery"] = Item("battery", kind="battery")
            self.heads.append("battery")
        if sound:
            self.items["volume"] = Item("volume", kind="volume")
            self.items["sound"] = Item("sound", kind="sound")
            self.items["sound"].title = "Sound"
            self.heads += ["volume", "sound"]
        self.threads = {}                             # source -> {"waiting": [names], "done": [...], "busy": [...]}
        self.on_thread_seen = lambda source: None     # a box with thread badges was clicked: its done threads count as seen
        self.volume, self.muted = 0, False            # what the slider shows (percent)
        self._vol_drag = False
        self.on_volume = lambda percent: None         # slider moved: set the volume
        self.on_mute = lambda: None                   # middle click: mute / unmute
        self.sound_tip = ""
        self._clock_timer = 0
        self._spin_timer = 0                           # redraws the bar while a thread badge shows "working"
        self.nag_after, self.nag_every = nag_after, nag_every     # seconds minimised before it shakes / seconds between shakes
        self._last_nag = time.monotonic()
        self._nag_timer = 0
        self.favicons = favicons.Chrome()
        self._favicon_pix = {}
        self.tab_source = None                                    # a chrometabs.TabServer: the tabs Chrome windows have
        self._chrome_map = {}                                     # X window id -> Chrome's window id, once matched
        self._tab_pix = {}
        self._hover_seg = None                                    # (window key, tab button index) under the pointer
        self.on_tab_click = lambda wid, window_id, tab: None      # a tab button of another tab was clicked
        self.row = Row(tail=("add",), head=self.heads)
        self._timer = 0
        self._shape = None
        self.center_x = self.sw / 2         # the row is centred here (the middle of the usable area, not of the screen)
        self._hover = None
        self._press = None              # (key, x, y, button) of the mouse press that may become a click or a drag
        self._drag = None               # {"key", "wid", "origin", "grab": (x, y)} while dragging a box
        self._leave_timer = 0
        self._ghost_da = None           # the drawing area inside the drag icon window
        self._pending_refresh = False
        self._strut_done = False
        self.on_menu = lambda idx, x, y: None          # "add a window at idx", anchored above (x, y)
        self.on_window_menu = lambda wid, x, y: None   # right-click on a window's box
        self.on_sound_click = lambda: None             # click on the sound box: next output
        self.on_sound_menu = lambda x, y: None         # right-click on it: choose an output

        self.set_name("sdesk-bar")
        self.set_title("sdesk-taskbar")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.stick()
        self.set_app_paintable(True)
        screen = self.get_screen()
        self.composited = bool(screen.is_composited() and screen.get_rgba_visual())
        if self.composited:
            self.set_visual(screen.get_rgba_visual())
        self._blur_sig = None
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.sw, height)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
                             | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.area.connect("scroll-event", self._on_scroll)
        self.area.set_has_tooltip(True)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._on_press)
        self.area.connect("button-release-event", self._on_release)
        self.area.connect("motion-notify-event", self._on_motion_notify)
        self.area.connect("leave-notify-event", self._on_leave_notify)
        self.area.connect("query-tooltip", self._on_tooltip)
        targets = [Gtk.TargetEntry.new(DND_TARGET, Gtk.TargetFlags.SAME_APP, 0)]
        self._targets = Gtk.TargetList.new(targets)
        self.area.drag_dest_set(0, targets, Gdk.DragAction.MOVE)
        self.area.connect("drag-begin", self._on_drag_begin)
        self.area.connect("drag-data-get", lambda *a: None)
        self.area.connect("drag-end", self._on_drag_end)
        self.area.connect("drag-motion", self._on_drag_motion)
        self.area.connect("drag-leave", self._on_drag_leave)
        self.area.connect("drag-drop", self._on_drag_drop)
        self.add(self.area)
        keys = [*self.heads, "add"]
        self.row.set_items(keys, {k: self._slot_width(self.items[k]) for k in keys})   # needs self.area for text measuring
        self._clock_timer = GLib.timeout_add_seconds(1, self._clock_tick)
        if self.nag_after > 0:
            self._nag_timer = GLib.timeout_add_seconds(5, self._nag_check)
        self.connect("realize", self._on_realize)
        self.connect("map", lambda *_: GLib.idle_add(self._apply_shape, True))

    def shutdown(self):
        """Stop animating and destroy the window (used when the bar is rebuilt for another rendering mode)."""
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._clock_timer:
            GLib.source_remove(self._clock_timer)
            self._clock_timer = 0
        if self._spin_timer:
            GLib.source_remove(self._spin_timer)
            self._spin_timer = 0
        if self._nag_timer:
            GLib.source_remove(self._nag_timer)
            self._nag_timer = 0
        self._cancel_leave()
        self.destroy()

    # ------------------------------------------------------------ placement
    def _on_realize(self, *_):
        self.place()
        self.xwm.blank(self.xid())              # until the row is measured nothing of the full-width window may show

    def place(self):
        self.set_size_request(self.sw, self.height)
        self.move(0, self.y)

    def xid(self):
        return self.get_window().get_xid() if self.get_realized() else None

    def reserve_space(self):
        """Tell the WM to keep other windows out of the bar's strip. Constant, so it never makes kwin re-layout."""
        if self._strut_done or not self.get_mapped():
            return
        self._strut_done = True
        self.xwm.set_strut(self.xid(), self.position, self.height + self.gap, 0, self.sw - 1)

    def _head_width(self, closed=False):
        """Width of the clock / sound boxes (current, or natural when ``closed``)."""
        src = self.row.nat if closed else self.row.cur
        return sum(src.get(k, 0.0) for k in self.row.head if k in self.row.order)

    def _origin_x(self, total=None):
        """Screen x where the row starts. The window boxes (and "+") are centred on ``center_x``; the clock and sound boxes
        hang off their left, so they never push the window icons around."""
        total = self.row.total() if total is None else total
        head = self._head_width()
        return self.center_x - (total - head) / 2 - head

    def set_center(self, x):
        """Centre the row on screen x (the middle of the area the windows use, which a sidebar may make off-centre)."""
        x = float(x)
        if abs(x - self.center_x) > 0.5:
            self.center_x = x
            self._apply_shape(True)
            self.area.queue_draw()

    def _apply_shape(self, force=False):
        """Clip the window (and its clicks) to the row. The window never moves; only this rectangle follows the row."""
        if not self.get_mapped():
            return False
        total = self.row.total()
        rect = (int(self._origin_x(total)), 0, int(math.ceil(total)), self.height)
        if force or rect != self._shape:
            self._shape = rect
            self.xwm.set_shape(self.xid(), *rect)
        if self.composited:
            self._apply_blur(force)
        return False

    def _apply_blur(self, force=False):
        """Compositor mode: tell kwin to blur what is behind each box (rounded like the box)."""
        rects = []
        for key, (x, w) in self.row.positions().items():
            if key not in self.items or self.items[key].kind in ("clock", "toggl", "battery", "sound", "volume"):
                continue                                              # no box there: nothing to frost
            bx, by, bw, bh = self._box_rect(key, x, w)
            if bw >= 6:
                rects += rounded_rects(round(bx), round(by), round(bw), round(bh), self.RADIUS)
        sig = tuple(rects)
        if force or sig != self._blur_sig:
            self._blur_sig = sig
            self.xwm.set_blur_region(self.xid(), rects)

    # ----------------------------------------------------------------- items
    def _theme_icon(self, theme, name):
        """A theme icon at exactly ICON x ICON: without FORCE_SIZE a theme may hand back its 256px or 512px picture, which
        then spilled over the title next to it."""
        pix = theme.load_icon(name, self.ICON, Gtk.IconLookupFlags.FORCE_SIZE)
        if pix is not None and (pix.get_width() != self.ICON or pix.get_height() != self.ICON):
            pix = pix.scale_simple(self.ICON, self.ICON, GdkPixbuf.InterpType.BILINEAR)
        return pix

    def _favicon(self, window_title):
        """The icon of the page a Chrome window shows (a GdkPixbuf at ICON size), or None to keep Chrome's own logo."""
        png = self.favicons.icon_png(window_title)
        if not png:
            return None
        if png not in self._favicon_pix:
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(png)
                loader.close()
                pix = loader.get_pixbuf()
                if pix is not None and (pix.get_width() != self.ICON or pix.get_height() != self.ICON):
                    pix = pix.scale_simple(self.ICON, self.ICON, GdkPixbuf.InterpType.BILINEAR)
            except GLib.Error:
                pix = None
            if len(self._favicon_pix) > 64:
                self._favicon_pix.clear()
            self._favicon_pix[png] = pix
        return self._favicon_pix[png]

    SHAKE_S = 0.8               # how long the "you forgot me" shake lasts
    SHAKE_PX = 4.0              # how far it swings (less than the space around a box, so nothing is clipped)

    def _nag_check(self):
        """Every few seconds: if it is time, shake every box that has been minimised for too long."""
        now = time.monotonic()
        if now - self._last_nag < self.nag_every:
            return True
        wall = time.time()
        due = [i for i in self.items.values()
               if i.kind == "window" and i.hidden and i.since is not None and wall - i.since >= self.nag_after]
        if due:
            self._last_nag = now
            for item in due:
                item.shake_t0 = now
            self._kick()
        return True

    def _icon_for(self, wid, wm_class):
        key = (wid, wm_class)
        if key in self._icons:
            return self._icons[key]
        pix = None
        icon = self.xwm.icon_argb(wid)
        if icon:
            w, h, argb = icon
            raw = bytearray()
            for p in argb:
                raw += bytes(((p >> 16) & 255, (p >> 8) & 255, p & 255, (p >> 24) & 255))
            pix = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(raw)), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
            pix = pix.scale_simple(self.ICON, self.ICON, GdkPixbuf.InterpType.BILINEAR)
        else:
            theme = Gtk.IconTheme.get_default()
            for name in (wm_class, wm_class.lower(), wm_class.split(".")[-1].lower(), "application-x-executable"):
                if name and theme.has_icon(name):
                    pix = self._theme_icon(theme, name)
                    break
        self._icons[key] = pix
        return pix

    def _layout(self, item, width=None):
        """Pango layout of an item's title. One font for every state: bold/italic would change the width, and an item
        must never change size because it became active or was minimised (those are shown by colour and dimming only)."""
        layout = self.area.create_pango_layout(item.title or item.wm_class or "window")
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(-1 if width is None else int(max(0, width) * Pango.SCALE))
        return layout

    def _text_cap(self):
        n = max(1, sum(1 for i in self.items.values() if i.kind == "window"))
        return max(70, min(190, int((self.sw - 400) / n) - 68))

    def _slot_width(self, item):
        """Natural width of an item's slot (its box plus the space around it)."""
        if item.key == "add":
            return self.ADD_W + 2 * self.SLOT_PAD
        if item.kind == "clock":                                           # fixed: the time ticking never moves anything
            probe = self._layout(item)
            probe.set_text("00:00", -1)
            return self.PAD_X * 2 + probe.get_pixel_size()[0] + 2 * self.SLOT_PAD
        if item.kind == "volume":
            return self.SLIDER_W + 2 * self.SLOT_PAD
        if item.kind == "toggl":
            return self._toggl_width() + 2 * self.SLOT_PAD
        if item.kind == "battery":
            return self.PAD_X * 2 + sum(self._bat_width(k) for k in self.BAT_KEYS) + self.BAT_SEP * 2 + 2 * self.SLOT_PAD
        if item.kind == "sound":
            text_w = min(self._layout(item).get_pixel_size()[0], 170)
            return self.PAD_X + self.ICON + self.ICON_GAP + text_w + self.PAD_X + 2 * self.SLOT_PAD
        if item.tabs:                                                      # a Chrome window: one button per tab
            item.seg_w = [self.TAB_PAD * 2 + self.TAB_ICON + self.TAB_GAP
                          + min(self._tab_layout(t["title"]).get_pixel_size()[0], self.TAB_TEXT_MAX) for t in item.tabs]
            if item.tab_more:
                item.seg_w.append(self.MORE_W)
            return sum(item.seg_w) + 2 * self.SLOT_PAD
        text_w = min(self._layout(item).get_pixel_size()[0], self._text_cap())
        return self.PAD_X + self.ICON + self.ICON_GAP + text_w + self._badges_width(item) + self.PAD_X + 2 * self.SLOT_PAD

    def _tab_layout(self, text, width=None):
        layout = self.area.create_pango_layout(text)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(-1 if width is None else int(max(0, width) * Pango.SCALE))
        return layout

    def _scaled(self, pix, size):
        if pix is None or (pix.get_width() == size and pix.get_height() == size):
            return pix
        return pix.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)

    def _tab_icon(self, url, fallback):
        """The page's icon at tab size: the one Chrome sent through the extension, else the one in Chrome's profile, else
        Chrome's own logo (never another tab's icon)."""
        png = (self.tab_source.icon_png(url) if self.tab_source is not None else None) or self.favicons.icon_png_for_url(url)
        if png:
            if png not in self._tab_pix:
                try:
                    loader = GdkPixbuf.PixbufLoader()
                    loader.write(png)
                    loader.close()
                    self._tab_pix[png] = self._scaled(loader.get_pixbuf(), self.TAB_ICON)
                except GLib.Error:
                    self._tab_pix[png] = None
                if len(self._tab_pix) > 128:
                    self._tab_pix.clear()
            if self._tab_pix.get(png) is not None:
                return self._tab_pix[png]
        return self._scaled(fallback, self.TAB_ICON)

    def _apply_tabs(self, rows):
        """Give every Chrome window the tabs the extension reported for it (or None: then it is one plain box)."""
        windows = self.tab_source.snapshot() if self.tab_source is not None else []
        chromes = {r["wid"]: r for r in rows if favicons.is_chrome(r["wm_class"])}
        matched = chrometabs.match_windows({w: r["title"] for w, r in chromes.items()}, windows, self._chrome_map,
                                           x_active=self.c.b.active()) if windows and chromes else {}
        self._chrome_map = {wid: w["id"] for wid, w in matched.items()}
        for r in rows:
            item = self.items[r["wid"]]
            w = matched.get(r["wid"])
            if w is None or not w.get("tabs"):
                item.tabs, item.tab_more, item.chrome_window = None, 0, None
                continue
            shown, item.tab_more = chrometabs.visible_tabs(w["tabs"])
            item.chrome_window = w["id"]
            item.tabs = [{"id": t.get("id"), "title": shorten(t.get("title") or t.get("url") or "…"),
                          "full": t.get("title") or t.get("url") or "", "url": t.get("url") or "",
                          "active": bool(t.get("active")), "icon": self._tab_icon(t.get("url"), item.logo)} for t in shown]

    def refresh(self):
        """Take the controller's current windows, in order, and let the row animate to match."""
        if self._drag is not None:             # never rebuild under an active drag
            self._pending_refresh = True
            return
        rows = self.c.snapshot()
        keys = []
        for r in rows:
            wid = r["wid"]
            item = self.items.get(wid)
            if item is None:
                item = self.items[wid] = Item(wid, wid)
                item.dim = 1.0 if r["hidden"] else 0.0
            item.title = window_label(r["wm_class"], r["title"]) if r["wm_class"] else (r["title"] or "window")
            item.wm_class = r["wm_class"]
            item.since = (time.time() - r["hidden_for"]) if r["hidden"] else None
            item.tip = r["title"] or ""
            item.active, item.hidden = bool(r["active"] and not r["hidden"]), bool(r["hidden"])
            item.thread = self.thread_source(r["wm_class"])
            item.logo = self._icon_for(wid, r["wm_class"])
            item.icon = (self._favicon(r["title"]) if favicons.is_chrome(r["wm_class"]) else None) or item.logo
            keys.append(wid)
        self._apply_tabs(rows)
        keys = [*self.heads, *keys, "add"]
        self.row.set_items(keys, {k: self._slot_width(self.items[k]) for k in keys})
        self._kick()

    def _kick(self):
        if not self._timer:
            self._timer = GLib.timeout_add(16, self._tick)

    def _tick(self):
        moving, gone = self.row.step()
        for key in gone:
            self.items.pop(key, None)
        busy = moving
        now = time.monotonic()
        for item in self.items.values():
            if item.shake_t0 is not None:
                t = (now - item.shake_t0) / self.SHAKE_S
                if t >= 1.0:
                    item.shake_t0, item.shake, item.nag = None, 0.0, 0.0
                else:
                    decay = 1.0 - t
                    item.shake = self.SHAKE_PX * math.sin(2 * math.pi * 5 * t) * decay      # five swings, dying out
                    item.nag = decay                                                        # and the box lights up meanwhile
                    busy = True
        for key, item in self.items.items():
            for attr, target, rate in (("act", 1.0 if item.active else 0.0, 0.22),
                                       ("hov", 1.0 if key == self._hover and self._drag is None else 0.0, 0.30),
                                       ("dim", 1.0 if item.hidden else 0.0, 0.22)):
                v = getattr(item, attr)
                if abs(target - v) < 0.01:
                    setattr(item, attr, target)
                else:
                    setattr(item, attr, v + (target - v) * rate)
                    busy = True
        self._apply_shape()
        self.area.queue_draw()
        if not busy:
            self._timer = 0
            return False
        return True

    # ----------------------------------------------------------- clock, sound
    def _clock_tick(self):
        now = time.strftime("%H:%M")
        item = self.items.get("clock")
        if item is not None and item.title != now:
            item.title = now
            self.area.queue_draw()
        if "toggl" in self.items and self.toggl is not None:
            if self.toggl.running:
                self.area.queue_draw()                                    # the running time ticks
            self._toggl_nag()
        return True

    SOUND_ICONS = {"headset": ("audio-headphones", "audio-headset", "audio-volume-high"),
                   "display": ("video-display", "audio-card", "audio-volume-high"),
                   "speakers": ("audio-speakers", "audio-card", "audio-volume-high")}

    def set_sound(self, sink):
        """Show the current output (an audio.Sink) in the sound box; None = no output known."""
        item = self.items.get("sound")
        if item is None:
            return
        if sink is None:
            item.title, item.icon, self.sound_tip = "No output", None, "No sound output found"
            self.volume, self.muted = 0, False
        else:
            if not self._vol_drag:                                          # while dragging the knob, the knob wins
                self.volume, self.muted = sink.volume, sink.muted
            theme = Gtk.IconTheme.get_default()
            item.icon = None
            for name in self.SOUND_ICONS.get(sink.kind, self.SOUND_ICONS["speakers"]):
                if theme.has_icon(name):
                    item.icon = self._theme_icon(theme, name)
                    break
            item.title = sink.label
            self.sound_tip = f"{sink.description}\nClick: next output, right-click: choose, scroll: volume"
        self.refresh()
        self.area.queue_draw()

    # --------------------------------------------------------------- battery
    def set_battery(self, readings):
        """Show new battery readings ({key: battery.Reading}); the box only grows or shrinks for a charging bolt."""
        if "battery" not in self.items:
            return
        self.batteries = dict(readings)
        self.refresh()
        self.area.queue_draw()

    def _bat_text(self, key):
        r = self.batteries.get(key)
        return f"{r.level}%" if r is not None and r.available else "–"

    def _bat_num_w(self):
        """Every number gets the room of "100%": a level going from 85 to 100 never moves the row."""
        probe = self._layout(self.items["battery"])
        probe.set_text("100%", -1)
        return probe.get_pixel_size()[0]

    def _bat_width(self, key):
        bolt = self.BAT_BOLT + 3 if battery.state(self.batteries.get(key)) == "charging" else 0
        side = self.BAT_SIDE if key != "mouse" else 0                     # the small L / R next to the keyboard
        return self.BAT_ICON + side + self.BAT_GAP + bolt + self._bat_num_w()

    def _bat_path(self, cr, key, x, cy):
        """Outline of the device (18 x 18 px, as in the mockup): a mouse, or a keyboard half."""
        y = cy - self.BAT_ICON / 2
        if key == "mouse":
            DropOverlay._round(cr, x + 5, y + 2, 8, 14, 4)
            cr.move_to(x + 9, y + 2)
            cr.line_to(x + 9, y + 7)
        else:
            DropOverlay._round(cr, x + 2, y + 4, 14, 10, 2)
            for dx in (5, 8, 11):
                cr.move_to(x + dx, y + 7.5)
                cr.line_to(x + dx + 0.01, y + 7.5)
            cr.move_to(x + 5, y + 10.5)
            cr.line_to(x + 13, y + 10.5)

    def _paint_battery(self, cr, bx, by, bw, bh, fade):
        """Per device: its outline, (a small L / R,) a bolt while charging, and the percentage. No box, like the clock.
        Painted twice: a dark copy 1 px lower first, as the shadow that keeps it legible on any wallpaper."""
        cy = by + bh / 2
        small = self.area.create_pango_layout("")
        small.set_font_description(Pango.FontDescription("Sans Bold 7"))
        for shadow in (True, False):
            x = bx + self.PAD_X
            for key in self.BAT_KEYS:
                st = battery.state(self.batteries.get(key))
                rgba = themes.current().shade(0.55) if shadow else self._bat_color(st)
                cr.set_source_rgba(rgba[0], rgba[1], rgba[2], rgba[3] * fade)
                dy = 1 if shadow else 0
                cr.new_path()
                self._bat_path(cr, key, x, cy + dy)
                cr.set_line_width(1.5)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.stroke()
                tx = x + self.BAT_ICON
                if key != "mouse":
                    small.set_text("L" if key == "left" else "R", -1)
                    cr.move_to(tx, cy - self.BAT_ICON / 2 - 1 + dy)            # superscript, right of the icon
                    PangoCairo.show_layout(cr, small)
                    tx += self.BAT_SIDE
                tx += self.BAT_GAP
                if st == "charging":
                    sx, sy = tx, cy - 7 + dy                                # the bolt: M6 0 1 8h3l-1 6 5-8H5z in 10 x 14
                    cr.move_to(sx + 6, sy)
                    for px, py in ((1, 8), (4, 8), (3, 14), (8, 6), (5, 6)):
                        cr.line_to(sx + px, sy + py)
                    cr.close_path()
                    cr.fill()
                    tx += self.BAT_BOLT + 3
                num = self._layout(self.items["battery"])
                num.set_text(self._bat_text(key), -1)
                _lw, lh = num.get_pixel_size()
                cr.move_to(tx, cy - lh / 2 + dy)
                PangoCairo.show_layout(cr, num)
                x += self._bat_width(key) + self.BAT_SEP

    # ----------------------------------------------------------------- toggl
    TG_RING = 16                # px: the ring of today's total (with the play triangle / the running dot inside)
    TG_GAP = 8
    TG_DESC_MAX = 220           # px of the description in the bar (the rest is in the card and the tooltip)
    TG_PROJ_MAX = 130

    def set_toggl(self, state):
        """Show a new toggl.State. The row only re-measures when the text changes (not every second)."""
        if "toggl" not in self.items:
            return
        self.toggl = state
        sig = self._toggl_texts()[:3]
        if sig != self._toggl_sig:
            self._toggl_sig = sig
            self.refresh()
        self.area.queue_draw()

    def set_toggl_open(self, is_open):
        if "toggl" in self.items and is_open != self.toggl_open:
            self.toggl_open = is_open
            self.area.queue_draw()

    def toggl_anchor(self):
        """Screen point above the middle of the timer item: where its card opens."""
        pos = self.row.positions().get("toggl")
        if pos is None:
            return self.sw / 2, self.y
        bx, _, bw, _ = self._box_rect("toggl", *pos)
        return bx + bw / 2, self.y

    def _toggl_texts(self):
        """(main, dim, clock, look): look is idle | run | forgot | offline | loading."""
        s = self.toggl
        if s is None or (not s.known and not s.error):
            return "Toggl…", "", "", "loading"
        if not s.known:
            return "Toggl offline", "", "", "offline"
        r = s.running
        if not r:
            return "Start timer", "", "", "idle"
        clock = toggl.fmt_clock(toggl.elapsed(r))
        return (r.get("description") or "(no description)", toggl.project_label(r), clock,
                "forgot" if toggl.forgot(r) else "run")

    def _tg_layout(self, text, width=None, bold=False):
        layout = self.area.create_pango_layout(text)
        if bold:
            desc = self.area.get_pango_context().get_font_description().copy()
            desc.set_weight(Pango.Weight.BOLD)
            layout.set_font_description(desc)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_width(-1 if width is None else int(max(0, width) * Pango.SCALE))
        return layout

    def _tg_clock_w(self, clock):
        """Room for the running time: that of "0:00:00" (or "00:00:00"), so ticking never moves the row."""
        return self._tg_layout("0" * (len(clock) - 6) + "0:00:00", bold=True).get_pixel_size()[0] if clock else 0

    def _toggl_width(self):
        main, dim, clock, _look = self._toggl_texts()
        w = self.PAD_X + self.TG_RING + self.TG_GAP
        w += min(self._tg_layout(main).get_pixel_size()[0], self.TG_DESC_MAX)
        if dim:
            w += self.TG_GAP + min(self._tg_layout("· " + dim).get_pixel_size()[0], self.TG_PROJ_MAX)
        if clock:
            w += self.TG_GAP + self._tg_clock_w(clock)
        return w + self.PAD_X

    def _toggl_today(self):
        s = self.toggl
        return toggl.today_seconds(s.entries, s.running) if s is not None and s.known else 0

    def _toggl_nag(self):
        """Extra 4: during work hours without a timer, "Start timer" shakes (like a window minimised for too long)."""
        s = self.toggl
        if self.toggl_hours is None or s is None or s.error or self.toggl_open:
            return
        now = time.monotonic()
        if now - self._toggl_last_nag < self.toggl_nag_every:
            return
        if toggl.should_nag(s.running, self.toggl_hours, datetime.now(), online=s.known):
            self._toggl_last_nag = now
            self.items["toggl"].shake_t0 = now
            self._kick()

    def _paint_toggl(self, cr, bx, by, bw, bh, item, fade):
        """The timer, plain like the clock: a ring with today's total, then what runs and for how long. A lit box while
        its card is open, while it shakes, and red when the timer was probably forgotten."""
        main, dim, clock, look = self._toggl_texts()
        T = themes.current()
        lit = 1.0 if self.toggl_open else item.nag
        if look == "forgot" or lit > 0.01:
            DropOverlay._round(cr, bx, by, bw, bh, self.RADIUS)
            if look == "forgot":
                cr.set_source_rgba(*T.paint("danger_fill"), T.fill(0.42) * fade)
            else:
                cr.set_source_rgba(*T.accent, T.fill(0.62) * lit * fade)
            cr.fill()
            DropOverlay._round(cr, bx + 0.5, by + 0.5, bw - 1, bh - 1, self.RADIUS - 0.5)
            edge = T.paint("danger") if look == "forgot" else T.accent_edge
            cr.set_source_rgba(*edge, (0.9 if look == "forgot" else 0.95 * lit) * fade)
            cr.set_line_width(1)
            cr.stroke()
        cr.save()
        cr.rectangle(bx, by, bw, bh)
        cr.clip()
        light = (0.82 + 0.18 * max(item.hov, lit)) * fade
        cy = by + bh / 2
        x = bx + self.PAD_X
        self._paint_toggl_ring(cr, x + self.TG_RING / 2, cy, look, fade)
        x += self.TG_RING + self.TG_GAP
        quiet = look in ("idle", "offline", "loading") and lit < 0.5
        alpha = light * (0.75 if quiet else 1.0) * (0.6 if look in ("offline", "loading") else 1.0)
        fg = T.on if lit >= 0.5 and look != "forgot" else T.text          # on the lit (accent) box: the accent's text
        for text, cap, rgba, bold in ((main, self.TG_DESC_MAX, fg(alpha), False),
                                      (("· " + dim) if dim else "", self.TG_PROJ_MAX, _a(fg(0.62), light), False),
                                      (clock, None, (*T.forgot_text, light) if look == "forgot" else fg(light), True)):
            if not text:
                continue
            layout = self._tg_layout(text, cap, bold)
            lw, lh = layout.get_pixel_size()
            if cap is None:
                lw = self._tg_clock_w(clock)
            cr.set_source_rgba(*T.shade(0.55 * fade))
            cr.move_to(x, cy - lh / 2 + 1)
            PangoCairo.show_layout(cr, layout)
            cr.set_source_rgba(*rgba)
            cr.move_to(x, cy - lh / 2)
            PangoCairo.show_layout(cr, layout)
            x += lw + self.TG_GAP
        cr.restore()

    def _paint_toggl_ring(self, cr, cx, cy, look, fade):
        """Extra 6: a ring that fills with today's total against the target; a play sign (no timer) or a dot (running)
        inside. Orange while Second Brain does not answer."""
        r = self.TG_RING / 2 - 1.5
        frac = min(1.0, self._toggl_today() / (self.toggl_target_h * 3600)) if self.toggl_target_h else 0.0
        warn = self.toggl is not None and bool(self.toggl.error)
        T = themes.current()
        for shadow in (True, False):
            dy = 1 if shadow else 0
            cr.new_path()
            cr.arc(cx, cy + dy, r, 0, 2 * math.pi)
            cr.set_line_width(2.2)
            cr.set_source_rgba(*(T.shade(0.45 * fade) if shadow else _a(T.text(0.30), fade)))
            cr.stroke()
            if frac > 0 and not shadow:
                cr.new_path()
                cr.arc(cx, cy, r, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac)
                cr.set_source_rgba(*(T.warn if warn else T.accent_edge), 0.95 * fade)
                cr.stroke()
            col = T.shade(0.45 * fade) if shadow else (
                (*T.paint("danger"), fade) if look == "forgot" else (*T.warn, fade) if warn
                else (*T.accent_edge, fade) if look == "run" else _a(T.text(0.85), fade))
            cr.set_source_rgba(*col)
            if look in ("run", "forgot"):
                cr.arc(cx, cy + dy, 3.2, 0, 2 * math.pi)
            else:
                cr.move_to(cx - 2, cy - 3.5 + dy)
                cr.line_to(cx + 3.5, cy + dy)
                cr.line_to(cx - 2, cy + 3.5 + dy)
                cr.close_path()
            cr.fill()

    def _toggl_tip(self):
        s = self.toggl
        keys = self.toggl_hotkeys
        if s is None or (not s.known and not s.error):
            return "Toggl: reading Second Brain…"
        lines = []
        if s.error:
            lines.append(f"⚠ {s.error}" + (" (showing the last known state)" if s.known else ""))
        if s.known:
            r = s.running
            if r:
                secs = toggl.elapsed(r)
                if toggl.forgot(r):
                    lines.append(f"Running for {int(secs // 3600)} h: did you forget to stop it?")
                start = toggl.parse_time(r.get("start"))
                lines += [r.get("description") or "(no description)", toggl.project_label(r),
                          f"Started {time.strftime('%H:%M', time.localtime(start)) if start else '?'}, running {toggl.fmt_clock(secs)}"]
            else:
                lines.append("No timer running")
            target = f" of {toggl.fmt_hm(self.toggl_target_h * 3600)}" if self.toggl_target_h else ""
            lines.append(f"Today {toggl.fmt_hm(self._toggl_today())}{target}")
        act = "Click: open the card" + (", middle-click: stop" if s.running else "")
        lines.append(act + (f" · {keys}" if keys else ""))
        return "\n".join(lines)

    # ------------------------------------------------------------- hit tests
    def _box_rect(self, key, x, w):
        """Visible box of a slot, in window coordinates."""
        left = self._origin_x() + x + self.SLOT_PAD
        item = self.items.get(key)
        if item is not None:
            left += item.shake
        return left, self.SLOT_PAD, w - 2 * self.SLOT_PAD, self.height - 2 * self.SLOT_PAD

    def _item_at(self, px):
        for key, (x, w) in self.row.positions().items():
            if key in self.row.dying or w <= 2 * self.SLOT_PAD:
                continue
            bx, _, bw, _ = self._box_rect(key, x, w)
            if bx <= px < bx + bw:
                return key
        return None

    def _n_windows(self):
        return len(self.row._live())

    def _windows_before(self, px):
        """How many window boxes lie (by their centre) to the left of window x ``px``: the index for "add here"."""
        n = 0
        for key, (x, w) in self.row.positions().items():
            item = self.items.get(key)
            if item is None or item.kind != "window" or key in self.row.dying:
                continue
            bx, _, bw, _ = self._box_rect(key, x, w)
            if bx + bw / 2 < px:
                n += 1
        return n

    # --------------------------------------------------------------- drawing
    def _paint_slider(self, cr, bx, by, bw, bh, item, fade):
        """Horizontal volume slider: a thin track, the filled part, a round knob. Greyed out while muted."""
        left, width = bx + self.SLIDER_INSET, max(1.0, bw - 2 * self.SLIDER_INSET)
        cy = by + bh / 2
        frac = max(0.0, min(1.0, self.volume / 100.0))
        live = 0.45 if self.muted else 1.0
        track_h, r = 4.0, 6.0 + 1.0 * max(item.hov, 1.0 if self._vol_drag else 0.0)
        T = themes.current()
        DropOverlay._round(cr, left, cy - track_h / 2, width, track_h, track_h / 2)
        cr.set_source_rgba(*T.shade(0.30 * fade))                         # a hair of shadow under the track
        cr.save()
        cr.translate(0, 1)
        cr.fill_preserve()
        cr.restore()
        cr.set_source_rgba(*_a(T.line(0.28), fade))
        cr.fill()
        if frac > 0:
            DropOverlay._round(cr, left, cy - track_h / 2, max(track_h, width * frac), track_h, track_h / 2)
            col = T.accent_edge if not self.muted else T.muted
            cr.set_source_rgba(*col, 0.95 * fade * (0.6 + 0.4 * live))
            cr.fill()
        kx = left + width * frac
        cr.arc(kx, cy + 1, r, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.35 * fade)
        cr.fill()
        cr.arc(kx, cy, r, 0, 2 * math.pi)
        cr.set_source_rgba(*T.knob, (0.55 + 0.4 * live) * fade)
        cr.fill()

    def _slider_track(self):
        """(left, width) of the slider track in window coordinates, or None."""
        pos = self.row.positions().get("volume")
        if pos is None:
            return None
        bx, _, bw, _ = self._box_rect("volume", *pos)
        return bx + self.SLIDER_INSET, max(1.0, bw - 2 * self.SLIDER_INSET)

    def _set_volume_from_x(self, x):
        track = self._slider_track()
        if track is None:
            return
        self.volume, self.muted = audio.volume_from_x(x, *track), False
        self.on_volume(self.volume)
        self.area.queue_draw()

    def _on_scroll(self, _a, ev):
        key = self._item_at(ev.x)
        if key is None or self.items[key].kind not in ("volume", "sound"):
            return False
        if ev.direction == Gdk.ScrollDirection.SMOOTH:
            direction = -ev.delta_y
        else:
            direction = 1 if ev.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT) else -1
        if not direction:
            return True
        self.volume, self.muted = audio.step_volume(self.volume, direction), False
        self.on_volume(self.volume)
        self.area.queue_draw()
        return True

    # ----------------------------------------------------------- thread badges
    THREAD_CLASSES = {"com.anthropic.claude": "claude", "com.t3tools.t3code": "t3"}      # window class -> program

    def thread_source(self, wm_class):
        cls = (wm_class or "").lower()
        return next((src for key, src in self.THREAD_CLASSES.items() if key in cls), None)

    def set_threads(self, summary):
        """Show how many threads of each program wait for you / are done / work: {"claude": {"waiting": [names], ...}, ...}."""
        if summary == self.threads:
            return
        self.threads = summary
        if not self._spin_timer and self._any_busy():
            self._spin_timer = GLib.timeout_add(50, self._spin_tick)
        self.refresh()
        self.area.queue_draw()

    SPIN_S = 1.6                # seconds for one turn of the "working" light around a blue badge
    SPIN_ARC = 0.3              # the lit part: this fraction of the badge's outline

    def _any_busy(self):
        return any(d.get("busy") for d in self.threads.values())

    def _spin_tick(self):
        if not self._any_busy():
            self._spin_timer = 0
            return False
        self.area.queue_draw()
        return True

    def _dots(self, item):
        """[(state, count)] to draw in a box, in the order waiting, done, working."""
        d = self.threads.get(item.thread, {}) if item.thread else {}
        return [(st, len(d[st])) for st in ("waiting", "done", "busy") if d.get(st)]

    def _dot_layout(self, text):
        layout = self.area.create_pango_layout(text)
        layout.set_font_description(Pango.FontDescription("Sans Bold 8"))
        return layout

    def _dot_width(self, count):
        return max(self.DOT, self._dot_layout(str(min(count, 99))).get_pixel_size()[0] + 10)

    def _badges_width(self, item):
        """Room the badges take in a box (0 without any)."""
        return sum(self.DOT_GAP + self._dot_width(n) for _, n in self._dots(item))

    def _thread_tip(self, item):
        d = self.threads.get(item.thread, {}) if item.thread else {}
        lines = []
        for st in ("waiting", "done", "busy"):
            if d.get(st):
                names = d[st]
                lines.append(f"{len(names)} {self.DOT_NAMES[st]}: " + ", ".join(names[:5]) + (" …" if len(names) > 5 else ""))
        return ("\n" + "\n".join(lines)) if lines else ""

    def _paint_badges(self, cr, x, by, bh, item, fade):
        """The coloured count pills of a box, from ``x`` on, in the box's own style: a flat tint with a thin lighter edge."""
        for st, n in self._dots(item):
            w = self._dot_width(n)
            x += self.DOT_GAP
            r, y = self.DOT / 2, by + (bh - self.DOT) / 2
            DropOverlay._round(cr, x, y, w, self.DOT, r)
            red, green, blue = getattr(themes.current(), self.DOT_TOKENS[st])
            cr.set_source_rgba(red, green, blue, 0.9 * fade)
            cr.fill_preserve()
            cr.set_line_width(1)
            cr.set_source_rgba(1, 1, 1, 0.35 * fade)
            if st == "busy":                                  # a light runs round the edge: this one is working
                cr.stroke_preserve()
                length = 2 * (w - self.DOT) + math.pi * self.DOT
                cr.set_dash([length * self.SPIN_ARC, length * (1 - self.SPIN_ARC)],
                            -length * ((time.monotonic() / self.SPIN_S) % 1.0))
                cr.set_line_width(1.6)
                cr.set_source_rgba(1, 1, 1, 0.85 * fade)
                cr.stroke()
                cr.set_dash([])
            else:
                cr.stroke()
            num = self._dot_layout(str(min(n, 99)))
            nw, nh = num.get_pixel_size()
            cr.set_source_rgba(1, 1, 1, fade)
            cr.move_to(x + (w - nw) / 2, y + (self.DOT - nh) / 2)
            PangoCairo.show_layout(cr, num)
            x += w

    def _paint_plain(self, cr, bx, by, bw, bh, item):
        """The clock and the sound output: just text (and an icon), no background, no border."""
        nat_w = self.row.nat.get(item.key, bw + 2 * self.SLOT_PAD) - 2 * self.SLOT_PAD
        fade = max(0.0, min(1.0, (bw - 8) / max(1.0, nat_w - 8)))
        light = (0.82 + 0.18 * item.hov) * fade
        cr.save()
        cr.rectangle(bx, by, bw, bh)
        cr.clip()
        if item.kind == "volume":
            self._paint_slider(cr, bx, by, bw, bh, item, fade)
            cr.restore()
            return
        if item.kind == "battery":
            self._paint_battery(cr, bx, by, bw, bh, fade)
            cr.restore()
            return
        if item.kind == "toggl":
            cr.restore()
            self._paint_toggl(cr, bx, by, bw, bh, item, fade)
            return
        layout = self._layout(item)
        if item.kind == "clock":
            layout.set_text(item.title, -1)
            lw, lh = layout.get_pixel_size()
            tx, ty = bx + (bw - lw) / 2, by + (bh - lh) / 2
        else:
            if item.icon is not None:
                Gdk.cairo_set_source_pixbuf(cr, item.icon, bx + self.PAD_X, by + (bh - self.ICON) / 2)
                cr.paint_with_alpha(light)
            avail = bw - (self.PAD_X * 2 + self.ICON + self.ICON_GAP)
            layout = self._layout(item, max(0, min(avail, 170)))
            lw, lh = layout.get_pixel_size()
            tx, ty = bx + self.PAD_X + self.ICON + self.ICON_GAP, by + (bh - lh) / 2
        if item.kind == "clock" or avail > 4:
            T = themes.current()
            cr.set_source_rgba(*T.shade(0.55 * fade))                  # soft shadow keeps it legible on any wallpaper
            cr.move_to(tx, ty + 1)
            PangoCairo.show_layout(cr, layout)
            cr.set_source_rgba(*_a(T.text(0.82 + 0.18 * item.hov), fade))
            cr.move_to(tx, ty)
            PangoCairo.show_layout(cr, layout)
        cr.restore()

    def _paint_box(self, cr, bx, by, bw, bh, item, alpha=1.0, backdrop=True):
        """One glass box: backdrop, flat tint, thin edge, icon and title. Used for the bar and for the drag ghost
        (which paints its own backdrop first and passes ``backdrop=False``)."""
        if bw < 1:
            return
        if item.kind in ("clock", "toggl", "battery", "sound", "volume"):
            self._paint_plain(cr, bx, by, bw, bh, item)
            return
        px, py = 0, self.y
        T = themes.current()
        act, hov, dim = item.act, item.hov, item.dim * (1 - item.nag)        # a shaking box lights up fully
        full = alpha * (1 - 0.45 * dim)
        cr.save()
        if full < 0.999:
            cr.push_group()
        DropOverlay._round(cr, bx, by, bw, bh, self.RADIUS)
        cr.clip()
        if not backdrop:
            pass
        elif self.composited:
            cr.set_source_rgba(*T.bar)                             # glass; kwin blurs what is behind it
            cr.paint()
        elif self._band is not None:
            Gdk.cairo_set_source_pixbuf(cr, self._band, -px, self._band_y - py)
            cr.paint()
            cr.set_source_rgba(*T.bar_dim)                         # slight darkening: frosted, not see-through
            cr.paint()
        else:
            cr.set_source_rgba(*T.bar_flat, 1)
            cr.paint()
        tint = lerp(T.tint(0.11 + 0.09 * hov), (*T.accent, T.fill(0.62)), act)
        cr.set_source_rgba(*tint)
        cr.paint()
        cr.reset_clip()
        edge = lerp(T.line(0.32), (*T.accent_edge, 0.95), act)
        DropOverlay._round(cr, bx + 0.5, by + 0.5, bw - 1, bh - 1, self.RADIUS - 0.5)
        cr.set_line_width(T.bw)
        cr.set_source_rgba(*edge)
        cr.stroke()
        # content, clipped to the box and fading in as the box grows
        nat_w = self.row.nat.get(item.key, bw + 2 * self.SLOT_PAD) - 2 * self.SLOT_PAD
        fade = max(0.0, min(1.0, (bw - 8) / max(1.0, nat_w - 8)))
        cr.save()
        cr.rectangle(bx, by, bw, bh)
        cr.clip()
        if item.kind in ("add", "clock"):
            layout = self._layout(item)
            layout.set_text("+" if item.kind == "add" else item.title, -1)
            lw, lh = layout.get_pixel_size()
            tx, ty = bx + (bw - lw) / 2, by + (bh - lh) / 2
            if item.kind == "clock":
                cr.set_source_rgba(*T.shade(0.45 * fade))
                cr.move_to(tx, ty + 1)
                PangoCairo.show_layout(cr, layout)
            cr.set_source_rgba(*_a(T.text(0.92), fade))
            cr.move_to(tx, ty)
            PangoCairo.show_layout(cr, layout)
        elif item.tabs:
            self._paint_tabs(cr, bx, by, bh, item, fade)
        else:
            if item.icon is not None:
                Gdk.cairo_set_source_pixbuf(cr, item.icon, bx + self.PAD_X, by + (bh - self.ICON) / 2)
                cr.paint_with_alpha(fade)
            badges_w = self._badges_width(item)
            avail = bw - (self.PAD_X * 2 + self.ICON + self.ICON_GAP) - badges_w
            if avail > 4:
                layout = self._layout(item, min(avail, self._text_cap()))
                lw, lh = layout.get_pixel_size()
                tx, ty = bx + self.PAD_X + self.ICON + self.ICON_GAP, by + (bh - lh) / 2
                cr.set_source_rgba(*T.shade(0.45 * fade))          # soft text shadow keeps it legible on any wallpaper
                cr.move_to(tx, ty + 1)
                PangoCairo.show_layout(cr, layout)
                shade = lerp(T.text(0.92), T.on(1.0), act)
                cr.set_source_rgba(shade[0], shade[1], shade[2], shade[3] * fade)
                cr.move_to(tx, ty)
                PangoCairo.show_layout(cr, layout)
                if badges_w:
                    self._paint_badges(cr, tx + lw, by, bh, item, fade)
        cr.restore()
        if full < 0.999:
            cr.pop_group_to_source()
            cr.paint_with_alpha(max(0.0, full))
        cr.restore()

    def _paint_tabs(self, cr, bx, by, bh, item, fade):
        """A Chrome window as a button group: one button per tab, thin dividers, the tab on screen lit."""
        x = bx
        T = themes.current()
        for i, w in enumerate(item.seg_w):
            is_more = item.tab_more and i == len(item.seg_w) - 1
            if i:
                cr.set_source_rgba(*_a(T.line(0.16), fade))
                cr.rectangle(x - 0.5, by + 1, 1, bh - 2)
                cr.fill()
            if is_more:
                layout = self._tab_layout("+%d" % item.tab_more)
                lw, lh = layout.get_pixel_size()
                tx, ty, alpha = x + (w - lw) / 2, by + (bh - lh) / 2, 0.7
            else:
                tab = item.tabs[i]
                if tab["active"]:
                    cr.set_source_rgba(*_a(T.tint(0.15), fade))
                    cr.rectangle(x, by, w, bh)
                    cr.fill()
                elif self._hover_seg == (item.key, i):
                    cr.set_source_rgba(*_a(T.tint(0.08), fade))
                    cr.rectangle(x, by, w, bh)
                    cr.fill()
                if tab["icon"] is not None:
                    Gdk.cairo_set_source_pixbuf(cr, tab["icon"], x + self.TAB_PAD, by + (bh - self.TAB_ICON) / 2)
                    cr.paint_with_alpha(fade * (1.0 if tab["active"] else 0.8))
                layout = self._tab_layout(tab["title"], w - self.TAB_PAD * 2 - self.TAB_ICON - self.TAB_GAP)
                lw, lh = layout.get_pixel_size()
                tx, ty, alpha = x + self.TAB_PAD + self.TAB_ICON + self.TAB_GAP, by + (bh - lh) / 2, \
                    (1.0 if tab["active"] else 0.72)
            cr.set_source_rgba(*T.shade(0.45 * fade))
            cr.move_to(tx, ty + 1)
            PangoCairo.show_layout(cr, layout)
            cr.set_source_rgba(*_a(lerp(T.text(alpha), T.on(alpha), item.act), fade))
            cr.move_to(tx, ty)
            PangoCairo.show_layout(cr, layout)
            x += w

    def _segment_at(self, key, px):
        """Index of the tab button of window ``key`` under window x ``px`` (the "+ N" button counts), or None."""
        item, pos = self.items.get(key), self.row.positions().get(key)
        if item is None or pos is None or not item.tabs:
            return None
        x = self._box_rect(key, *pos)[0]
        for i, w in enumerate(item.seg_w):
            if x <= px < x + w:
                return i
            x += w
        return None

    def _draw(self, _area, cr):
        total = self.row.total()
        x0 = self._origin_x(total)
        cr.rectangle(x0 - 40, 0, total + 80, self.height)                  # only the row (+ margin) is ever visible
        cr.clip()
        if self.composited:                                               # the gaps are really transparent
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
        else:
            if self._band_raw is not None:                                # sharp copy of the desktop: looks see-through
                Gdk.cairo_set_source_pixbuf(cr, self._band_raw, 0, self._band_y - self.y)
            else:
                cr.set_source_rgb(*themes.current().bar_flat)
            cr.paint()
        gp = self.row.gap_position()
        if gp is not None and self._drag is not None:                      # soft outline of where the box will land
            gx, gw = gp
            nat = self.row.gap["nat"] or 1
            strength = max(0.0, min(1.0, gw / nat))
            bx, by, bw, bh = self._box_rect("gap", gx, gw)
            if bw > 2:
                DropOverlay._round(cr, bx, by, bw, bh, self.RADIUS)
                cr.set_source_rgba(*themes.current().accent, 0.16 * strength)
                cr.fill_preserve()
                cr.set_source_rgba(*themes.current().accent_edge, 0.55 * strength)
                cr.set_line_width(1.2)
                cr.stroke()
        for key, (x, w) in self.row.positions().items():
            item = self.items.get(key)
            if item is None:
                continue
            bx, by, bw, bh = self._box_rect(key, x, w)
            self._paint_box(cr, bx, by, bw, bh, item)
        return False

    # -------------------------------------------------------------------- glass
    def refresh_glass(self):
        """Re-read the wallpaper behind the bar.

        Without a compositor nothing can be see-through, so the bar paints a copy of what is behind it: sharp between the
        boxes (they look transparent, yet the gaps still get clicks) and blurred inside them (the glass).
        """
        if self.composited or not self.get_realized() or not self.get_mapped():
            return False                                                  # with a compositor nothing needs copying
        self.xwm.blank(self.xid())                                        # invisible but still mapped: keeps its strut
        GLib.timeout_add(320, self._grab_band)                            # let the desktop repaint under us
        return False

    def _grab_band(self):
        try:
            band_h = self.height + self.gap + 16
            y0 = self.sh - band_h if self.position == "bottom" else 0
            pix = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), 0, y0, self.sw, band_h)
            if pix is not None:
                self._band_raw, self._band, self._band_y = pix, blur(pix), y0
        except Exception as e:                                            # keep the dark fallback
            print(f"sdesk: could not capture the wallpaper for the glass ({e})", flush=True)
        self._apply_shape(True)                                           # show the bar again
        self.area.queue_draw()
        return False

    # ------------------------------------------------------------ mouse input
    def _on_motion_notify(self, _a, ev):
        key = self._item_at(ev.x)
        if self._vol_drag and ev.state & Gdk.ModifierType.BUTTON1_MASK:
            self._set_volume_from_x(ev.x)
            return True
        if key is not None and self.items[key].kind in ("clock", "battery"):
            key = None                                                      # nothing to click there: no hover glow
        seg = (key, self._segment_at(key, ev.x)) if key is not None and self.items[key].tabs else None
        if seg != self._hover_seg:
            self._hover_seg = seg
            self.area.queue_draw()
        if key != self._hover:
            self._hover = key
            self._kick()
        p = self._press
        if p and ev.state & Gdk.ModifierType.BUTTON1_MASK and p[0] in self.items and self.items[p[0]].kind == "window":
            if math.hypot(ev.x - p[1], ev.y - p[2]) > 7:                   # moved far enough: this is a drag, not a click
                self._press = None
                self._start_drag(p[0], p[1], p[2], ev)
        return False

    def _on_leave_notify(self, _a, ev):
        if self._hover_seg is not None:
            self._hover_seg = None
            self.area.queue_draw()
        if self._hover is not None:
            self._hover = None
            self._kick()
        return False

    def _on_press(self, _a, ev):
        key = self._item_at(ev.x)
        kind = self.items[key].kind if key is not None else None
        if ev.button == 1 and kind == "volume":
            self._vol_drag = True                                         # drag the knob (a click jumps there)
            self._set_volume_from_x(ev.x)
            self._kick()
        elif ev.button == 1:
            self._press = (key, ev.x, ev.y, 1) if key is not None else None
        elif ev.button == 2 and kind in ("volume", "sound"):
            self.muted = not self.muted
            self.on_mute()
            self.area.queue_draw()
        elif ev.button == 2 and kind == "toggl":
            if self.toggl is not None and self.toggl.running:
                self.on_toggl_stop()                                      # middle click stops the timer
        elif ev.button == 2 and kind == "window":
            self.c.b.close(self.items[key].wid)                           # middle click closes the window
        elif ev.button == 3:
            kind = self.items[key].kind if key is not None else None
            if kind == "window":
                self.on_window_menu(self.items[key].wid, ev.x_root, self.y)   # Close / Force Quit
            elif kind == "sound":
                self.on_sound_menu(ev.x_root, self.y)                     # choose an output
            elif kind in ("clock", "toggl", "battery", "volume"):
                pass
            else:                                                         # between boxes, or on "+": add a window here
                self.on_menu(self._windows_before(ev.x) if key is None else self._n_windows(), ev.x_root, self.y)
        return True

    def _on_release(self, _a, ev):
        if self._vol_drag:
            self._vol_drag = False
            self._kick()
            return True
        p, self._press = self._press, None
        if ev.button == 1 and p and p[0] == self._item_at(ev.x):
            kind = self.items[p[0]].kind
            if kind == "add":
                self.on_menu(self._n_windows(), ev.x_root, self.y)
            elif kind == "sound":
                self.on_sound_click()
            elif kind == "toggl":
                self.on_toggl_click(*self.toggl_anchor())
            elif kind == "window":
                item = self.items[p[0]]
                seg = self._segment_at(p[0], ev.x)
                if item.tabs and seg is not None and seg < len(item.tabs) and not item.tabs[seg]["active"]:
                    self.on_tab_click(item.wid, item.chrome_window, item.tabs[seg])     # another tab of that window
                else:
                    if item.thread and self.threads.get(item.thread, {}).get("done"):
                        self.on_thread_seen(item.thread)
                    self.c.click(item.wid)
        return True

    def _on_tooltip(self, _a, x, y, _kb, tip):
        key = self._item_at(x)
        item = self.items.get(key)
        if self._drag is not None or item is None or key == "add":
            return False
        if item.kind == "clock":
            tip.set_text(time.strftime("%A %d %B %Y"))
        elif item.kind == "battery":
            tip.set_text(battery.tooltip(self.batteries))
        elif item.kind == "toggl":
            tip.set_text(self._toggl_tip())
        elif item.kind == "volume":
            tip.set_text(f"Volume {self.volume}%{' (muted)' if self.muted else ''}\nDrag or scroll to change, middle-click to mute")
        elif item.kind == "sound":
            tip.set_text(self.sound_tip or item.title)
        elif item.tabs:
            seg = self._segment_at(key, x)
            if seg is not None and seg < len(item.tabs):
                tip.set_text(item.tabs[seg]["full"] or item.tabs[seg]["title"])
            elif seg is not None:
                tip.set_text("%d more tabs" % item.tab_more)
            else:
                tip.set_text(item.tip or item.title)
        else:
            tip.set_text((item.tip or item.title or item.wm_class) + self._thread_tip(item))
        return True

    # ------------------------------------------------------------ drag & drop
    def _start_drag(self, key, x, y, ev):
        item = self.items[key]
        pos = self.row.positions()[key]
        bx, by, _, _ = self._box_rect(key, *pos)
        live = self.row._live()
        self._drag = {"key": key, "wid": item.wid, "origin": live.index(key), "grab": (x - bx, y - by), "dropped": False}
        self.row.pick_up(key)                   # its slot turns into a gap of the same size: nothing moves
        self._hover = None
        self.area.drag_begin_with_coordinates(self._targets, Gdk.DragAction.MOVE, 1, ev, int(x), int(y))
        self._kick()

    def _ghost_geometry(self):
        """Size of the drag ghost's surface and where the pointer holds it (after the tilt)."""
        d = self._drag
        item = self.items[d["key"]]
        bw = self.row.nat[d["key"]] - 2 * self.SLOT_PAD
        bh = self.height - 2 * self.SLOT_PAD
        pad = 18
        W, H = int(bw + 2 * pad), int(bh + 2 * pad)
        a = math.radians(self.TILT)
        gx, gy = d["grab"]
        vx, vy = gx - bw / 2, gy - bh / 2
        hx = W / 2 + vx * math.cos(a) - vy * math.sin(a)
        hy = H / 2 + vx * math.sin(a) + vy * math.cos(a)
        return item, W, H, bw, bh, int(round(hx)), int(round(hy)), a

    def _bar_backdrop(self):
        """The bar as it looks right now (boxes included, without the box being dragged), blurred: what the ghost, being
        frosted glass, shows through itself. Returns (pixbuf, window x of its left edge)."""
        try:
            total = self.row.total()
            x0, pad, h = self._origin_x(total), 40, self.height
            w = int(total + 2 * pad)
            surf = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
            cr = cairo.Context(surf)
            cr.translate(-(x0 - pad), 0)
            self._draw(None, cr)
            return blur(Gdk.pixbuf_get_from_surface(surf, 0, 0, w, h), 5), x0 - pad
        except Exception:
            return None

    def _render_ghost(self, cr, geo, origin, outline=False):
        """Paint the tilted ghost. ``origin`` = screen position of the surface's top-left, or None for a plain outline.

        Real transparency needs a compositor, so, like the bar, the ghost is glass: it is painted over a blurred copy of what
        is underneath it (the bar with its boxes, else the wallpaper) with the same flat tint and thin edge."""
        item, W, H, bw, bh, hx, hy, a = geo
        cr.save()
        cr.translate(W / 2, H / 2)
        cr.rotate(a)
        cr.translate(-bw / 2, -bh / 2)
        DropOverlay._round(cr, 0, 0, bw, bh, self.RADIUS)
        cr.restore()                                # the tilted outline stays as the current path
        cr.clip()
        if outline:                                  # only the silhouette is wanted (window shape / blur region)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.paint()
            return
        T = themes.current()
        if self.composited:                          # really translucent; kwin blurs what is behind it
            cr.set_source_rgba(*T.bar)
            cr.paint()
        else:
            cr.set_source_rgba(*T.paint("ghost_flat"), 1)  # base where no copy of the background exists (over windows)
            cr.paint()
        if origin is not None and not self.composited:
            ox, oy = origin
            if self._band is not None:
                Gdk.cairo_set_source_pixbuf(cr, self._band, -ox, self._band_y - oy)
                cr.paint()
            snap = self._bar_backdrop()
            if snap is not None:
                Gdk.cairo_set_source_pixbuf(cr, snap[0], snap[1] - ox, self.y - oy)
                cr.paint()
            cr.set_source_rgba(*T.bar_dim)
            cr.paint()
        cr.reset_clip()
        cr.save()
        cr.translate(W / 2, H / 2)
        cr.rotate(a)
        cr.translate(-bw / 2, -bh / 2)
        saved = (item.hov, item.dim)
        item.hov, item.dim = 0.0, 0.0
        self._paint_box(cr, 0, 0, bw, bh, item, backdrop=False)
        item.hov, item.dim = saved
        cr.restore()

    @staticmethod
    def _opaque_rects(surf, threshold=128):
        """Rectangles covering the pixels of an ARGB32 surface that are at least half opaque (row by row)."""
        w, h, stride = surf.get_width(), surf.get_height(), surf.get_stride()
        data = surf.get_data()
        rects = []
        for y in range(h):
            row = y * stride
            x = 0
            while x < w:
                if data[row + x * 4 + 3] >= threshold:
                    start = x
                    while x < w and data[row + x * 4 + 3] >= threshold:
                        x += 1
                    rects.append((start, y, x - start, 1))
                else:
                    x += 1
        return rects

    def _on_drag_begin(self, _a, ctx):
        """Draw the drag icon ourselves and clip its window to the ghost's outline. Without a compositor GTK does not
        shape drag icons, which would leave a plain rectangle behind the tilted ghost."""
        if self._drag is None:
            return
        geo = self._ghost_geometry()
        _, W, H, _, _, hx, hy, _ = geo
        outline = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        self._render_ghost(cairo.Context(outline), geo, None, outline=True)   # silhouette: window shape / blur region
        da = Gtk.DrawingArea()
        da.set_size_request(W, H)

        def paint(_w, cr):
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)
            seat = Gdk.Display.get_default().get_default_seat()
            _, px, py = seat.get_pointer().get_position()
            self._render_ghost(cr, geo, (px - hx, py - hy))
            return False

        da.connect("draw", paint)
        da.show()
        self._ghost_da = da
        Gtk.drag_set_icon_widget(ctx, da, hx, hy)
        top = da.get_toplevel()                      # GTK wraps the widget in its own icon window: clip that one
        top.realize()
        xid = top.get_window().get_xid()
        rects = self._opaque_rects(outline)
        if self.composited:
            self.xwm.set_blur_region(xid, rects)     # a frosted, translucent ghost
        else:
            self.xwm.set_shape_rects(xid, rects)

    def _pointer_closed_x(self):
        """Pointer x in the coordinates of the closed layout (the row without the gap, centred): stable while the gap opens."""
        seat = Gdk.Display.get_default().get_default_seat()
        _, px, _ = seat.get_pointer().get_position()
        _, closed_total = self.row.closed_layout()
        head = self._head_width(closed=True)
        return px - (self.center_x - (closed_total - head) / 2 - head)

    def _cancel_leave(self):
        if self._leave_timer:
            GLib.source_remove(self._leave_timer)
            self._leave_timer = 0

    def _on_drag_motion(self, _a, ctx, x, y, t):
        if self._drag is None:
            return False
        self._cancel_leave()
        if self._ghost_da is not None:
            self._ghost_da.queue_draw()                 # the glass follows what is under it
        Gdk.drag_status(ctx, Gdk.DragAction.MOVE, t)
        idx = self.row.drop_index(self._pointer_closed_x())
        if idx != self.row.gap["idx"]:
            self.row.set_gap(idx)
            self.c.bar_preview(self._drag["wid"], idx)     # the desktop previews the same drop
            self._kick()
        return True

    def _on_drag_leave(self, _a, ctx, t):
        # GTK also emits drag-leave right *before* drag-drop, so do not react at once: if a drop follows, it cancels this
        if self._drag is not None and not self._leave_timer:
            self._leave_timer = GLib.timeout_add(80, self._gap_home)

    def _gap_home(self):
        self._leave_timer = 0
        d = self._drag
        if d is not None and self.row.gap["idx"] != d["origin"]:
            self.row.set_gap(d["origin"])                  # pointer left the bar: the gap glides back to where it came from
            self.c.clear_preview()
            self._kick()
        return False

    def _on_drag_drop(self, _a, ctx, x, y, t):
        self._cancel_leave()
        d = self._drag
        if d is not None:
            idx = self.row.drop()                          # lands in the gap, seamlessly
            d["dropped"] = True
            self.c.reorder(d["wid"], idx)
            self._kick()
        Gtk.drag_finish(ctx, True, False, t)
        return True

    def _on_drag_end(self, _a, ctx):
        self._cancel_leave()
        self._ghost_da = None
        d, self._drag = self._drag, None
        if d is not None and not d["dropped"]:
            self.row.drop(d["origin"])                     # cancelled: back where it was
            self.c.clear_preview()
        if self._pending_refresh:
            self._pending_refresh = False
            self.refresh()
        self._kick()
