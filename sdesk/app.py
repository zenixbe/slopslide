"""Entry point: wires the X backend, the controller, the taskbar and the overlay together."""
import argparse
import os
import signal
import sys
import traceback

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import audio, battery, chrometabs, dock, ipc, safety, threads  # noqa: E402
from .edgefade import EdgeFade  # noqa: E402
from .controller import LAUNCH_TIMEOUT, Controller  # noqa: E402
from .menu import MenuItem, QuickMenu, launch, load_menu_items, window_menu_items  # noqa: E402
from .taskbar import DropOverlay, Taskbar  # noqa: E402
from .xwm import XWM  # noqa: E402


def parse_args(argv):
    p = argparse.ArgumentParser(prog="sdesk", description="Scrolling desktop for Deepin / kwin_x11")
    p.add_argument("--gap", type=int, default=24, help="space between windows (px)")
    p.add_argument("--margin", type=int, default=24, help="space at the strip ends (px)")
    p.add_argument("--vmargin", type=int, default=12,
                   help="space above and below the windows (px); a window with a tall titlebar may sit lower, see README")
    p.add_argument("--new-width", type=float, default=1 / 3, metavar="FRACTION",
                   help="a window that opens after sdesk started is at most this fraction of the screen wide (default 1/3)")
    p.add_argument("--right-edge", default="auto", metavar="auto|off|PX",
                   help="windows end before a floating sidebar on the right edge: 'auto' finds it (default), 'off' ignores it, "
                        "or give its width in px")
    p.add_argument("--edge-gap", default="auto", metavar="auto|off|PX",
                   help="with a compositor, windows fold away under the wallpaper this far left of the sidebar, so none is ever visible "
                        "under it: 'auto' = the edge padding (default), 'off', or a width in px")
    p.add_argument("--edge-fade", default="auto", metavar="auto|off|PX",
                   help="a soft edge this wide left of that gap, where windows dissolve instead of being cut: 'auto' = none (default), 'off', or a width in px")
    p.add_argument("--edge-padding", type=int, default=16, help="space between the windows and such a sidebar (px)")
    p.add_argument("--scroll-step", type=int, default=480, help="px scrolled per wheel notch")
    p.add_argument("--bar", choices=("bottom", "top"), default="bottom", help="taskbar position")
    p.add_argument("--bar-height", type=int, default=52)
    p.add_argument("--sound", default="auto", choices=("auto", "off"),
                   help="show the current sound output left of the taskbar icons (click = next output); 'off' hides it")
    p.add_argument("--battery", default="auto", choices=("auto", "off"),
                   help="show the battery of the mouse (Logitech receiver) and the Dygma Defy halves next to the clock; "
                        "'off' hides it and never reads the devices")
    p.add_argument("--sound-match", default="", metavar="TEXT",
                   help="a click cycles only through the outputs whose name contains TEXT, e.g. 'logitech' "
                        "(default: every real output; also every one if fewer than two match)")
    p.add_argument("--nag-after", type=int, default=30, metavar="MIN",
                   help="a window minimised for this many minutes shakes its taskbar box now and then (0 = never)")
    p.add_argument("--nag-every", type=int, default=5, metavar="MIN", help="minutes between those shakes")
    p.add_argument("--chrome-tabs", default="auto", choices=("auto", "off"),
                   help="show the tabs of Chrome windows as buttons (needs the extension in chrome-extension/); 'off' disables")
    p.add_argument("--chrome-port", type=int, default=chrometabs.DEFAULT_PORT, help="port on 127.0.0.1 the extension connects to")
    p.add_argument("--threads", default="auto", choices=("auto", "off"),
                   help="badges on the Claude and T3 Code taskbar boxes for their threads that wait for you / are done / work; "
                        "'off' disables")
    p.add_argument("--bar-gap", type=int, default=8, help="float the bar this many px off the screen edge")
    p.add_argument("--disable-dock", action="store_true",
                   help="turn Deepin's own dock off while running (restored on exit). Also removes the tray/clock in it")
    p.add_argument("--hide-dock", action="store_true",
                   help="weaker: only set the dock to keep-hidden (it still slides out at the bottom edge)")
    p.add_argument("--restore", "--restore-dock", dest="restore", action="store_true",
                   help="panic button: stop sdesk, put every window back where it was and bring Deepin's dock back")
    p.add_argument("--guardian", type=int, metavar="PID", help=argparse.SUPPRESS)   # internal: watches sdesk's pid
    return p.parse_args(argv)


