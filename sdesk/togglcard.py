"""The Toggl card that opens above the timer in the taskbar: pick and start a timer, or change / stop the running one.

Same dark card as the "add a window" menu (menu.py). Two faces:

* picker (no timer): a search field; "start again" rows (recent entries, one per project + description) and the projects
  that match; then the chosen project, a description field and Start;
* running: project, start time and the running time; the description (editable once Second Brain has
  ``PATCH /current``); Stop, "Back to <previous>" (one click, extra 8) and Save.

The card never talks to the network: it calls ``on_start`` / ``on_stop`` / ``on_save`` and gets the result back through
:meth:`TogglCard.update` (a :class:`toggl.State`).
"""
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import menu, toggl  # noqa: E402
from . import theme as themes  # noqa: E402

PATCH_TIP = "needs Second Brain update (PATCH /api/ws/toggl/current)"
MAX_PROJECTS = 7

def css(t=None):
    """The card's own CSS (on top of the menu's) for theme ``t``. With "Donker glas" and Teal this is exactly the CSS
    from before #22 (tests/golden/toggl_css_glass_dark.css)."""
    t = t or themes.current()
    R, H = themes.css_rgba, themes.css_hex
    t1, t2, t3, t4 = (H(c) for c in t.card_text)
    bw = t.bw
    return f"""
.sdesk-toggl-search, .sdesk-toggl-desc {{
    background-color: {R(t.tint(0.06))}; color: {t1}; border-radius: 8px;
    border: {bw}px solid {R(t.line(0.14))}; padding: 6px 10px; box-shadow: none; caret-color: {H(t.accent_edge)};
}}
.sdesk-toggl-search:focus, .sdesk-toggl-desc:focus {{ border-color: {R(t.accent_edge, 0.85)}; }}
.sdesk-toggl-desc:disabled, .sdesk-toggl-desc.readonly, .sdesk-toggl-desc.readonly:focus {{
    color: {t3}; background-color: {R(t.tint(0.03))}; border-style: dashed; border-color: {R(t.line(0.18))};
}}
.sdesk-toggl-section {{ font-size: 10px; font-weight: 700; letter-spacing: 1px; color: {t3}; padding: 8px 12px 2px 12px; }}
.sdesk-toggl-chip {{
    font-size: 12px; font-weight: 600; color: {t2}; background-color: {R(t.tint(0.08))};
    border: {bw}px solid {R(t.line(0.14))}; border-radius: 6px; padding: 3px 8px;
}}
.sdesk-toggl-chip.empty {{ color: {t4}; font-weight: 400; }}
.sdesk-toggl-big {{ font-size: 22px; font-weight: 700; color: {t1}; font-feature-settings: "tnum"; }}
.sdesk-toggl-big.forgot {{ color: {H(t.danger)}; }}
.sdesk-toggl-dim {{ font-size: 11px; color: {t3}; }}
.sdesk-toggl-error {{ font-size: 11px; color: {H(t.danger_soft)}; padding: 4px 12px 0 12px; }}
.sdesk-toggl-warn {{
    font-size: 12px; font-weight: 600; color: {H(t.danger_ink)}; background-color: {R(t.danger_fill, t.fill(0.22))};
    border: {bw}px solid {R(t.danger, 0.5)}; border-radius: 8px; padding: 6px 10px; margin: 2px 0 6px 0;
}}
button.sdesk-toggl-btn {{
    background-image: none; background-color: {R(t.tint(0.08))}; color: {t2}; text-shadow: none;
    border: {bw}px solid {R(t.line(0.14))}; border-radius: 8px; padding: 4px 12px; box-shadow: none;
}}
button.sdesk-toggl-btn:hover {{ background-color: {R(t.tint(0.14))}; }}
button.sdesk-toggl-btn.go {{ background-color: {R(t.accent, t.fill(0.85))}; border-color: {R(t.accent_edge, 0.9)}; color: {H(t.on_accent) if t.on_accent[0] < 0.5 else "#fff"}; }}
button.sdesk-toggl-btn.go:hover {{ background-color: {R(t.accent, 1)}; }}
button.sdesk-toggl-btn.stop {{ background-color: {R(t.danger_deep, 0.75)}; border-color: {R(t.danger, 0.6)}; color: {H(t.danger_pale)}; }}
button.sdesk-toggl-btn:disabled {{ opacity: 0.45; }}
list.sdesk-items row.sdesk-toggl-row {{ padding: 5px 12px; margin: 1px 0; }}
list.sdesk-items row.sdesk-toggl-row .sdesk-item-title {{ font-size: 13px; }}
""".encode()


