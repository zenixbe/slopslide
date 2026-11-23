"""Themes and accent colours for the whole SlopSlide system (issue #22). Pure Python, no GTK: other programs (the Super
launcher key-nav-app, later the LLM usage sidebar) keep a copy of this file.

Two files in ``~/.config/slopslide/`` ($XDG_CONFIG_HOME):

- ``themes.json``: the catalogue (themes as tokens, accents). The source is ``sdesk/themes.json``; sdesk copies it here
  at start, so other programs need no copy of their own.
- ``theme.json``: the choice, ``{"version": 1, "theme": "glass-dark", "accent": "teal"}`` (or ``"accent": "#e11d48"``).
  The launcher writes it atomically (tmp + rename).

A missing or broken file means "Donker glas" with Teal. The accent is adjusted per theme (lightness only) until text
on a selection reaches 4.5:1 and rings and edges reach 3:1 against the glass (WCAG 2.2 AA).
"""
import colorsys
import json
import math
import os
import re
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOGUE_PATH = os.path.join(HERE, "themes.json")
CHOICE_FILE = "theme.json"
CATALOGUE_FILE = "themes.json"
DEFAULT_THEME = "glass-dark"
DEFAULT_ACCENT = "teal"
WHITE = (255.0, 255.0, 255.0)
DARK = (11.0, 16.0, 21.0)            # "dark text" on a light accent
TEXT_MIN = 4.5                       # WCAG AA, normal text
UI_MIN = 3.0                         # WCAG AA, non-text (rings, edges, slider)
STATUS = ("ok", "warn", "info")      # the sidebar's task tree uses these three for its groups (llm-usage-sidebar#36)
STATUS_DE = 20.0                     # CIEDE2000: ok, warn and info differ at least this much from each other
STATUS_HUE = 30.0                    # degrees of hue between ok, warn and info
CLASH_DE = 10.0                      # an accent closer than this to a status colour "looks the same": the picker warns

# the minimum the code needs when themes.json is missing or broken: Donker glas + Teal, as before #22
_FALLBACK = {
    "version": 1, "default_theme": DEFAULT_THEME, "default_accent": DEFAULT_ACCENT, "wall": [29, 95, 122],
    "accents": [{"id": "teal", "name": "Teal", "base": "#14b8a6", "edge": "#73edd9",
                 "exact": {"base": [20.4, 183.6, 165.75], "edge": [114.75, 237.15, 216.75]}}],
    "themes": [{"id": "glass-dark", "name": "Donker glas", "note": "", "accent": "teal", "tokens": {
        "mode": "dark", "glass": [14, 19, 24, 0.74], "glass_flat": [24, 29, 34], "bar": [10.2, 15.3, 20.4, 0.30],
        "bar_dim": [0, 0, 0, 0.16], "bar_flat": [25.5, 28.05, 35.7], "text": [255, 255, 255], "text_boost": 0,
        "shadow": [0, 0, 0], "shadow_k": 1, "tint": [255, 255, 255], "tint_k": 1, "line": [255, 255, 255],
        "line_boost": 0, "fill_boost": 0, "bw": 1, "warn": [244.8, 158.1, 10.2], "ok": [33.15, 196.35, 94.35],
        "info": [58.65, 130.05, 244.8], "danger": "#f87171", "danger_fill": "#dc2626",
        "cairo": {"danger": [247.35, 112.2, 112.2], "danger_fill": [219.3, 38.25, 38.25],
                  "ghost_flat": [25.5, 33.15, 38.25]},
        "danger_deep": [127, 29, 29], "danger_soft": "#fca5a5", "danger_ink": "#fecaca", "danger_pale": "#fee2e2",
        "forgot_text": [255, 209.1, 209.1], "muted": [191.25, 198.9, 204], "knob": [255, 255, 255],
        "card_text": ["#f1f5f9", "#e2e8f0", "#94a3b8", "#64748b"]}}],
}


# ------------------------------------------------------------------ colours
def parse_color(value):
    """'#rgb', '#rrggbb', [r, g, b] or [r, g, b, a] (r, g, b in 0-255) -> ((r, g, b), alpha). ValueError if bad."""
    if isinstance(value, str):
        m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value.strip())
        if not m:
            raise ValueError(f"not a colour: {value!r}")
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(float(int(h[i:i + 2], 16)) for i in (0, 2, 4)), 1.0
    if isinstance(value, (list, tuple)) and len(value) in (3, 4) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        rgb = tuple(float(v) for v in value[:3])
        alpha = float(value[3]) if len(value) == 4 else 1.0
        if all(0 <= v <= 255 for v in rgb) and 0 <= alpha <= 1:
            return rgb, alpha
    raise ValueError(f"not a colour: {value!r}")


