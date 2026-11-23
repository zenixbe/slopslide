"""The "add a window here" menu: a glass card in the style of the taskbar."""
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, Gtk, Pango  # noqa: E402

from . import favicons  # noqa: E402
from .shapes import rounded_rects  # noqa: E402

CONFIG_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                           "sdesk", "menu.json")


@dataclass
class MenuItem:
    title: str
    description: str
    exec: str
    icon: str = "application-x-executable"
    action: Optional[Callable[[], None]] = None      # if set, picking the item calls this instead of launching ``exec``


# `exec` may list fallbacks with ||; an entry is only offered if one of them is installed
DEFAULT_ITEMS = [
    MenuItem("Chrome", "New browser window",
             "google-chrome-stable --new-window || google-chrome --new-window || x-www-browser", "google-chrome"),
    MenuItem("File Manager", "New file manager window",
             "dde-file-manager -n || xdg-open ~", "system-file-manager"),
    MenuItem("Terminal", "New terminal window",
             "deepin-terminal || x-terminal-emulator", "utilities-terminal"),
]


def _installed(command):
    return any(shutil.which(alt.split()[0]) for alt in command.split("||") if alt.strip())


def load_menu_items(path=CONFIG_PATH):
    """Items from ~/.config/sdesk/menu.json if present, else the defaults that are installed."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                raw = json.load(f)["items"]
            return [MenuItem(i["title"], i.get("description", ""), i["exec"],
                             i.get("icon", "application-x-executable")) for i in raw]
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"sdesk: ignoring {path}: {e}")
    return [i for i in DEFAULT_ITEMS if _installed(i.exec)]


def app_name(wm_class):
    """'com.anthropic.Claude' -> 'Claude', 'google-chrome' -> 'Google Chrome'."""
    name = (wm_class or "").split(".")[-1].replace("-", " ").replace("_", " ").strip()
    return name.title() if name.islower() else (name or "this app")


_PROGRAMS = None


def _program_names():
    """{lower-case wm class or desktop id: the name the application menu shows}, read once from the .desktop files."""
    global _PROGRAMS
    if _PROGRAMS is None:
        _PROGRAMS = {}
        try:
            for info in Gio.AppInfo.get_all():
                name = info.get_name()
                keys = [(info.get_id() or "").removesuffix(".desktop")]
                if isinstance(info, Gio.DesktopAppInfo):
                    keys.append(info.get_startup_wm_class() or "")
                for key in keys:
                    if key:
                        _PROGRAMS.setdefault(key.lower(), name)
        except Exception:                      # no desktop files readable: fall back to the guess below
            pass
    return _PROGRAMS


def program_name(wm_class, lookup=None):
    """The program's name for a taskbar box: from its .desktop file ('Google Chrome', 'Slack'), else made from the window
    class ('com.anthropic.Claude' -> 'Claude')."""
    names = _program_names() if lookup is None else lookup
    raw = (wm_class or "").strip()
    for key in (raw.lower(), raw.split(".")[-1].lower()):
        if key and key in names:
            return names[key]
    return app_name(raw)


CHROME_LABEL_MAX = 15       # characters of a Chrome tab title in its taskbar box, the ellipsis included


def shorten(text, limit=CHROME_LABEL_MAX):
    """At most ``limit`` characters: a longer text loses its end for an ellipsis ('Dashboard ‹ Ellen' -> 'Dashboard ‹ El…')."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


FILE_MANAGERS = ("dde-file-manager", "nautilus", "thunar", "dolphin", "nemo", "pcmanfm", "pcmanfm-qt", "caja", "files")


def window_label(wm_class, title):
    """What a taskbar box says: the program's name, except for a file manager, whose title is the folder it shows
    ('smoor — File Manager' -> 'smoor'), and for Chrome, whose title is the page of the tab it shows, cut to 15 characters."""
    name = program_name(wm_class)
    if favicons.is_chrome(wm_class):                       # a Chrome window: the tab it shows, shortened
        return shorten(favicons.page_title(title)) or name
    if (wm_class or "").split(".")[-1].lower() in FILE_MANAGERS and (title or "").strip():
        text = title.strip()
        for suffix in {name, "File Manager", "Files"}:
            for sep in (" — ", " – ", " - "):
                if text.endswith(sep + suffix) and len(text) > len(sep + suffix):
                    return text[:-len(sep + suffix)].strip()
        return text
    return name


def window_menu_items(wm_class, n_windows, close, force_quit):
    """Right-click menu of a taskbar button."""
    app = app_name(wm_class)
    if n_windows > 1:
        warn = f"Kill {app} at once: all {n_windows} of its windows close, unsaved work is lost"
    else:
        warn = f"Kill {app} immediately; unsaved work is lost"
    return [
        MenuItem("Close", "Ask the window to close, like its × button", "", "window-close", close),
        MenuItem("Force Quit", warn, "", "process-stop", force_quit),
    ]