_provider = None


def install_css(t=None):
    """The menu CSS plus the card's own, in screen-wide providers (loaded again on a theme switch)."""
    global _provider
    menu.install_css(t)
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), _provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _provider.load_from_data(css(t))


def when_text(epoch, now=None):
    """"today 9:30", "yesterday", "Wed 18 Nov": when a recent entry ran."""
    if not epoch:
        return ""
    now = time.time() if now is None else now
    d, today = time.localtime(epoch), time.localtime(now)
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(d[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    if round(days) == 0:
        return "today " + time.strftime("%H:%M", d)
    if round(days) == 1:
        return "yesterday"
    return time.strftime("%a %d %b", d)


class _Row(Gtk.ListBoxRow):
    """One row of the picker: a recent entry ("again") or a project, or a section title (not selectable)."""

    def __init__(self, kind, data=None, title="", detail="", badge=""):
        super().__init__()
        self.kind, self.data = kind, data
        if kind == "section":
            self.set_selectable(False)
            self.set_activatable(False)
            label = Gtk.Label(label=title, xalign=0)
            label.get_style_context().add_class("sdesk-toggl-section")
            self.add(label)
            return
        self.get_style_context().add_class("sdesk-toggl-row")
        hbox = Gtk.Box(spacing=10)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        t = Gtk.Label(label=title, xalign=0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.get_style_context().add_class("sdesk-item-title")
        vbox.pack_start(t, False, False, 0)
        if detail:
            d = Gtk.Label(label=detail, xalign=0)
            d.set_ellipsize(Pango.EllipsizeMode.END)
            d.get_style_context().add_class("sdesk-item-desc")
            vbox.pack_start(d, False, False, 0)
        hbox.pack_start(vbox, True, True, 0)
        if badge:
            b = Gtk.Label(label=badge)
            b.set_valign(Gtk.Align.CENTER)
            b.get_style_context().add_class("sdesk-badge")
            hbox.pack_end(b, False, False, 0)
        self.add(hbox)


class TogglCard(Gtk.Window):
    WIDTH = 460

    def __init__(self, on_start, on_stop, on_save, on_close=lambda: None, target_h=8.0, hotkeys=True):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_start, self.on_stop, self.on_save, self.on_close = on_start, on_stop, on_save, on_close
        self.target_h = target_h
        self.hotkeys = hotkeys
        self.state = toggl.State()
        self.mode = None                         # "pick" | "run" while open
        self._open = False
        self._project = None                     # the project chosen in the picker
        self._anchor = (0, 0)
        self._tick = 0
        self._closed_at = 0.0
        self._desc_dirty = False                 # the user typed in the running description: an update must not undo it
        self.set_title("sdesk toggl")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)          # not NORMAL: sdesk never tiles it
        self.get_style_context().add_class("sdesk-menu")
        self.set_app_paintable(True)
        screen = self.get_screen()
        composited = bool(screen.get_rgba_visual() and screen.is_composited())
        if composited:
            self.set_visual(screen.get_rgba_visual())
        install_css()

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.get_style_context().add_class("sdesk-card")
        if not composited:
            self.card.get_style_context().add_class("flat")
        self.card.set_size_request(self.WIDTH, -1)
        self.add(self.card)
        self._build_pick()
        self._build_run()
        self.error = Gtk.Label(xalign=0)
        self.error.set_line_wrap(True)
        self.error.get_style_context().add_class("sdesk-toggl-error")
        self.error.set_no_show_all(True)                    # shown only with a message
        self.card.pack_start(self.error, False, False, 0)
        footer = Gtk.Box()
        footer.get_style_context().add_class("sdesk-footer")
        self.hint = Gtk.Label(xalign=0)
        self.today = Gtk.Label(xalign=1)
        self.today.get_style_context().add_class("sdesk-toggl-dim")
        footer.pack_start(self.hint, True, True, 0)
        footer.pack_end(self.today, False, False, 0)
        self.card.pack_end(footer, False, False, 0)

        self.connect("key-press-event", self._on_key)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("delete-event", lambda *_: self.cancel() or True)
        self.realize()

    # ------------------------------------------------------------------ build
    def _btn(self, label, cls, cb):
        b = Gtk.Button(label=label)
        b.get_style_context().add_class("sdesk-toggl-btn")
        if cls:
            b.get_style_context().add_class(cls)
        b.connect("clicked", lambda *_: cb())
        b.set_can_focus(False)
        return b

    def _build_pick(self):
        self.pick = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search a project or a recent entry")
        self.search.get_style_context().add_class("sdesk-toggl-search")
        self.search.connect("search-changed", lambda *_: self._fill())
        self.search.connect("activate", lambda *_: self._activate_selected())
        self.pick.pack_start(self.search, False, False, 2)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.get_style_context().add_class("sdesk-items")
        self.listbox.connect("row-activated", lambda _l, row: self._activate(row))
        self.pick.pack_start(self.listbox, False, False, 0)
        bottom = Gtk.Box(spacing=8)
        bottom.set_margin_top(6)
        self.chip = Gtk.Label(label="choose a project", xalign=0)
        self.chip.set_ellipsize(Pango.EllipsizeMode.END)
        self.chip.set_max_width_chars(22)
        self.chip.get_style_context().add_class("sdesk-toggl-chip")
        self.chip.get_style_context().add_class("empty")
        bottom.pack_start(self.chip, False, False, 0)
        self.new_desc = Gtk.Entry()
        self.new_desc.set_placeholder_text("What are you doing?")
        self.new_desc.get_style_context().add_class("sdesk-toggl-desc")
        self.new_desc.set_max_length(toggl.DESC_MAX)
        self.new_desc.connect("changed", lambda *_: self._sync_start())
        self.new_desc.connect("activate", lambda *_: self._start_new())
        bottom.pack_start(self.new_desc, True, True, 0)
        self.start_btn = self._btn("▶  Start", "go", self._start_new)
        bottom.pack_end(self.start_btn, False, False, 0)
        self.pick.pack_start(bottom, False, False, 0)
        self.card.pack_start(self.pick, False, False, 0)

    def _build_run(self):
        self.run = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.warn = Gtk.Label(xalign=0)
        self.warn.set_line_wrap(True)
        self.warn.get_style_context().add_class("sdesk-toggl-warn")
        self.warn.set_no_show_all(True)
        self.run.pack_start(self.warn, False, False, 0)
        top = Gtk.Box(spacing=10)
        self.run_project = Gtk.Label(xalign=0)
        self.run_project.set_ellipsize(Pango.EllipsizeMode.END)
        self.run_project.set_max_width_chars(26)
        self.run_project.get_style_context().add_class("sdesk-toggl-chip")
        top.pack_start(self.run_project, False, False, 0)
        self.run_since = Gtk.Label(xalign=0)
        self.run_since.get_style_context().add_class("sdesk-toggl-dim")
        top.pack_start(self.run_since, False, False, 0)
        self.run_clock = Gtk.Label(xalign=1)
        self.run_clock.get_style_context().add_class("sdesk-toggl-big")
        top.pack_end(self.run_clock, False, False, 0)
        self.run.pack_start(top, False, False, 0)
        self.run_desc = Gtk.Entry()
        self.run_desc.get_style_context().add_class("sdesk-toggl-desc")
        self.run_desc.set_max_length(toggl.DESC_MAX)
        self.run_desc.connect("changed", lambda *_: self._desc_changed())
        self.run_desc.connect("activate", lambda *_: self._save())
        self.run.pack_start(self.run_desc, False, False, 0)
        row = Gtk.Box(spacing=8)
        row.pack_start(self._btn("■  Stop", "stop", self._stop), False, False, 0)
        self.back_btn = self._btn("", None, self.back)
        self.back_btn.get_child().set_ellipsize(Pango.EllipsizeMode.END)
        self.back_btn.get_child().set_max_width_chars(24)
        self.back_btn.set_no_show_all(True)                 # only when there is a previous entry
        row.pack_start(self.back_btn, False, False, 0)
        self.save_btn = self._btn("Save  ⏎", "go", self._save)
        row.pack_end(self.save_btn, False, False, 0)
        self.run.pack_start(row, False, False, 0)
        self.card.pack_start(self.run, False, False, 0)

    # ------------------------------------------------------------------- show
    def popup(self, x, y, state):
        """Open with its bottom edge above screen point (x, y)."""
        self.state = state
        self._anchor = (x, y)
        self.mode = "run" if state.running else "pick"
        self._project = None
        self._desc_dirty = False
        self.search.set_text("")
        self.new_desc.set_text("")
        self.error.set_text("")
        self._open = True
        self._show_mode()
        if not self._tick:
            self._tick = GLib.timeout_add(1000, self._on_tick)

    def _show_mode(self):
        pick = self.mode == "pick"
        self.pick.set_visible(pick)
        self.pick.set_no_show_all(not pick)
        self.run.set_visible(not pick)
        self.run.set_no_show_all(pick)
        if pick:
            self._fill()
            self._set_project(None)
        else:
            self._fill_run(reset=True)
        self._refresh_common()
        self.show_all()
        self._place()
        GLib.idle_add(lambda: self._open and self._place() and False)       # again once GTK has measured it
        self.present_with_time(Gdk.CURRENT_TIME)
        (self.search if pick else self.run_desc).grab_focus()
        if not pick:
            self.run_desc.set_position(-1)

    def _place(self):
        self.resize(self.WIDTH, 1)
        w = max(self.get_preferred_size()[1].width, self.WIDTH)
        h = self.get_preferred_size()[1].height
        sw = self.get_screen().get_width()
        x, y = self._anchor
        self.move(min(max(8, int(x - w / 2)), sw - w - 8), max(8, int(y - h - 12)))

    def update(self, state):
        """New data from the Toggl thread while the card is open."""
        old, self.state = self.state, state
        if not self._open:
            return
        if bool(old.running) != bool(state.running) and not state.busy:
            self.mode = "run" if state.running else "pick"          # started or stopped (here, elsewhere or by a hotkey)
            self._desc_dirty = False
            self._show_mode()
            return
        if self.mode == "pick" and (old.projects != state.projects or old.entries != state.entries):
            self._fill()
        if self.mode == "run":
            self._fill_run(reset=not self._desc_dirty and old.running != state.running)
        self._refresh_common()

    def apply_theme(self):
        """The CSS is shared (install_css); only the footer hint is markup with its own colour."""
        if self.state is not None:
            self._refresh_common()

    def _refresh_common(self):
        s = self.state
        self.error.set_text(s.action_error or s.error or "")
        self.error.set_visible(bool(s.action_error or s.error))
        if s.busy:
            self.error.set_text({"start": "Starting…", "stop": "Stopping…", "save": "Saving…"}.get(s.busy, "…"))
            self.error.set_visible(True)
        keys = ("↑↓ choose · ⏎ start · Tab description" if self.mode == "pick"
                else "⏎ save · Ctrl+⏎ stop") + " · Esc close"
        self.hint.set_markup(f'<span foreground="{themes.css_hex(themes.current().card_text[3])}">'
                             f'{GLib.markup_escape_text(keys)}</span>')
        self._show_today()

    def _show_today(self):
        total = toggl.today_seconds(self.state.entries, self.state.running)
        target = f" / {toggl.fmt_hm(self.target_h * 3600)}" if self.target_h else ""
        self.today.set_text(f"Today {toggl.fmt_hm(total)}{target}")

    def _on_tick(self):
        if not self._open:
            self._tick = 0
            return False
        if self.mode == "run":
            self._show_clock()
        self._show_today()
        return True

    # ----------------------------------------------------------------- picker
    def _fill(self):
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        q = self.search.get_text().strip()
        again = [r for r in toggl.recent(self.state.entries, 20)
                 if toggl.match(q, r["description"], r["client"], r["project"])][:4 if not q else 3]
        projects = toggl.search_projects(self.state.projects, q, MAX_PROJECTS if q else 3)
        if again:
            self.listbox.add(_Row("section", title="START AGAIN"))
            for i, r in enumerate(again):
                detail = " · ".join(x for x in (toggl.project_label(r), when_text(r["when"]),
                                                toggl.fmt_hm(r["seconds"]) if r["seconds"] else "") if x)
                self.listbox.add(_Row("again", r, r["description"], detail, "⏎" if i == 0 else ""))
        if projects:
            self.listbox.add(_Row("section", title="PROJECTS"))
            for p in projects:
                self.listbox.add(_Row("project", p, toggl.project_label(p)))
        if not again and not projects:
            msg = ("Loading projects…" if not self.state.projects and not self.state.error
                   else "No project matches" if q else "No projects")
            self.listbox.add(_Row("section", title=msg.upper()))
        self.listbox.show_all()
        first = next((r for r in self.listbox.get_children() if r.kind != "section"), None)
        self.listbox.select_row(first)
        if self._open:
            self._place()

    def _rows(self):
        return [r for r in self.listbox.get_children() if r.kind != "section"]

    def _move(self, step):
        rows = self._rows()
        if not rows:
            return
        cur = self.listbox.get_selected_row()
        i = rows.index(cur) if cur in rows else -1
        self.listbox.select_row(rows[(i + step) % len(rows)])

    def _activate_selected(self):
        row = self.listbox.get_selected_row()
        if row is not None:
            self._activate(row)

    def _activate(self, row):
        if row.kind == "again":
            self.on_start(row.data["project_id"], row.data["description"])
            self.cancel()
        elif row.kind == "project":
            self._set_project(row.data)
            self.new_desc.grab_focus()

    def _set_project(self, p):
        self._project = p
        ctx = self.chip.get_style_context()
        if p is None:
            self.chip.set_text("choose a project")
            ctx.add_class("empty")
        else:
            self.chip.set_text(toggl.project_label(p))
            ctx.remove_class("empty")
        self._sync_start()

    def _sync_start(self):
        ok = self._project is not None and bool(self.new_desc.get_text().strip())
        self.start_btn.set_sensitive(ok)
        self.start_btn.set_tooltip_text(None if ok else "Choose a project and type what you are doing")

    def _start_new(self):
        if self._project is None:
            self.error.set_text("Choose a project first")
            self.error.set_visible(True)
            self.search.grab_focus()
            return
        desc = self.new_desc.get_text().strip()
        if not desc:
            self.error.set_text("Type what you are doing: Toggl needs a description to bill it")
            self.error.set_visible(True)
            return
        self.on_start(self._project["id"], desc)
        self.cancel()

    # ---------------------------------------------------------------- running
    def _fill_run(self, reset=False):
        r = self.state.running or {}
        self.run_project.set_text(toggl.project_label(r))
        start = toggl.parse_time(r.get("start"))
        self.run_since.set_text(f"started {time.strftime('%H:%M', time.localtime(start))}" if start else "")
        if reset:
            self.run_desc.set_text(r.get("description") or "")
            self._desc_dirty = False
        can = self.state.patch
        self.run_desc.set_editable(can)
        ctx = self.run_desc.get_style_context()
        (ctx.remove_class if can else ctx.add_class)("readonly")
        self.run_desc.set_tooltip_text(None if can else PATCH_TIP)
        self.save_btn.set_sensitive(can and self._desc_dirty)
        self.save_btn.set_tooltip_text(None if can else PATCH_TIP)
        prev = toggl.previous(self.state.entries, r)
        self.back_btn.set_visible(prev is not None)
        if prev is not None:
            self.back_btn.set_label(f"↺  Back to: {prev['description']}")
            self.back_btn.set_tooltip_text(f"Stop this timer and start {prev['description']} "
                                           f"({toggl.project_label(prev)}) again" + (" · Super+T" if self.hotkeys else ""))
        self._show_clock()

    def _show_clock(self):
        r = self.state.running
        if not r:
            return
        secs = toggl.elapsed(r)
        self.run_clock.set_text(toggl.fmt_clock(secs))
        late = toggl.forgot(r)
        ctx = self.run_clock.get_style_context()
        (ctx.add_class if late else ctx.remove_class)("forgot")
        if late:
            self.warn.set_text(f"Running for {int(secs // 3600)} h: did you forget to stop it? "
                               "Stop it, then fix the end time in Toggl.")
        self.warn.set_visible(late)

    def _desc_changed(self):
        r = self.state.running or {}
        self._desc_dirty = self.run_desc.get_text().strip() != (r.get("description") or "").strip()
        self.save_btn.set_sensitive(self.state.patch and self._desc_dirty)

    def _save(self):
        if not self.state.patch:
            self.error.set_text(PATCH_TIP)
            self.error.set_visible(True)
            return
        text = self.run_desc.get_text().strip()
        if not text:
            self.error.set_text("The description cannot be empty")
            self.error.set_visible(True)
            return
        if self._desc_dirty:
            self.on_save(text)
        self.cancel()

    def _stop(self):
        self.on_stop()
        self.cancel()

    def back(self):
        """Extra 8: start the previous entry again (Second Brain stops the running one first). False if there is none."""
        prev = toggl.previous(self.state.entries, self.state.running)
        if prev is None:
            return False
        self.on_start(prev["project_id"], prev["description"])
        self.cancel()
        return True

    # ---------------------------------------------------------------- general
    def is_open(self):
        return self._open

    def contains(self, x, y):
        ox, oy = self.get_position()
        return ox <= x < ox + self.get_allocated_width() and oy <= y < oy + self.get_allocated_height()

    def closed_recently(self, within=0.5):
        return time.monotonic() - self._closed_at < within

    def _on_focus_out(self, *_):
        """Close when another window takes the focus. Not at once: a key grab (Super+T) takes it away for a moment too."""
        GLib.timeout_add(200, lambda: (self._open and not self.is_active() and self.cancel()) and False)
        return False

    def cancel(self):
        if self._open:
            self._closed_at = time.monotonic()
            self._open = False
            self.mode = None
            self.hide()
            self.on_close()

    def _on_key(self, _w, ev):
        k = ev.keyval
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        if k == Gdk.KEY_Escape:
            self.cancel()
        elif self.mode == "run" and ctrl and k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._stop()
        elif self.mode == "pick" and self.search.has_focus() and k in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._move(-1 if k == Gdk.KEY_Up else 1)
        elif self.mode == "pick" and self.search.has_focus() and k == Gdk.KEY_Tab:
            row = self.listbox.get_selected_row()
            if row is not None and row.kind == "project":
                self._set_project(row.data)
            self.new_desc.grab_focus()
        else:
            return False
        return True