def normalize_hex(value):
    """'#E11D48' / 'e11d48' / '#e1d' -> '#e11d48', or None."""
    try:
        rgb, _ = parse_color(value)
    except ValueError:
        return None
    return to_hex(rgb)


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in rgb)


def over(fg, alpha, bg):
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(fg, bg))


def mix(a, b, t):
    return tuple(x * (1 - t) + y * t for x, y in zip(a, b))


def luminance(rgb):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    x, y = luminance(a), luminance(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def shift_lightness(rgb, dl):
    """The same hue and saturation, lightness moved by ``dl`` (HLS, 0-1)."""
    h, l, s = colorsys.rgb_to_hls(*(v / 255.0 for v in rgb))
    l = max(0.0, min(1.0, l + dl))
    return tuple(v * 255.0 for v in colorsys.hls_to_rgb(h, l, s))


def _lab(rgb):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a, b):
    """CIEDE2000 colour difference of two sRGB colours (0-255)."""
    l1, a1, b1 = _lab(a)
    l2, a2, b2 = _lab(b)
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cm = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(cm ** 7 / (cm ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dl, dc = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    else:
        dhp = h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360
    dh = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2)
    lm, cmp_ = (l1 + l2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hm = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hm = (h1p + h2p) / 2
    else:
        hm = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hm - 30)) + 0.24 * math.cos(math.radians(2 * hm))
         + 0.32 * math.cos(math.radians(3 * hm + 6)) - 0.20 * math.cos(math.radians(4 * hm - 63)))
    sl = 1 + 0.015 * (lm - 50) ** 2 / math.sqrt(20 + (lm - 50) ** 2)
    sc = 1 + 0.045 * cmp_
    sh = 1 + 0.015 * cmp_ * t
    rt = (-2 * math.sqrt(cmp_ ** 7 / (cmp_ ** 7 + 25 ** 7))
          * math.sin(math.radians(60 * math.exp(-(((hm - 275) / 25) ** 2)))))
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dh / sh) ** 2 + rt * (dc / sc) * (dh / sh))


def hue(rgb):
    return colorsys.rgb_to_hls(*(v / 255.0 for v in rgb))[0] * 360


def hue_diff(a, b):
    d = abs(hue(a) - hue(b)) % 360
    return min(d, 360 - d)


def boost(alpha, k):
    """Move an alpha towards 1 by the fraction ``k`` (0: unchanged). Light and high-contrast themes use it."""
    return alpha + (1 - alpha) * k


# ------------------------------------------------------------------ files
def config_dir():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "slopslide")


def _valid_catalogue(data):
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list) or not isinstance(
            data.get("accents"), list) or not data["themes"]:
        return False
    for t in data["themes"]:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not isinstance(t.get("tokens", {}), dict):
            return False
    for a in data["accents"]:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            return False
        try:
            parse_color(a.get("base"))
            parse_color(a.get("edge"))
        except ValueError:
            return False
    return True