def launch(item):
    """Run the item detached from sdesk, so it survives sdesk quitting."""
    subprocess.Popen(item.exec, shell=True, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Glass in the style of the taskbar (sdesk/taskbar.py): dark translucent card that kwin blurs behind, 6 px corners,
# hairline edges, and teal ACCENT (20,184,166) / ACCENT_EDGE (115,237,217) for the selected row.
CSS = b"""
window.sdesk-menu { background-color: transparent; }
.sdesk-card {
    background-color: rgba(14,19,24,0.74); border-radius: 6px; border: 1px solid rgba(255,255,255,0.20);
    box-shadow: 0 10px 30px rgba(0,0,0,0.35); padding: 6px;
}
.sdesk-card.flat { background-color: rgb(24,29,34); border-radius: 0; box-shadow: none; }
.sdesk-header { padding: 8px 10px 6px 10px; }
.sdesk-header-title { font-size: 11px; font-weight: 700; letter-spacing: 1px; color: rgba(255,255,255,0.62); }
list.sdesk-items { background-color: transparent; }
list.sdesk-items row {
    background-color: transparent; border-radius: 6px; padding: 8px 12px; margin: 1px 0;
    border: 1px solid transparent;
}
list.sdesk-items row:hover { background-color: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.16); }
list.sdesk-items row:selected { background-color: rgba(20,184,166,0.62); border-color: rgba(115,237,217,0.95); }
.sdesk-item-title {
    font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.92); text-shadow: 0 1px 0 rgba(0,0,0,0.45);
}
list.sdesk-items row:selected .sdesk-item-title { color: #ffffff; }
.sdesk-item-desc { font-size: 11px; color: rgba(255,255,255,0.62); margin-top: 1px; }
list.sdesk-items row:selected .sdesk-item-desc { color: rgba(255,255,255,0.85); }
.sdesk-badge {
    font-family: monospace; font-size: 11px; font-weight: bold; color: rgba(255,255,255,0.62);
    background-color: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.16);
    border-radius: 4px; padding: 1px 6px; margin-left: 8px;
}
list.sdesk-items row:selected .sdesk-badge {
    background-color: rgba(255,255,255,0.18); border-color: rgba(255,255,255,0.35); color: #ffffff;
}
.sdesk-footer { padding: 9px 8px 3px 8px; }
"""


class _Row(Gtk.ListBoxRow):
    def __init__(self, item, index):
        super().__init__()
        self.item = item
        hbox = Gtk.Box(spacing=12)
        for m in ("start", "end", "top", "bottom"):
            getattr(hbox, f"set_margin_{m}")(4)
        theme = Gtk.IconTheme.get_default()
        img = Gtk.Image.new_from_icon_name(
            item.icon if theme.has_icon(item.icon) else "application-x-executable", Gtk.IconSize.DND)
        img.set_pixel_size(24)
        img.set_valign(Gtk.Align.CENTER)
        hbox.pack_start(img, False, False, 0)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=item.title, xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.get_style_context().add_class("sdesk-item-title")
        vbox.pack_start(title, False, False, 0)
        if item.description:
            desc = Gtk.Label(label=item.description, xalign=0)
            desc.set_ellipsize(Pango.EllipsizeMode.END)
            desc.get_style_context().add_class("sdesk-item-desc")
            vbox.pack_start(desc, False, False, 0)
        hbox.pack_start(vbox, True, True, 0)
        if index < 9:
            badge = Gtk.Label(label=str(index + 1))
            badge.set_valign(Gtk.Align.CENTER)
            badge.get_style_context().add_class("sdesk-badge")
            hbox.pack_end(badge, False, False, 0)
        self.add(hbox)


def _key(text):
    return f'<span size="small" foreground="#ffffff" alpha="62%" weight="bold">{text}</span>'


def _word(text):
    return f'<span size="small" foreground="#ffffff" alpha="42%">{text}</span>'


class QuickMenu(Gtk.Window):
    """Keyboard-driven popup: ↑↓ / j k / Ctrl+P N, Enter, 1-9, Esc; closes when focus is lost."""

    WIDTH = 360

    def __init__(self, items, on_pick, on_cancel, header="ADD WINDOW HERE", set_blur=None):
        """``set_blur(xid, rects)`` asks kwin to blur what is behind the card (the glass), like the taskbar does."""
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._set_blur, self._blur_sig = set_blur, None
        self.items, self.on_pick, self.on_cancel, self.header = items, on_pick, on_cancel, header
        self._default = (items, on_pick, on_cancel, header)
        self._open = False
        self.set_title("sdesk menu")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)     # not NORMAL: sdesk never tiles it
        self.get_style_context().add_class("sdesk-menu")
        self.set_app_paintable(True)
        screen = self.get_screen()
        composited = bool(screen.get_rgba_visual() and screen.is_composited())
        if composited:
            self.set_visual(screen.get_rgba_visual())

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.get_style_context().add_class("sdesk-card")
        if not composited:
            card.get_style_context().add_class("flat")
        card.set_size_request(self.WIDTH, -1)
        if composited and set_blur:
            card.connect("size-allocate", self._on_card_alloc)

        header = Gtk.Box()
        header.get_style_context().add_class("sdesk-header")
        self.title_label = Gtk.Label(label=header, xalign=0)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.get_style_context().add_class("sdesk-header-title")
        header.pack_start(self.title_label, True, True, 0)
        card.pack_start(header, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.get_style_context().add_class("sdesk-items")
        self.listbox.connect("row-activated", lambda _l, row: self._pick(row.item))
        self._fill(items)
        card.pack_start(self.listbox, True, True, 0)

        footer = Gtk.Box()
        footer.get_style_context().add_class("sdesk-footer")
        hint = Gtk.Label(xalign=0)
        hint.set_markup(
            f'{_key("↑↓")} {_word("Navigate")}    {_key("⏎")} {_word("Open")}    {_key("Esc")} {_word("Close")}')
        footer.pack_start(hint, True, True, 0)
        card.pack_start(footer, False, False, 0)
        self.add(card)

        self.connect("key-press-event", self._on_key)
        self.connect("focus-out-event", lambda *_: self.cancel() or False)
        self.connect("delete-event", lambda *_: self.cancel() or True)
        self.realize()

    def _on_card_alloc(self, _card, a):
        sig = (a.x, a.y, a.width, a.height)
        win = self.get_window()
        if win is None or sig == self._blur_sig:
            return
        self._blur_sig = sig
        try:
            self._set_blur(win.get_xid(), rounded_rects(a.x, a.y, a.width, a.height, 6))
        except Exception:                             # blur is decoration; never let it break the menu
            pass

    # ----------------------------------------------------------------- show
    def _fill(self, items):
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        for i, item in enumerate(items):
            self.listbox.add(_Row(item, i))
        self.listbox.show_all()

    def popup(self, x, y, above=False, items=None, header=None, on_pick=None, on_cancel=None):
        """Open near screen point (x, y); ``above`` puts the menu's bottom edge at y.

        ``items`` / ``header`` / callbacks override the defaults for this one popup (e.g. the window menu).
        """
        d_items, d_pick, d_cancel, d_header = self._default
        self.items = items if items is not None else d_items
        self.on_pick = on_pick or d_pick
        self.on_cancel = on_cancel or d_cancel
        self.title_label.set_text(header if header is not None else d_header)
        if not self.items:
            self.on_cancel()
            return
        self._fill(self.items)
        self.resize(self.WIDTH, 1)                         # shrink back after a longer menu
        self._open = True
        self.listbox.select_row(self.listbox.get_row_at_index(0))
        w, h = self.get_preferred_size()[1].width, self.get_preferred_size()[1].height
        w = max(w, self.WIDTH)
        screen = self.get_screen()
        sw, sh = screen.get_width(), screen.get_height()
        px = min(max(8, int(x - w / 2)), sw - w - 8)
        if above or y + 8 + h > sh - 8:
            py = max(8, int(y - h - 12))
        else:
            py = int(y + 8)
        self.move(px, py)
        self.show_all()
        self.present_with_time(Gdk.CURRENT_TIME)

    def is_open(self):
        return self._open

    def contains(self, x, y):
        ox, oy = self.get_position()
        return ox <= x < ox + self.get_allocated_width() and oy <= y < oy + self.get_allocated_height()

    def cancel(self):
        if self._open:
            self._open = False
            self.hide()
            self.on_cancel()

    def _pick(self, item):
        self._open = False
        self.hide()
        if item.action:
            item.action()
        else:
            self.on_pick(item)

    # ------------------------------------------------------------- keyboard
    def _move(self, step):
        rows = self.listbox.get_children()
        cur = self.listbox.get_selected_row()
        i = rows.index(cur) if cur in rows else -1
        self.listbox.select_row(rows[(i + step) % len(rows)])

    def _on_key(self, _w, ev):
        k, ctrl = ev.keyval, bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(ev.state & Gdk.ModifierType.MOD1_MASK)
        if k == Gdk.KEY_Escape:
            self.cancel()
        elif k == Gdk.KEY_Up or (ctrl and k in (Gdk.KEY_p, Gdk.KEY_P)) or (not ctrl and not alt and k == Gdk.KEY_k):
            self._move(-1)
        elif k == Gdk.KEY_Down or (ctrl and k in (Gdk.KEY_n, Gdk.KEY_N)) or (not ctrl and not alt and k == Gdk.KEY_j):
            self._move(1)
        elif k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_ISO_Enter):
            row = self.listbox.get_selected_row()
            if row:
                self._pick(row.item)
        elif not ctrl and not alt and (Gdk.KEY_1 <= k <= Gdk.KEY_9 or Gdk.KEY_KP_1 <= k <= Gdk.KEY_KP_9):
            n = k - Gdk.KEY_1 if k <= Gdk.KEY_9 else k - Gdk.KEY_KP_1
            if n < len(self.items):
                self._pick(self.items[n])
        else:
            return False
        return True
