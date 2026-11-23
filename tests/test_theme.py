"""Themes and accents (issue #22): loading, fallback, contrast adjustment, token mapping, status colours, live follow."""
import json
import os
import re
import time

import pytest

from sdesk import theme as themes

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")
CAT = themes.load_catalogue()
THEMES = themes.theme_ids(CAT)
ACCENTS = [a["id"] for a in CAT["accents"]]
CUSTOM = ["#e11d48", "#ffff00", "#000080", "#808080", "#ffffff", "#000000", "#0f0"]


def rgb(t):
    return tuple(v * 255 for v in t[:3])


# ------------------------------------------------------------------ loading and fallback
def test_catalogue_has_the_five_chosen_themes_and_ten_accents():
    assert THEMES == ["glass-dark", "glass-light", "contrast", "amber", "midnight"]
    assert [t["name"] for t in CAT["themes"]] == ["Donker glas", "Licht glas", "Hoog contrast", "Amber avond",
                                                  "Middernacht"]
    assert len(ACCENTS) == 10 and ACCENTS[0] == "teal"
    assert CAT["default_theme"] == "glass-dark" and CAT["default_accent"] == "teal"


@pytest.mark.parametrize("content", [None, "", "{not json", "[]", '{"themes": [], "accents": []}',
                                     '{"themes": [{"id": 1}], "accents": []}',
                                     '{"themes": [{"id": "x"}], "accents": [{"id": "a", "base": "red"}]}'])
def test_a_missing_or_broken_catalogue_falls_back_to_glass_dark_teal(tmp_path, content):
    path = tmp_path / "themes.json"
    if content is not None:
        path.write_text(content)
    cat = themes.load_catalogue(str(path))
    t = themes.Theme(cat, "glass-light", "blue")
    assert (t.id, t.accent_id) == ("glass-dark", "teal")
    assert t.accent == pytest.approx((0.08, 0.72, 0.65))


@pytest.mark.parametrize("content", [None, "", "nope", "[]", '"x"', '{"theme": 3, "accent": []}', '{}'])
def test_a_missing_or_broken_choice_means_glass_dark_teal(tmp_path, content):
    if content is not None:
        (tmp_path / "theme.json").write_text(content)
    t = themes.load(str(tmp_path))
    assert (t.id, t.accent_id) == ("glass-dark", "teal")


def test_unknown_theme_or_accent_falls_back_per_field(tmp_path):
    themes.write_choice("glass-light", "no-such-accent", str(tmp_path))
    t = themes.load(str(tmp_path))
    assert (t.id, t.accent_id) == ("glass-light", "blue")            # the theme's own default accent
    themes.write_choice("no-such-theme", "pink", str(tmp_path))
    t = themes.load(str(tmp_path))
    assert (t.id, t.accent_id) == ("glass-dark", "pink")


def test_custom_hex_accent(tmp_path):
    themes.write_choice("midnight", "#E11D48", str(tmp_path))
    t = themes.load(str(tmp_path))
    assert t.accent_id == "#e11d48"
    assert themes.find_accent(CAT, "#e1d")["id"] == "#ee11dd"
    assert themes.find_accent(CAT, "#12345") is None
    assert themes.find_accent(CAT, "e11d48") is None                  # a bare word is an accent id, not a colour


def test_write_choice_is_atomic_and_readable(tmp_path):
    themes.write_choice("amber", "#e11d48", str(tmp_path))
    assert json.loads((tmp_path / "theme.json").read_text()) == {"version": 1, "theme": "amber", "accent": "#e11d48"}
    assert os.listdir(tmp_path) == ["theme.json"]                     # no temporary file left behind
    assert themes.read_choice(str(tmp_path)) == ("amber", "#e11d48")


def test_install_catalogue_copies_once(tmp_path):
    assert themes.install_catalogue(str(tmp_path / "slopslide")) is True
    assert themes.install_catalogue(str(tmp_path / "slopslide")) is False
    copy = json.loads((tmp_path / "slopslide" / "themes.json").read_text())
    assert themes.theme_ids(copy) == THEMES


def test_config_dir_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert themes.config_dir() == str(tmp_path / "slopslide")