def load_catalogue(path=None):
    """The catalogue at ``path`` (default: the one next to this file); the built-in fallback if missing or broken."""
    try:
        with open(path or CATALOGUE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return _FALLBACK
    return data if _valid_catalogue(data) else _FALLBACK


def _atomic_write(path, text):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def install_catalogue(folder=None, source=None):
    """Copy the catalogue to ``folder``/themes.json (atomic) if it differs. True if it wrote."""
    folder = folder or config_dir()
    with open(source or CATALOGUE_PATH, encoding="utf-8") as f:
        text = f.read()
    dest = os.path.join(folder, CATALOGUE_FILE)
    try:
        with open(dest, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    _atomic_write(dest, text)
    return True


def read_choice(folder=None):
    """(theme id, accent) from ``folder``/theme.json; (None, None) for what is missing or broken."""
    try:
        with open(os.path.join(folder or config_dir(), CHOICE_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    th, acc = data.get("theme"), data.get("accent")
    return (th if isinstance(th, str) else None), (acc if isinstance(acc, str) else None)


def write_choice(theme_id, accent, folder=None):
    """Write the choice atomically: a reader never sees half a file."""
    data = {"version": 1, "theme": theme_id, "accent": accent}
    _atomic_write(os.path.join(folder or config_dir(), CHOICE_FILE), json.dumps(data) + "\n")


# ------------------------------------------------------------------ resolving
def theme_ids(catalogue):
    return [t["id"] for t in catalogue["themes"]]


def find_theme(catalogue, theme_id):
    for t in catalogue["themes"]:
        if t["id"] == theme_id:
            return t
    return None


def find_accent(catalogue, value):
    """An accent of the catalogue by id, or a custom one from a hex value; None if neither."""
    if not isinstance(value, str):
        return None
    for a in catalogue["accents"]:
        if a["id"] == value:
            return a
    hx = normalize_hex(value) if value.strip().startswith("#") else None
    if hx is None:
        return None
    base, _ = parse_color(hx)
    return {"id": hx, "name": hx, "base": hx, "edge": to_hex(mix(base, WHITE, 0.45)), "custom": True}


def tokens(catalogue, theme_id):
    """The raw tokens of a theme: those of the first theme, with the theme's own on top."""
    first = catalogue["themes"][0].get("tokens", {})
    t = find_theme(catalogue, theme_id) or catalogue["themes"][0]
    merged = dict(_FALLBACK["themes"][0]["tokens"])
    merged.update(first)
    merged.pop("cairo", None)                        # exact painted colours belong to their own theme only
    merged.update(t.get("tokens", {}))
    return merged


def _num(tok, key, default):
    v = tok.get(key, default)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _color(tok, key):
    try:
        return parse_color(tok[key])
    except (KeyError, ValueError):
        return parse_color(_FALLBACK["themes"][0]["tokens"][key])


def resolve_accent(tok, accent, wall):
    """Adjust the accent's lightness for this theme until WCAG AA holds. Returns a dict (0-255 colours)."""
    mode = tok.get("mode", "dark")
    glass, galpha = _color(tok, "glass")
    bg = over(glass, galpha, wall)
    base0, _ = parse_color(accent["base"])
    edge0, _ = parse_color(accent["edge"])
    fill_alpha = boost(0.62, _num(tok, "fill_boost", 0.0))
    base = base0
    on = WHITE
    for _ in range(60):
        fill = over(base, fill_alpha, bg)
        w, d = contrast(WHITE, fill), contrast(DARK, fill)
        on = WHITE if (w >= TEXT_MIN or w >= d) else DARK
        if contrast(on, fill) >= TEXT_MIN:
            break
        base = shift_lightness(base, -0.02 if on == WHITE else 0.02)
    line = mix(base0, (0.0, 0.0, 0.0), 0.18) if mode == "light" else edge0
    line0 = line
    for _ in range(60):
        if contrast(line, bg) >= UI_MIN:
            break
        line = shift_lightness(line, -0.02 if mode == "light" else 0.02)
    fill = over(base, fill_alpha, bg)
    return {"base": base, "line": line, "on": on, "fill_alpha": fill_alpha, "bg": bg,
            "adjusted": base != base0 or line != line0,
            "text_on_fill": contrast(on, fill), "line_on_bg": contrast(line, bg)}


def clash_of(tok, accent, resolved):
    """Which status colour the accent looks like in this theme ('warn', 'danger', 'ok', 'info'), or None.
    The catalogue marks Amber, Oranje and Rood; a measured CIEDE2000 distance under CLASH_DE catches the rest."""
    if accent.get("clash"):
        return accent["clash"]
    for key in ("warn", "danger", "ok", "info"):
        ref, _ = _color(tok, key)
        if min(delta_e(resolved["base"], ref), delta_e(resolved["line"], ref)) < CLASH_DE:
            return key
    return None


def _f(rgb):
    return tuple(v / 255.0 for v in rgb)


class Theme:
    """One resolved theme + accent. Colours are Cairo floats (0-1); ``css_*`` give CSS strings."""

    def __init__(self, catalogue, theme_id=None, accent=None):
        th = find_theme(catalogue, theme_id) or find_theme(catalogue, catalogue.get("default_theme")) \
            or catalogue["themes"][0]
        acc = find_accent(catalogue, accent) or find_accent(catalogue, th.get("accent")) \
            or find_accent(catalogue, catalogue.get("default_accent")) or catalogue["accents"][0]
        self.id, self.name, self.note = th["id"], th.get("name", th["id"]), th.get("note", "")
        self.accent_id, self.accent_name = acc["id"], acc.get("name", acc["id"])
        self.tok = tok = tokens(catalogue, self.id)
        self.mode = tok.get("mode", "dark")
        try:
            wall, _ = parse_color(catalogue.get("wall", [29, 95, 122]))
        except ValueError:
            wall = (29.0, 95.0, 122.0)
        self.resolved = r = resolve_accent(tok, acc, wall)
        self.adjusted = r["adjusted"]
        self.clash = clash_of(tok, acc, r)
        exact = acc.get("exact") if not r["adjusted"] else None
        try:
            self.accent = _f(parse_color(exact["base"])[0]) if exact else _f(r["base"])
            self.accent_edge = _f(parse_color(exact["edge"])[0]) if exact else _f(r["line"])
        except (KeyError, TypeError, ValueError):
            self.accent, self.accent_edge = _f(r["base"]), _f(r["line"])
        self.on_accent = _f(r["on"])
        self.bw = int(_num(tok, "bw", 1))
        self._text_boost = _num(tok, "text_boost", 0.0)
        self._shadow_k = _num(tok, "shadow_k", 1.0)
        self._tint_k = _num(tok, "tint_k", 1.0)
        self._line_boost = _num(tok, "line_boost", 0.0)
        self._fill_boost = _num(tok, "fill_boost", 0.0)
        for key in ("text", "shadow", "tint", "line", "warn", "ok", "info", "danger", "danger_fill", "danger_deep",
                    "danger_soft", "danger_ink", "danger_pale", "forgot_text", "muted", "knob", "glass_flat",
                    "bar_flat"):
            setattr(self, "_" + key if key in ("text", "shadow", "tint", "line") else key, _f(_color(tok, key)[0]))
        exact_paint = tok.get("cairo") if isinstance(tok.get("cairo"), dict) else {}
        self._cairo = {}
        for key, value in exact_paint.items():
            try:
                self._cairo[key] = _f(parse_color(value)[0])
            except ValueError:
                pass
        self.ghost_flat = self.bar_flat
        for key in ("glass", "bar", "bar_dim"):
            rgb, a = _color(tok, key)
            setattr(self, key, (*_f(rgb), a))
        try:
            self.card_text = [_f(parse_color(c)[0]) for c in tok["card_text"]][:4]
            assert len(self.card_text) == 4
        except (KeyError, TypeError, ValueError, AssertionError):
            self.card_text = [_f(parse_color(c)[0]) for c in _FALLBACK["themes"][0]["tokens"]["card_text"]]

    def paint(self, key):
        """A colour for Cairo: the theme's exact painted value if it has one (see 'cairo' in themes.json)."""
        return self._cairo.get(key) or getattr(self, key)

    # alphas the code always used, mapped per theme (identity for Donker glas)
    def text(self, a):
        return (*self._text, boost(a, self._text_boost))

    def shade(self, a):
        return (*self._shadow, a * self._shadow_k)

    def tint(self, a):
        return (*self._tint, min(1.0, a * self._tint_k))

    def line(self, a):
        return (*self._line, boost(a, self._line_boost))

    def fill(self, a):
        """Alpha of an accent fill."""
        return boost(a, self._fill_boost)

    def on(self, a):
        return (*self.on_accent, a)

    @property
    def light(self):
        return self.mode == "light"

    def summary(self):
        """What other programs need, as plain data (0-255 hex)."""
        return {"theme": self.id, "accent": self.accent_id, "accent_hex": to_hex(self.resolved["base"]),
                "accent_line_hex": to_hex(self.resolved["line"]), "on_accent_hex": to_hex(self.resolved["on"]),
                "adjusted": self.adjusted, "clash": self.clash}


# CSS helpers
def css_rgb(rgb):
    return "rgb(%d,%d,%d)" % tuple(int(round(v * 255)) for v in rgb[:3])


def css_rgba(rgb, a=None):
    if a is None:
        a = rgb[3] if len(rgb) > 3 else 1.0
    return "rgba(%d,%d,%d,%.2f)" % (*(int(round(v * 255)) for v in rgb[:3]), a)


def css_hex(rgb):
    return to_hex(tuple(v * 255 for v in rgb[:3]))


# ------------------------------------------------------------------ the current theme of this process
_current = None


def load(folder=None, catalogue_path=None):
    """Read the choice from ``folder`` and resolve it against the catalogue."""
    cat = load_catalogue(catalogue_path)
    th, acc = read_choice(folder)
    return Theme(cat, th, acc)


def current():
    global _current
    if _current is None:
        _current = Theme(load_catalogue())
    return _current


def set_current(theme):
    global _current
    _current = theme