def run_guardian(pid):
    """Independent watcher: if sdesk dies without exiting cleanly, put windows and dock back."""
    xwm = XWM()
    result = safety.guard(pid,
                          lambda: safety.restore_windows(xwm, safety.load_originals()),
                          lambda: dock.restore("block"))
    if result == "restored":
        print("sdesk guardian: sdesk died; windows and dock restored", flush=True)
        safety.mark_clean()
    try:
        os.remove(safety.GUARDIAN_PID)
    except OSError:
        pass
    return 0


def panic():
    """`sdesk --restore`: whatever state things are in, get a normal desktop back."""
    me = os.getpid()
    for entry in os.listdir("/proc"):
        if entry.isdigit() and int(entry) != me and safety.is_sdesk(int(entry)):
            try:
                os.kill(int(entry), signal.SIGTERM)                 # a running sdesk cleans up after itself
            except OSError:
                pass
    import time
    time.sleep(2.5)
    originals = safety.load_originals()
    if originals:
        safety.restore_windows(XWM(), originals)
    dock.restore("block")
    safety.mark_clean()
    print(f"sdesk: restored {len(originals)} window(s) and the dock.")
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.guardian:
        return run_guardian(args.guardian)
    if args.restore:
        return panic()

    bar_ids = set()
    xwm = XWM(exclude_xids=lambda: bar_ids)
    right_edge = args.right_edge if args.right_edge in ("auto", "off") else int(args.right_edge)
    fade_px = 0 if args.edge_gap == "off" else (args.edge_padding if args.edge_gap == "auto" else int(args.edge_gap))
    ctl = Controller(xwm, gap=args.gap, margin=args.margin, scroll_step=args.scroll_step, new_max_frac=args.new_width,
                      vmargin=args.vmargin, right_edge=right_edge, edge_pad=args.edge_padding)
    overlay = DropOverlay()

    def on_pick(item):
        ctl.reserve_timeout(LAUNCH_TIMEOUT)       # keep the slot open while the app starts
        launch(item)

    menu = QuickMenu(load_menu_items(), on_pick=on_pick, on_cancel=lambda: ctl.release(), set_blur=xwm.set_blur_region)

    def open_menu(idx, x, y, above=False):
        if ctl.drag or ctl.resizing is not None:
            return
        ctl.reserve(idx)                          # open the slot now: shows where the window will go
        state["menu_armed"] = False               # ignore the still-held right button
        menu.popup(x, y, above)

    tab_server = None
    if args.chrome_tabs != "off":
        tab_server = chrometabs.TabServer(port=args.chrome_port, on_change=lambda: GLib.idle_add(refresh_tabs))
        if not tab_server.start():
            print(f"sdesk: port {args.chrome_port} is taken; Chrome tabs are not shown", file=sys.stderr)
            tab_server = None

    def refresh_tabs():
        bar = state.get("bar")
        if bar is not None:
            bar.refresh()
        return False

    def open_tab(wid, window_id, tab):
        """A tab button of another tab: tell Chrome which tab, and bring its window to the middle."""
        if tab_server is not None:
            tab_server.send({"cmd": "activate", "tabId": tab["id"], "windowId": window_id})
        ctl.focus(wid)

    thread_tracker = threads.Tracker()

    def refresh_threads():
        """Read the states the programs keep (every 3 s) and show them on the bar. Never raises: a broken read shows nothing."""
        try:
            state["threads"] = threads.poll(thread_tracker)
        except Exception:
            traceback.print_exc()
            return True
        bar = state.get("bar")
        if bar is not None:
            bar.set_threads(state["threads"])
        return True

    def thread_seen(source):
        """A box with thread badges was clicked: its done threads count as seen."""
        thread_tracker.acknowledge(source)
        refresh_threads()

    sound_on = args.sound != "off" and audio.available()
    watcher = None

    def current_sink(st):
        return next((s for s in st[0] if s.name == st[1]), None) if st else None

    def apply_audio(st):
        state["audio"] = st
        bar = state.get("bar")
        if bar is not None:
            bar.set_sound(current_sink(st))
        return False

    def open_sound_menu(x, y):
        """Right-click on the sound box: every output, the current one marked."""
        st = state.get("audio")
        if not st or watcher is None or ctl.drag or ctl.resizing is not None:
            return
        icons = {"headset": "audio-headphones", "display": "video-display", "speakers": "audio-speakers"}
        items = [MenuItem(s.label, "Current output" if s.name == st[1] else s.description, "", icons.get(s.kind, "audio-card"),
                          (lambda n=s.name: watcher.choose(n))) for s in st[0]]
        state["menu_armed"] = False
        menu.popup(x, y, above=True, items=items, header="SOUND OUTPUT", on_pick=lambda _i: None, on_cancel=lambda: None)

    def open_window_menu(wid, x, y):
        """Right-click on a taskbar button: Close / Force Quit for that window."""
        if ctl.drag or ctl.resizing is not None:
            return
        info = xwm.info(wid)
        items = window_menu_items(info.wm_class, len(xwm.app_windows(wid)),
                                  close=lambda: xwm.close(wid), force_quit=lambda: xwm.force_quit(wid))
        state["menu_armed"] = False
        menu.popup(x, y, above=True, items=items, header=(info.title or info.wm_class or "Window").upper(),
                   on_pick=lambda _i: None, on_cancel=lambda: None)

    state = {"audio": None, "menu_armed": False, "refresh_timer": 0, "ticking": False, "polls": 0, "title_timer": 0, "quitting": False}

    def guarded(fn, default):
        """Run a timer/event callback; a bug in one frame must not kill the timer."""
        def run(*a):
            try:
                return fn(*a)
            except Exception:
                traceback.print_exc()
                return default
        return run

    def tick():
        try:
            moving = ctl.tick()
        except Exception:
            traceback.print_exc()
            moving = False
        state["ticking"] = moving       # never leave the flag stuck on
        return moving

    def request_tick():
        if not state["ticking"]:
            state["ticking"] = True
            GLib.timeout_add(16, tick)

    def refresh_titles(*_):
        state["title_timer"] = 0
        state["bar"].refresh()
        return False

    def on_title(_wid):
        if not state["title_timer"]:
            state["title_timer"] = GLib.timeout_add(150, refresh_titles)

    def poll():
        xwm.pump()
        ctl.poll()
        if menu.is_open():                        # a click anywhere outside the menu dismisses it
            px, py, down = xwm.pointer()
            if not down:
                state["menu_armed"] = True        # the click that opened the menu has been released
            elif state["menu_armed"] and not menu.contains(px, py):
                menu.cancel()
        state["polls"] += 1
        if state["polls"] % 4 == 0:
            ctl.reconcile()
        if state["polls"] % 10 == 0:
            ctl.refresh_zones()                   # a tool window or dialog over a gap may have moved: keep its clicks
        if state["polls"] % 20 == 0:
            ctl.update_inset()                    # a floating sidebar may have appeared, moved or been resized
            fade.keep_stacked()                   # and kwin may have stacked something above the cover
        if state["polls"] % 1000 == 0:
            fade.refresh()                        # roughly every 30 s: follow a changed wallpaper
        return True

    poll = guarded(poll, True)

    ctl.request_tick = request_tick
    ctl.on_change = lambda: state["bar"].refresh()
    ctl.on_indicator = overlay.show_rect
    ctl.on_zones = xwm.set_zones
    ctl.on_area = lambda a: state["bar"].set_center(a.x + a.w / 2)
    soft_px = 0 if args.edge_fade == "off" else (0 if args.edge_fade == "auto" else int(args.edge_fade))
    fade = EdgeFade(xwm, fade_px, soft_px, exclude=bar_ids)
    ctl.edge_extra = soft_px if fade.active else 0        # room beside the gap for the soft edge: the last window is never faded
    ctl.on_sidebar = fade.update
    xwm.on_zone_menu = open_menu

    def resize_from_gap(idx, x, y):
        target = ctl.resize_target(idx)
        if target is not None:
            wid, side = target
            xwm.start_resize(wid, 7 if side == "left" else 3, x, y)    # _NET_WM_MOVERESIZE_SIZE_LEFT / _RIGHT
    xwm.on_zone_resize = resize_from_gap

    def make_bar():
        """A new taskbar window. Its look depends on whether a compositor runs, and a window's visual cannot change
        afterwards, so switching Deepin's window effects on/off means building a new one (see rebuild_bar)."""
        b = Taskbar(ctl, xwm, height=args.bar_height, position=args.bar, gap=args.bar_gap, sound=sound_on,
                    battery=args.battery != "off", nag_after=args.nag_after * 60, nag_every=max(1, args.nag_every) * 60)
        b.set_center(ctl.area.x + ctl.area.w / 2)
        b.on_menu = lambda idx, x, y: open_menu(idx, x, y, above=True)
        b.on_window_menu = open_window_menu
        b.tab_source = tab_server
        b.on_tab_click = open_tab
        b.on_thread_seen = thread_seen
        b.on_sound_click = lambda: watcher.cycle() if watcher else None
        b.on_sound_menu = open_sound_menu
        b.on_volume = lambda v: watcher.set_volume(v) if watcher else None
        b.on_mute = lambda: watcher.toggle_mute() if watcher else None
        return b

    def finish_bar(b):
        bar_ids.add(b.xid())
        b.place()
        b.reserve_space()
        b.refresh()
        b.set_threads(state.get("threads", {}))
        if state["audio"]:
            b.set_sound(current_sink(state["audio"]))
        if state.get("battery"):
            b.set_battery(state["battery"])
        GLib.timeout_add(2500, b.refresh_glass)      # without a compositor: copy the wallpaper once windows have settled
        return False

    def rebuild_bar():
        old = state["bar"]
        bar_ids.discard(old.xid())
        old.shutdown()
        state["bar"] = make_bar()
        state["bar"].show_all()
        GLib.timeout_add(250, finish_bar, state["bar"])
        ctl.set_edge_extra(fade.fade if fade.active else 0)     # the cover only exists with a compositor
        fade.update(ctl.sidebar)
        print(f"sdesk: window effects changed, taskbar rebuilt "
              f"({'with' if state['bar'].composited else 'without'} compositor)", flush=True)
        return False

    state["bar"] = make_bar()
    if args.threads != "off":
        refresh_threads()
        GLib.timeout_add_seconds(3, refresh_threads)
    if args.battery != "off":
        def apply_battery(readings):
            state["battery"] = readings
            if state.get("bar") is not None:
                state["bar"].set_battery(readings)
            return False
        state["battery_poller"] = battery.Poller(lambda readings: GLib.idle_add(apply_battery, readings))
        state["battery_poller"].start()
    if sound_on:
        watcher = audio.Watcher(lambda st: GLib.idle_add(apply_audio, st), match=args.sound_match)
        watcher.start()
    Gdk.Screen.get_default().connect("composited-changed", lambda *_: GLib.timeout_add(700, rebuild_bar) and None)
    def run_refresh():
        state["refresh_timer"] = 0
        ctl.refresh_windows()
        desktop = xwm.current_desktop()
        if desktop != state.get("desktop", desktop):          # another virtual desktop may have another wallpaper
            GLib.timeout_add(400, state["bar"].refresh_glass)
            GLib.timeout_add(500, fade.refresh)
        state["desktop"] = desktop
        return False

    def schedule_refresh():
        """Coalesce bursts (Show Desktop hides/restores every window within a few ms) into one relayout."""
        if not state["refresh_timer"]:
            state["refresh_timer"] = GLib.timeout_add(80, run_refresh)

    xwm.on_clients = schedule_refresh
    xwm.on_active = ctl.active_changed
    xwm.on_workarea = ctl.layout
    xwm.on_title = on_title
    xwm.on_scroll = ctl.scroll_step_dir
    GLib.io_add_watch(xwm.fileno(), GLib.IO_IN, guarded(lambda *_: xwm.pump() or True, True))
    GLib.timeout_add(30, poll)

    def shutdown(*_):
        if state["quitting"]:
            return False
        state["quitting"] = True
        control.stop()
        fade.shutdown()
        if tab_server is not None:
            tab_server.stop()
        if watcher is not None:
            watcher.stop()
        if state.get("battery_poller") is not None:
            state["battery_poller"].stop()
        ending = safety.session_ending()           # logout/reboot: nothing may block, the session is going away anyway
        ctl.restore_all()
        xwm.d.sync()
        dock.restore("never" if ending else "no-block")
        safety.mark_clean()                        # tells the guardian there is nothing left to undo
        Gtk.main_quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGHUP, shutdown)      # terminal closed

    control = ipc.Server(lambda req: ipc.handle(req, ctl.snapshot, ctl.focus, focused=xwm.active),
                         run_on_main=lambda fn: GLib.idle_add(fn))
    try:
        control.start()                      # lets the launcher list and focus windows
    except OSError as e:
        print(f"sdesk: control socket not available ({e})", file=sys.stderr)
    safety.begin()
    saved_minimized = safety.load_minimized()       # when the windows that are still minimised went into the taskbar
    ctl.on_minimized = safety.save_minimized
    ctl.on_originals = lambda d: safety.save_originals({w: (r.x, r.y, r.w, r.h) for w, r in d.items()})
    if args.disable_dock:
        if not dock.disable():
            dock.hide()                      # fall back to the weaker option
    elif args.hide_dock:
        dock.hide()
    safety.spawn_guardian()                  # from here on a hard kill of sdesk cannot leave the desktop broken

    state["bar"].show_all()

    def after_mapped():
        finish_bar(state["bar"])
        if not xwm.grab_super_scroll():
            print("sdesk: Super+scroll is already taken by another program; "
                  "the taskbar still works", file=sys.stderr)
        ctl.refresh_windows()
        ctl.adopt_minimized(saved_minimized)
        safety.save_minimized(ctl.hidden_since)
        state["bar"].refresh()
        return False

    GLib.timeout_add(250, after_mapped)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: (state["bar"].refresh_glass(), fade.refresh()) and True)  # wallpaper changed
    print("sdesk running. Super+scroll scrolls; drag windows or taskbar buttons to reorder; "
          "right-click between windows (or buttons) to add one; Ctrl+C quits.")
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