# ------------------------------------------------------------------ contrast adjustment
@pytest.mark.parametrize("theme_id", THEMES)
@pytest.mark.parametrize("accent", ACCENTS + CUSTOM)
def test_every_combination_reaches_wcag_aa(theme_id, accent):
    t = themes.Theme(CAT, theme_id, accent)
    r = t.resolved
    assert r["text_on_fill"] >= themes.TEXT_MIN, "text on the selection"
    assert r["line_on_bg"] >= themes.UI_MIN, "ring / edge / slider against the glass"


def test_adjustment_only_changes_lightness():
    t = themes.Theme(CAT, "glass-light", "green")
    assert t.adjusted
    base, _ = themes.parse_color("#22c55e")
    assert abs(themes.hue(t.resolved["line"]) - themes.hue(base)) < 2


def test_glass_dark_teal_is_not_adjusted_and_keeps_the_old_cairo_values():
    t = themes.Theme(CAT)
    assert (t.id, t.accent_id, t.adjusted, t.clash) == ("glass-dark", "teal", False, None)
    assert t.accent == pytest.approx((0.08, 0.72, 0.65), abs=1e-9)        # the old ACCENT
    assert t.accent_edge == pytest.approx((0.45, 0.93, 0.85), abs=1e-9)   # the old ACCENT_EDGE
    assert t.on_accent == (1.0, 1.0, 1.0)


def test_dark_text_on_a_light_accent():
    t = themes.Theme(CAT, "glass-dark", "amber")
    assert t.on_accent == pytest.approx(tuple(v / 255 for v in themes.DARK))


# ------------------------------------------------------------------ token mapping
def test_glass_dark_tokens_are_the_colours_from_before():
    t = themes.Theme(CAT)
    ap = pytest.approx
    assert t.text(0.62) == ap((1, 1, 1, 0.62)) and t.text(0.92) == ap((1, 1, 1, 0.92))
    assert t.shade(0.45) == ap((0, 0, 0, 0.45))
    assert t.tint(0.11) == ap((1, 1, 1, 0.11)) and t.line(0.32) == ap((1, 1, 1, 0.32))
    assert t.fill(0.62) == ap(0.62)
    assert t.bar == ap((0.04, 0.06, 0.08, 0.30)) and t.bar_dim == ap((0, 0, 0, 0.16))
    assert t.bar_flat == ap((0.10, 0.11, 0.14))
    assert t.paint("ghost_flat") == ap((0.10, 0.13, 0.15))
    assert t.warn == ap((0.96, 0.62, 0.04)) and t.ok == ap((0.13, 0.77, 0.37)) and t.info == ap((0.23, 0.51, 0.96))
    assert t.paint("danger") == ap((0.97, 0.44, 0.44)) and t.paint("danger_fill") == ap((0.86, 0.15, 0.15))
    assert t.forgot_text == ap((1, 0.82, 0.82)) and t.muted == ap((0.75, 0.78, 0.8)) and t.knob == ap((1, 1, 1))
    assert t.bw == 1


def test_other_themes_do_not_inherit_the_exact_paint_values():
    t = themes.Theme(CAT, "glass-light")
    assert t.paint("danger") == t.danger != pytest.approx((0.97, 0.44, 0.44))
    assert t.paint("ghost_flat") == t.bar_flat


def test_light_theme_has_dark_text_and_a_light_shadow():
    t = themes.Theme(CAT, "glass-light")
    assert themes.luminance(rgb(t.text(1))) < 0.05
    assert t.text(0.62)[3] > 0.62                      # boosted: dim text stays readable on light glass
    assert t.shade(0.45)[:3] == (1.0, 1.0, 1.0)


def test_high_contrast_has_thick_edges_and_no_shadow():
    t = themes.Theme(CAT, "contrast")
    assert t.bw == 2 and t.shade(0.45)[3] == 0 and t.line(0.20)[3] > 0.7


def _norm(css):
    """Colours as rgba tuples with rounded alpha, so 0.5 == 0.50 and #fff == rgb(255,255,255)."""
    def col(m):
        s = m.group(0)
        if s.startswith("#"):
            h = s[1:]
            h = "".join(c * 2 for c in h) if len(h) == 3 else h
            return "rgba(%d,%d,%d,1.00)" % tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        nums = [float(x) for x in re.findall(r"[\d.]+", s)]
        a = nums[3] if len(nums) > 3 else 1.0
        return "rgba(%d,%d,%d,%.2f)" % (*map(int, nums[:3]), a)
    return re.sub(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)", col, css)


