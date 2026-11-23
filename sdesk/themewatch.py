"""Follow ``~/.config/slopslide/theme.json`` live (issue #22): watch the folder, not the file, so the launcher's
atomic write (tmp + rename) is seen too. A burst of events gives one reload, 50 ms after the last one."""
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import theme as themes  # noqa: E402

DEBOUNCE_MS = 50
WATCHED = (themes.CHOICE_FILE, themes.CATALOGUE_FILE)


class ThemeWatcher:
    """Calls ``on_change(theme)`` on the GLib main loop whenever the choice (or the catalogue copy) changes."""

    def __init__(self, on_change, folder=None, catalogue_path=None):
        self.on_change = on_change
        self.folder = folder or themes.config_dir()
        self.catalogue_path = catalogue_path
        self._monitor = None
        self._timer = 0
        self._last = None

    def start(self):
        os.makedirs(self.folder, exist_ok=True)
        self._monitor = Gio.File.new_for_path(self.folder).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._monitor.connect("changed", self._on_event)
        self._last = self.load()
        return self._last

    def load(self):
        return themes.load(self.folder, self.catalogue_path)

    def stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None

    def _on_event(self, _mon, file, other, _event):
        names = {os.path.basename(f.get_path()) for f in (file, other) if f is not None and f.get_path()}
        if not names & set(WATCHED):
            return
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(DEBOUNCE_MS, self._reload)

    def _reload(self):
        self._timer = 0
        t = self.load()
        key = (t.id, t.accent_id)
        if self._last is None or key != (self._last.id, self._last.accent_id):
            self._last = t
            self.on_change(t)
        return False
