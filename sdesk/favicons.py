"""The favicon of the tab a Chrome window shows, for its taskbar box. No GTK in here.

Chrome tells the window manager only its own logo, never the page's icon. Its profile does hold the icons, though: the
window title is the title of the active tab, ``History`` maps a page title to a URL, and ``Favicons`` maps that URL to a PNG.
So the lookup is title -> latest visit with that title -> its icon, read from the profile's SQLite files without ever writing
to them.

It is a good guess, not a certainty: two tabs with the same title give the icon of the one visited last, and a page that is
brand new has no history row yet (the box keeps Chrome's logo until it has).
"""
import glob
import os
import re
import sqlite3
import time
from urllib.parse import urlsplit

PROFILE_GLOBS = ("~/.config/google-chrome/*/Favicons", "~/.config/chromium/*/Favicons")
CHROME_CLASSES = ("google-chrome", "google-chrome-beta", "google-chrome-unstable", "chromium", "chromium-browser")
_SUFFIX = re.compile(r"\s+-\s+(Google Chrome|Chromium)(\s+-\s+.+)?$")
MISS_TTL = 6.0                      # a title without icon is looked for again after this many seconds (history lags)


def is_chrome(wm_class):
    return (wm_class or "").lower() in CHROME_CLASSES


def page_title(window_title):
    """'Inbox (3) - Gmail - Google Chrome' -> 'Inbox (3) - Gmail' ('... - Google Chrome - Work' too)."""
    return _SUFFIX.sub("", window_title or "").strip()


def _ro(path):
    """Read-only, ignoring Chrome's lock: a copy is not needed and we never change the file."""
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, timeout=0.2)


def _icon_for_url(f, url):
    """The biggest icon stored for this page, else for any page of the same site (an open Favicons connection)."""
    q = ("SELECT fb.image_data FROM icon_mapping im JOIN favicon_bitmaps fb ON fb.icon_id = im.icon_id "
         "WHERE im.page_url {} ? ORDER BY fb.width DESC LIMIT 1")
    hit = f.execute(q.format("="), (url,)).fetchone()
    if not hit:                                                  # another page of the same site has the icon
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            hit = f.execute(q.format("LIKE"), (f"{parts.scheme}://{parts.netloc}/%",)).fetchone()
    return bytes(hit[0]) if hit and hit[0] else None


def _lookup(profile_dir, title=None, url=None):
    """PNG bytes of the icon for a page in one profile: given its URL, or given its title (then the latest visit with that
    title is looked up in the history first). None if there is none."""
    fav, hist = os.path.join(profile_dir, "Favicons"), os.path.join(profile_dir, "History")
    if not os.path.exists(fav) or (url is None and not os.path.exists(hist)):
        return None
    h = f = None
    try:
        f = _ro(fav)
        if url is None:
            h = _ro(hist)
            row = h.execute("SELECT url FROM urls WHERE title = ? ORDER BY last_visit_time DESC LIMIT 1", (title,)).fetchone()
            if not row:
                return None
            url = row[0]
        return _icon_for_url(f, url)
    except sqlite3.Error:
        return None
    finally:
        for c in (h, f):
            if c is not None:
                c.close()


class Chrome:
    """Title -> favicon PNG, with a small cache (a miss expires quickly, a hit stays)."""

    def __init__(self, profile_dirs=None, clock=time.monotonic):
        self._dirs = profile_dirs
        self.clock = clock
        self._cache = {}                                          # title -> (png or None, time looked)

    def profile_dirs(self):
        if self._dirs is not None:
            return list(self._dirs)
        dirs = []
        for pattern in PROFILE_GLOBS:
            dirs += [os.path.dirname(p) for p in glob.glob(os.path.expanduser(pattern))]
        return dirs

    def _cached(self, key, find):
        png, when = self._cache.get(key, (None, None))
        if when is not None and (png is not None or self.clock() - when < MISS_TTL):
            return png
        png = None
        for d in self.profile_dirs():
            png = find(d)
            if png:
                break
        if len(self._cache) > 400:
            self._cache.clear()
        self._cache[key] = (png, self.clock())
        return png

    def icon_png(self, window_title):
        """The icon of the page a window title (of a Chrome window) names."""
        title = page_title(window_title)
        return self._cached(("title", title), lambda d: _lookup(d, title=title)) if title else None

    def icon_png_for_url(self, url):
        """The icon of a page whose URL is known (a tab reported by the extension)."""
        if not url or not url.startswith(("http://", "https://")):
            return None
        return self._cached(("url", url), lambda d: _lookup(d, url=url))