@pytest.mark.parametrize("module,golden", [("menu", "menu_css_glass_dark.css"),
                                           ("togglcard", "toggl_css_glass_dark.css")])
def test_glass_dark_css_is_the_css_from_before(module, golden):
    import importlib
    mod = importlib.import_module("sdesk." + module)
    with open(os.path.join(GOLDEN, golden)) as f:
        old = f.read()
    assert _norm(mod.css(themes.Theme(CAT)).decode()) == _norm(old)


@pytest.mark.parametrize("theme_id", THEMES[1:])
def test_other_themes_change_the_css(theme_id):
    from sdesk import menu, togglcard
    t = themes.Theme(CAT, theme_id)
    for mod in (menu, togglcard):
        assert mod.css(t) != mod.css(themes.Theme(CAT))
    if t.light:
        assert "rgba(255,255,255,0.92)" not in menu.css(t).decode()


# ------------------------------------------------------------------ status colours (llm-usage-sidebar#36)
@pytest.mark.parametrize("theme_id", THEMES)
def test_ok_warn_info_stay_distinguishable(theme_id):
    t = themes.Theme(CAT, theme_id)
    cols = {k: rgb(getattr(t, k)) for k in themes.STATUS}
    for a, b in (("ok", "warn"), ("ok", "info"), ("warn", "info")):
        assert themes.delta_e(cols[a], cols[b]) >= themes.STATUS_DE, (theme_id, a, b)
        assert themes.hue_diff(cols[a], cols[b]) >= themes.STATUS_HUE, (theme_id, a, b)


@pytest.mark.parametrize("theme_id", THEMES)
@pytest.mark.parametrize("accent", ACCENTS + CUSTOM)
def test_the_adjusted_accent_never_silently_collapses_onto_a_status_colour(theme_id, accent):
    """Either the accent stays CLASH_DE away from ok, warn and info, or the picker shows ⚠ (``clash``)."""
    t = themes.Theme(CAT, theme_id, accent)
    if t.clash:
        return
    r = t.resolved
    for key in themes.STATUS + ("danger",):
        ref = rgb(getattr(t, key))
        assert min(themes.delta_e(r["base"], ref), themes.delta_e(r["line"], ref)) >= themes.CLASH_DE, key


@pytest.mark.parametrize("theme_id", THEMES)
def test_the_default_accent_of_each_theme_has_no_clash(theme_id):
    t = themes.Theme(CAT, theme_id)
    assert t.clash is None, (theme_id, t.accent_id)


@pytest.mark.parametrize("theme_id", THEMES)
def test_amber_orange_red_always_warn(theme_id):
    assert themes.Theme(CAT, theme_id, "amber").clash == "warn"
    assert themes.Theme(CAT, theme_id, "orange").clash == "warn"
    assert themes.Theme(CAT, theme_id, "red").clash == "danger"


def test_delta_e_basics():
    assert themes.delta_e((10, 20, 30), (10, 20, 30)) == pytest.approx(0)
    assert themes.delta_e((255, 255, 255), (0, 0, 0)) == pytest.approx(100, abs=0.1)
    a, b = (200, 30, 40), (30, 200, 40)
    assert themes.delta_e(a, b) == pytest.approx(themes.delta_e(b, a))


def test_contrast_basics():
    assert themes.contrast((255, 255, 255), (0, 0, 0)) == pytest.approx(21)
    assert themes.contrast((119, 119, 119), (255, 255, 255)) == pytest.approx(4.48, abs=0.01)


# ------------------------------------------------------------------ live follow
def test_watcher_reloads_after_an_atomic_write(tmp_path):
    from gi.repository import GLib

    from sdesk.themewatch import ThemeWatcher
    seen = []
    w = ThemeWatcher(seen.append, folder=str(tmp_path))
    first = w.start()
    assert first.id == "glass-dark"
    ctx = GLib.MainContext.default()
    try:
        themes.write_choice("midnight", "pink", str(tmp_path))
        end = time.monotonic() + 3
        while not seen and time.monotonic() < end:
            ctx.iteration(False)
            time.sleep(0.01)
    finally:
        w.stop()
    assert seen and (seen[-1].id, seen[-1].accent_id) == ("midnight", "pink")
