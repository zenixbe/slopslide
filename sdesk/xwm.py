"""X11 backend: talks EWMH to the running window manager (kwin_x11 on Deepin)."""
import os
import signal
from dataclasses import dataclass

from Xlib import X, Xatom, Xcursorfont as cursorfont, display, error, protocol
from Xlib.ext import shape

from .layout import Rect

NO_DESKTOP_FILTER = 0xFFFFFFFF   # _NET_WM_DESKTOP value of "on all desktops"
LOCK_MASKS = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)   # caps/num lock combos
SUPER = X.Mod4Mask


@dataclass
class WinInfo:
    title: str = ""
    wm_class: str = ""


class XWM:
    def __init__(self, exclude_xids=lambda: ()):
        self.d = display.Display()
        self.root = self.d.screen().root
        self.exclude_xids = exclude_xids
        self._wins = {}                  # xid -> Window resource
        self._watched = set()
        self._own_strut = {}
        self._zone_wins = []             # pool of invisible input-only windows (right-click targets)
        self._zone_idx = {}              # their xid -> insert index
        self._zones_shown = []
        self.A = {}                      # atom cache
        # callbacks, set by the app
        self.on_clients = lambda: None
        self.on_active = lambda: None
        self.on_workarea = lambda: None
        self.on_title = lambda wid: None
        self.on_scroll = lambda direction: None
        self.on_key = lambda name: None             # a key grabbed with grab_key was pressed
        self._keys = {}                             # (keycode, modifiers without locks) -> name
        self.on_zone_menu = lambda idx, x, y: None
        self.on_zone_resize = lambda idx, x, y: None      # left button pressed in a gap: the user grabbed it to resize
        self.root.change_attributes(event_mask=X.PropertyChangeMask)
        self.d.flush()

    # -------------------------------------------------------------- basics
    def atom(self, name):
        if name not in self.A:
            self.A[name] = self.d.intern_atom(name)
        return self.A[name]

    def fileno(self):
        return self.d.fileno()

    def win(self, wid):
        if wid not in self._wins:
            self._wins[wid] = self.d.create_resource_object("window", wid)
        return self._wins[wid]

    def _prop(self, window, name, prop_type=X.AnyPropertyType, length=1024):
        try:
            p = window.get_full_property(self.atom(name), prop_type, length)
        except error.XError:
            return None
        return None if p is None else p.value

    def _cardinals(self, window, name):
        v = self._prop(window, name)
        return [] if v is None else list(v)

    def _atoms(self, window, name):
        return [self.d.get_atom_name(a) for a in self._cardinals(window, name)]

    def _send(self, window, name, data, mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask):
        data = [v & 0xFFFFFFFF for v in data]          # negative coordinates -> two's complement
        ev = protocol.event.ClientMessage(
            window=window, client_type=self.atom(name), data=(32, data))
        self.root.send_event(ev, event_mask=mask)
        self.d.flush()

    # --------------------------------------------------------- environment
    def current_desktop(self):
        v = self._cardinals(self.root, "_NET_CURRENT_DESKTOP")
        return v[0] if v else 0

    def workarea(self):
        v = self._cardinals(self.root, "_NET_WORKAREA")
        i = min(self.current_desktop(), max(0, len(v) // 4 - 1)) * 4
        if len(v) >= i + 4:
            return Rect(*v[i:i + 4])
        # WM publishes no work area: use the whole screen minus our own bar
        g = self.root.get_geometry()
        top = self._own_strut.get("top", 0)
        return Rect(0, top, g.width, g.height - top - self._own_strut.get("bottom", 0))

    def screen_size(self):
        g = self.root.get_geometry()
        return g.width, g.height

    def active(self):
        v = self._cardinals(self.root, "_NET_ACTIVE_WINDOW")
        return v[0] if v and v[0] else None

    def pointer(self):
        q = self.root.query_pointer()
        return q.root_x, q.root_y, bool(q.mask & (X.Button1Mask | X.Button3Mask))

    # ------------------------------------------------------------- windows
    def list_windows(self):
        cur = self.current_desktop()
        out = []
        for xid in self._cardinals(self.root, "_NET_CLIENT_LIST"):
            if xid in self.exclude_xids():
                continue
            w = self.win(xid)
            if self._manageable(w, cur):
                out.append(xid)
                self._watch(w)
        return out

    def _watch(self, w):
        if w.id in self._watched:
            return
        self._watched.add(w.id)
        try:
            w.change_attributes(event_mask=X.PropertyChangeMask)
        except error.XError:
            pass

    def _manageable(self, w, cur_desktop):
        try:
            types = self._atoms(w, "_NET_WM_WINDOW_TYPE")
            if types and types[0] != "_NET_WM_WINDOW_TYPE_NORMAL":
                return False
            if w.get_wm_transient_for():
                return False
            states = self._atoms(w, "_NET_WM_STATE")
            for bad in ("FULLSCREEN", "SKIP_TASKBAR", "MODAL"):         # HIDDEN (minimised / show desktop) stays managed
                if "_NET_WM_STATE_" + bad in states:
                    return False
            desk = self._cardinals(w, "_NET_WM_DESKTOP")
            if desk and desk[0] not in (cur_desktop, NO_DESKTOP_FILTER):
                return False
            return True
        except error.XError:
            return False

    def _sidebar_candidates(self):
        cands = []
        for xid in self._cardinals(self.root, "_NET_CLIENT_LIST"):
            if xid in self.exclude_xids():
                continue
            w = self.win(xid)
            try:
                types = self._atoms(w, "_NET_WM_WINDOW_TYPE")
                viewable = w.get_attributes().map_state == X.IsViewable
            except error.XError:
                continue
            rect = self.frame_rect(xid)
            if not types or rect is None:
                continue
            cands.append({"xid": xid, "type": types[0].replace("_NET_WM_WINDOW_TYPE_", ""), "viewable": viewable, "rect": rect,
                          "states": {a.replace("_NET_WM_STATE_", "") for a in self._atoms(w, "_NET_WM_STATE")}})
        return cands

    def gap_obstacles(self, strip_xids):
        """Rects of mapped windows that may lie over a gap of the strip and must keep their clicks: tool windows (UTILITY,
        TOOLBAR) and anything transient for a window of the strip (``strip_xids``), such as a docked side panel or a
        dialog."""
        out = []
        for xid in self._cardinals(self.root, "_NET_CLIENT_LIST"):
            if xid in strip_xids or xid in self.exclude_xids():
                continue
            w = self.win(xid)
            try:
                if w.get_attributes().map_state != X.IsViewable:
                    continue
                if "_NET_WM_STATE_HIDDEN" in self._atoms(w, "_NET_WM_STATE"):
                    continue
                types = self._atoms(w, "_NET_WM_WINDOW_TYPE")
                tool = bool(types) and types[0] in ("_NET_WM_WINDOW_TYPE_UTILITY", "_NET_WM_WINDOW_TYPE_TOOLBAR")
                if not tool:
                    parent = w.get_wm_transient_for()
                    if parent is None or parent.id not in strip_xids:
                        continue
            except error.XError:
                continue                                   # window vanished while we looked
            rect = self.frame_rect(xid)
            if rect is not None:
                out.append(rect)
        return out

    def sidebar_rect(self):
        """Rect of a floating widget sidebar on the right edge, or None."""
        from . import layout
        sw, sh = self.screen_size()
        return layout.find_sidebar(self._sidebar_candidates(), sw, sh)

    def top_level_of(self, xid):
        """The ancestor of ``xid`` that is a direct child of the root (kwin wraps clients in a frame, sometimes in two
        levels): the only kind of window whose stacking can be compared with ours."""
        cur = self.win(xid)
        for _ in range(8):
            parent = cur.query_tree().parent
            if parent.id == self.root.id:
                return cur.id
            cur = parent
        return None

    def sidebar_frame(self):
        """X id of the sidebar's top-level (frame) window, or None."""
        from . import layout
        sw, sh = self.screen_size()
        c = layout.find_sidebar_candidate(self._sidebar_candidates(), sw, sh)
        if c is None:
            return None
        try:
            return self.top_level_of(c["xid"])
        except error.XError:
            return None

    def keep_directly_below(self, xid, sibling):
        """Make sure window ``xid`` (one of ours, override-redirect) is stacked immediately under ``sibling``, because kwin
        reorders its own windows and may leave us behind. Returns True if it ends up there, False if it could not be done."""
        from . import layout
        try:
            children = [c.id for c in self.root.query_tree().children]
            if not layout.directly_below(children, xid, sibling):
                self.win(xid).configure(stack_mode=X.Below, sibling=sibling)
                self.d.sync()
                children = [c.id for c in self.root.query_tree().children]
            return layout.directly_below(children, xid, sibling)
        except error.XError:
            return False

    def right_inset(self, pad):
        """Width of a floating widget sidebar on the right edge (+ padding), 0 if there is none."""
        from . import layout
        sw, sh = self.screen_size()
        return layout.right_inset(self._sidebar_candidates(), sw, sh, pad)

    def desktop_image(self, x, y, w, h):
        """Pixels (BGRX, 4 bytes each) of the desktop wallpaper window, read from the window itself: with a compositor
        that is the wallpaper even where other windows cover it. None if there is no such window."""
        for xid in self._cardinals(self.root, "_NET_CLIENT_LIST"):
            win = self.win(xid)
            try:
                if "_NET_WM_WINDOW_TYPE_DESKTOP" in self._atoms(win, "_NET_WM_WINDOW_TYPE"):
                    img = win.get_image(x, y, w, h, X.ZPixmap, 0xFFFFFFFF)
                    return bytes(img.data) if img.depth in (24, 32) and len(img.data) >= w * h * 4 else None
            except error.XError:
                return None
        return None

    def is_hidden(self, wid):
        return "_NET_WM_STATE_HIDDEN" in self._atoms(self.win(wid), "_NET_WM_STATE")

    def frame_top(self, wid):
        """Height of the window's titlebar (top frame extent), 0 if unknown."""
        return self._frame_extents(self.win(wid))[2]

    def pid(self, wid):
        v = self._cardinals(self.win(wid), "_NET_WM_PID")
        return v[0] if v else None

    def app_windows(self, wid):
        """All managed-looking windows of the same process (for the force-quit warning)."""
        pid = self.pid(wid)
        if not pid:
            return [wid]
        return [x for x in self._cardinals(self.root, "_NET_CLIENT_LIST") if self.pid(x) == pid
                and self._atoms(self.win(x), "_NET_WM_WINDOW_TYPE")[:1] in ([], ["_NET_WM_WINDOW_TYPE_NORMAL"])] or [wid]

    def force_quit(self, wid):
        """Kill the application that owns the window (all its windows go with it)."""
        pid = self.pid(wid)
        if pid and pid > 1 and pid != os.getpid():
            os.kill(pid, signal.SIGKILL)
        else:
            self.win(wid).kill_client()            # no usable pid: drop its X connection instead
        self.d.flush()

    def info(self, wid):
        w = self.win(wid)
        title = self._prop(w, "_NET_WM_NAME")
        if title is None:
            title = self._prop(w, "WM_NAME")
        if isinstance(title, bytes):
            title = title.decode("utf-8", "replace")
        try:
            cls = w.get_wm_class() or ("", "")
        except error.XError:
            cls = ("", "")
        return WinInfo(title or "", cls[1] if len(cls) > 1 else cls[0])

    def icon_argb(self, wid, want=32):
        """Best-fitting _NET_WM_ICON as (w, h, [argb ints]) or None."""
        data = self._cardinals(self.win(wid), "_NET_WM_ICON")
        best, i = None, 0
        while i + 2 <= len(data):
            w, h = data[i], data[i + 1]
            if w <= 0 or h <= 0 or i + 2 + w * h > len(data):
                break
            if best is None or abs(w - want) < abs(best[0] - want):
                best = (w, h, data[i + 2:i + 2 + w * h])
            i += 2 + w * h
        return best

    def _frame_extents(self, w):
        v = self._cardinals(w, "_NET_FRAME_EXTENTS")
        return tuple(v[:4]) if len(v) >= 4 else (0, 0, 0, 0)    # left, right, top, bottom

    def frame_rect(self, wid):
        w = self.win(wid)
        try:
            l, r, t, b = self._frame_extents(w)
            g = w.get_geometry()
            o = self.root.translate_coords(w, 0, 0)    # client origin in root coordinates
            return Rect(o.x - l, o.y - t, g.width + l + r, g.height + t + b)
        except error.XError:
            return None

    # ------------------------------------------------------------- actions
    def place(self, wid, rect):
        w = self.win(wid)
        try:
            l, r, t, b = self._frame_extents(w)
            cw, ch = max(1, rect.w - l - r), max(1, rect.h - t - b)
            flags = 1 | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (2 << 12)   # NW gravity, x/y/w/h, pager
            self._send(w, "_NET_MOVERESIZE_WINDOW", [flags, rect.x, rect.y, cw, ch])
        except error.XError:
            pass                                       # window vanished mid-animation

    def unmaximize(self, wid):
        w = self.win(wid)
        states = self._atoms(w, "_NET_WM_STATE")
        for s in ("_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"):
            if s in states:
                self._send(w, "_NET_WM_STATE", [0, self.atom(s), 0, 2, 0])   # 0 = remove

    def activate(self, wid):
        self._send(self.win(wid), "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0, 0, 0])

    def minimize(self, wid):
        """Ask the window manager to minimise the window (the ICCCM way: WM_CHANGE_STATE to IconicState)."""
        self._send(self.win(wid), "WM_CHANGE_STATE", [3, 0, 0, 0, 0])

    def close(self, wid):
        self._send(self.win(wid), "_NET_CLOSE_WINDOW", [X.CurrentTime, 2, 0, 0, 0])

    def set_strut(self, xid, edge, size, span_start, span_end):
        """Reserve ``size`` px at screen edge 'bottom'|'top' for a dock window."""
        w = self.win(xid)
        idx = {"top": 2, "bottom": 3}[edge]
        self._own_strut[edge] = size
        strut = [0, 0, 0, 0]
        strut[idx] = size
        partial = strut + [0, 0, 0, 0, 0, 0, 0, 0]
        partial[8 + (idx - 2) * 2] = span_start
        partial[9 + (idx - 2) * 2] = span_end
        w.change_property(self.atom("_NET_WM_STRUT_PARTIAL"), Xatom.CARDINAL, 32, partial)
        w.change_property(self.atom("_NET_WM_STRUT"), Xatom.CARDINAL, 32, strut)
        self.d.flush()

    # ------------------------------------------------- Super + scroll grab
    def grab_super_scroll(self):
        """Grab Super+wheel (buttons 4-7) on the root window. Returns False if refused."""
        ok = True
        for button in (4, 5, 6, 7):
            for lock in LOCK_MASKS:
                err = error.CatchError(error.BadAccess)
                self.root.grab_button(
                    button, SUPER | lock, True, X.ButtonPressMask,
                    X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE, onerror=err)
                self.d.sync()
                if err.get_error():
                    ok = False
        return ok

    def grab_key(self, name, keysym_name, mods):
        """Grab a key combination on the root window (e.g. "t" with SUPER): pressing it calls ``on_key(name)``.
        Returns False if another program holds it already (X answers BadAccess)."""
        from Xlib import XK
        code = self.d.keysym_to_keycode(XK.string_to_keysym(keysym_name))
        if not code:
            return False
        ok = True
        for lock in LOCK_MASKS:
            err = error.CatchError(error.BadAccess)
            self.root.grab_key(code, mods | lock, True, X.GrabModeAsync, X.GrabModeAsync, onerror=err)
            self.d.sync()
            if err.get_error():
                ok = False
        if ok:
            self._keys[(code, mods)] = name
        else:
            for lock in LOCK_MASKS:
                self.root.ungrab_key(code, mods | lock)
            self.d.sync()
        return ok

    def blank(self, xid):
        """Make a window (all but 1px of it) invisible without unmapping it, so it keeps its strut."""
        self.win(xid).shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, [(0, 0, 1, 1)])
        self.d.sync()

    def set_shape(self, xid, x, y, w, h):
        """Clip a window (clicks too) to one rectangle. The window itself never moves, so what it has drawn stays aligned."""
        win = self.win(xid)
        rect = [(int(x), int(y), max(1, int(w)), max(1, int(h)))]
        win.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, rect)
        win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, rect)
        self.d.flush()

    def set_shape_rects(self, xid, rects):
        """Clip a window to a list of (x, y, w, h) rectangles."""
        win = self.win(xid)
        win.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.YXBanded, 0, 0, rects)
        win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.YXBanded, 0, 0, rects)
        self.d.flush()

    def set_blur_region(self, xid, rects):
        """Ask kwin's blur effect to blur what is behind these (x, y, w, h) rectangles of the window. [] removes it."""
        win = self.win(xid)
        atom = self.atom("_KDE_NET_WM_BLUR_BEHIND_REGION")
        if rects:
            win.change_property(atom, Xatom.CARDINAL, 32, [v for r in rects for v in r])
        else:
            win.delete_property(atom)
        self.d.flush()

    def unblank(self, xid):
        self.win(xid).shape_mask(shape.SO.Set, shape.SK.Bounding, 0, 0, X.NONE)
        self.d.sync()

    # ------------------------------------------------------ gap click zones
    def set_zones(self, zones):
        """Show invisible click targets: zones = [(insert_index, Rect)]; [] hides them all."""
        if zones == self._zones_shown:
            return
        self._zones_shown = list(zones)
        while len(self._zone_wins) < len(zones):
            # InputOnly: no pixels, so it works with or without a compositor
            zw = self.root.create_window(0, 0, 1, 1, 0, X.CopyFromParent, X.InputOnly, X.CopyFromParent,
                                         override_redirect=1, event_mask=X.ButtonPressMask,
                                         cursor=self._resize_cursor())
            self._zone_wins.append(zw)
        self._zone_idx = {}
        for zw, (idx, r) in zip(self._zone_wins, zones):
            zw.configure(x=r.x, y=r.y, width=r.w, height=r.h, stack_mode=X.Above)
            zw.map()
            self._zone_idx[zw.id] = idx
        for zw in self._zone_wins[len(zones):]:
            zw.unmap()
        self.d.flush()

    def _resize_cursor(self):
        """The horizontal double arrow: tells the user that a gap can be dragged to resize."""
        if getattr(self, "_hcursor", None) is None:
            try:
                font = self.d.open_font("cursor")
                self._hcursor = font.create_glyph_cursor(font, cursorfont.sb_h_double_arrow, cursorfont.sb_h_double_arrow + 1,
                                                         (0, 0, 0), (0xffff, 0xffff, 0xffff))
            except Exception:
                self._hcursor = X.NONE
        return self._hcursor

    def start_resize(self, xid, direction, x, y):
        """Hand an interactive resize of window ``xid`` to the window manager, as if its own border had been grabbed.
        ``direction``: _NET_WM_MOVERESIZE value (3 = right edge, 7 = left edge); (x, y) the pointer, root coordinates."""
        self.d.ungrab_pointer(X.CurrentTime)            # the click on our gap window grabbed the pointer: let the wm have it
        self.d.flush()
        self.activate(xid)                              # kwin only starts a resize for the active window
        self._send(self.win(xid), "_NET_WM_MOVERESIZE", [x, y, direction, 1, 1])

    # --------------------------------------------------------- event loop
    def pump(self):
        """Drain pending X events and fire callbacks."""
        clients = active = workarea = False
        titles = set()
        while self.d.pending_events():
            ev = self.d.next_event()
            if ev.type == X.PropertyNotify:
                name = self.d.get_atom_name(ev.atom)
                if ev.window.id == self.root.id:
                    if name in ("_NET_CLIENT_LIST", "_NET_CURRENT_DESKTOP"):
                        clients = True
                    elif name == "_NET_ACTIVE_WINDOW":
                        active = True
                    elif name == "_NET_WORKAREA":
                        workarea = True
                elif name in ("_NET_WM_NAME", "WM_NAME", "WM_CLASS", "_NET_WM_ICON"):
                    titles.add(ev.window.id)
                elif name in ("_NET_WM_STATE", "_NET_WM_DESKTOP", "_NET_WM_WINDOW_TYPE"):
                    clients = True
            elif ev.type == X.KeyPress:
                name = self._keys.get((ev.detail, ev.state & ~(X.LockMask | X.Mod2Mask) & 0xFF))
                if name:
                    self.on_key(name)
            elif ev.type == X.ButtonPress:
                if ev.window.id in self._zone_idx:
                    if ev.detail == 3:
                        self.on_zone_menu(self._zone_idx[ev.window.id], ev.root_x, ev.root_y)
                    elif ev.detail == 1:
                        self.on_zone_resize(self._zone_idx[ev.window.id], ev.root_x, ev.root_y)
                elif ev.detail in (4, 5, 6, 7):
                    # wheel up / left = towards the start of the strip
                    self.on_scroll(-1 if ev.detail in (4, 6) else 1)
        if clients:
            self.on_clients()
        if workarea:
            self.on_workarea()
        if active:
            self.on_active()
        for wid in titles:
            self.on_title(wid)
