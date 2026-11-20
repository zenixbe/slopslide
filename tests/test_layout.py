from sdesk import layout
from sdesk.layout import Rect

AREA = Rect(0, 0, 1000, 600)


def test_compute_has_margins_between_and_around():
    rects, total = layout.compute([("a", 300, None), ("b", 200, None)], AREA, gap=20, margin=30, scroll=0)
    assert rects["a"] == Rect(30, 30, 300, 540)           # top/bottom margin 30
    assert rects["b"].x - rects["a"].right == 20           # gap between windows
    assert total == 30 + 300 + 20 + 200 + 30


def test_explicit_height_is_clamped_and_top_aligned():
    rects, _ = layout.compute([("a", 100, 200), ("b", 100, 9999)], AREA, 10, 10, 0)
    assert rects["a"].h == 200 and rects["a"].y == 10
    assert rects["b"].h == 580


def test_scroll_moves_everything_left():
    r0, _ = layout.compute([("a", 300, None)], AREA, 10, 10, 0)
    r1, _ = layout.compute([("a", 300, None)], AREA, 10, 10, 50)
    assert r1["a"].x == r0["a"].x - 50


def test_clamp_scroll():
    assert layout.clamp_scroll(-50, 3000, 1000) == 0
    assert layout.clamp_scroll(5000, 3000, 1000) == 2000
    assert layout.clamp_scroll(10, 600, 1000) == -200      # narrow strip is centred


def test_reveal_minimal_movement():
    assert layout.reveal(0, 100, 300, 1000, 20) == 0        # already visible
    assert layout.reveal(0, 900, 300, 1000, 20) == 900 + 300 + 20 - 1000
    assert layout.reveal(500, 100, 300, 1000, 20) == 80     # scroll left just enough
    assert layout.reveal(0, 400, 990, 1000, 20) == 380      # wider than view: align left


def test_scroll_bounds_classic_without_centres():
    assert layout.scroll_bounds(3000, 1000) == (0, 2000)
    assert layout.scroll_bounds(600, 1000) == (-200, -200)       # narrow: one fixed, centred position


def test_scroll_bounds_widened_so_first_and_last_window_can_reach_the_middle():
    lo, hi = layout.scroll_bounds(3000, 1000, first_cx=300, last_cx=2700)
    assert lo == 300 - 500 and hi == 2700 - 500                  # lo < 0 and hi > total - view
    assert lo < 0 and hi > 2000


def test_widened_bounds_still_include_the_centred_resting_position_of_a_narrow_strip():
    total, view = 3452, 5120
    lo, hi = layout.scroll_bounds(total, view, first_cx=574, last_cx=total - 574)
    assert lo <= (total - view) / 2 <= hi


def test_center_scroll():
    assert layout.center_scroll(400, 300, 1000) == 400 + 150 - 500


# ------------------------------------------------------- finding a floating sidebar on the right edge
def cand(type_, rect, states=("ABOVE", "STAYS_ON_TOP", "STICKY"), viewable=True):
    return {"type": type_, "rect": rect, "states": set(states), "viewable": viewable}


SW, SH = 5120, 1440
SIDEBAR = cand("UTILITY", Rect(4764, 0, 356, 1440))                         # the real "LLM usage" window


def test_the_real_sidebar_is_found_and_padding_is_added():
    assert layout.right_inset([SIDEBAR], SW, SH, 16) == 356 + 16
    assert layout.right_inset([SIDEBAR], SW, SH, 0) == 356


def test_things_that_are_not_a_sidebar_are_ignored():
    ignored = [
        cand("NOTIFICATION", Rect(4633, 0, 488, 1440), ()),                  # dde-shell's notification layer
        cand("NORMAL", Rect(5119, 12, 1676, 1356), ()),                      # one of our own parked windows
        cand("NORMAL", Rect(3497, 13, 1793, 1354), ()),                      # an ordinary window
        cand("DOCK", Rect(4000, 1380, 800, 52)),                             # a dock/bar
        cand("DESKTOP", Rect(0, 0, 5120, 1440)),
        cand("UTILITY", Rect(4764, 0, 356, 1440), viewable=False),           # not shown
        cand("UTILITY", Rect(4764, 0, 356, 1440), states=("ABOVE", "HIDDEN")),
        cand("UTILITY", Rect(4764, 0, 356, 1440), states=()),                # not floating on top
        cand("UTILITY", Rect(2000, 100, 356, 1000)),                         # not at the right edge
        cand("UTILITY", Rect(4900, 100, 220, 90)),                           # a small popup, not a sidebar
        cand("UTILITY", Rect(3000, 0, 2120, 1440)),                          # far too wide
    ]
    for c in ignored:
        assert layout.right_inset([c], SW, SH, 16) == 0, c


def test_with_several_candidates_the_one_reaching_furthest_left_wins():
    wide = cand("UTILITY", Rect(4600, 0, 520, 1440))
    assert layout.right_inset([SIDEBAR, wide], SW, SH, 10) == 520 + 10
    assert layout.right_inset([], SW, SH, 10) == 0


def test_a_sidebar_a_few_pixels_from_the_edge_still_counts():
    floating = cand("UTILITY", Rect(4740, 0, 356, 1440))                     # ends at 5096: a 24px margin to the edge
    assert layout.right_inset([floating], SW, SH, 0) == SW - 4740


# ------------------------------------------------------------------ the fade strip beside the sidebar
def test_find_sidebar_returns_the_rectangle_and_ignores_the_same_things_as_right_inset():
    assert layout.find_sidebar([SIDEBAR], SW, SH) == Rect(4764, 0, 356, 1440)
    assert layout.find_sidebar([cand("NORMAL", Rect(5119, 12, 1676, 1356), ())], SW, SH) is None
    assert layout.find_sidebar([cand("NOTIFICATION", Rect(4633, 0, 488, 1440), ())], SW, SH) is None
    assert layout.find_sidebar([], SW, SH) is None


def test_the_widest_reaching_sidebar_is_the_one_found():
    wide = cand("UTILITY", Rect(4600, 0, 520, 1440))
    assert layout.find_sidebar([SIDEBAR, wide], SW, SH) == Rect(4600, 0, 520, 1440)


def test_the_fade_zone_sits_directly_left_of_the_sidebar_with_the_same_height():
    z = layout.fade_zone(Rect(4764, 0, 356, 1440), 64, SW)
    assert z == Rect(4700, 0, 64, 1440) and z.right == 4764                           # ends exactly where the sidebar starts
    assert layout.fade_zone(Rect(4764, 40, 356, 800), 100, SW) == Rect(4664, 40, 100, 800)


def test_no_fade_zone_without_a_sidebar_a_width_or_room():
    assert layout.fade_zone(None, 64, SW) is None
    assert layout.fade_zone(Rect(4764, 0, 356, 1440), 0, SW) is None
    assert layout.fade_zone(Rect(30, 0, 356, 1440), 64, SW) is None                   # would stick out of the screen


def test_an_unpainted_light_capture_is_refused_but_real_wallpapers_are_not():
    from sdesk.edgefade import looks_unusable
    white = b"\xff\xff\xff\x00" * 5000
    gtk_grey = bytes((0xf4, 0xf5, 0xf6, 0)) * 5000          # the light grey of an unpainted GTK window
    teal = bytes((70, 60, 5, 0)) * 5000                    # BGRX of a dark teal wallpaper
    black = b"\x00\x00\x00\x00" * 5000                     # a solid black wallpaper is genuine: fading into it is right
    dark_solid = bytes((40, 30, 20, 0)) * 5000
    gradient = b"".join(bytes((i % 256, (i * 2) % 256, 90, 0)) for i in range(5000))   # varies: even if light, it is real
    mixed = b"\xff\xff\xff\x00" * 2500 + teal[:10000]
    assert looks_unusable(white) and looks_unusable(gtk_grey) and looks_unusable(b"") and looks_unusable(None)
    assert not looks_unusable(teal) and not looks_unusable(black) and not looks_unusable(dark_solid)
    assert not looks_unusable(gradient) and not looks_unusable(mixed)


# ----------------------------------------------- the cover also hides everything under the sidebar
def test_the_cover_spans_the_fade_strip_and_the_whole_sidebar():
    side = Rect(4764, 0, 356, 1440)
    z = layout.cover_zone(side, 64, SW)
    assert z == Rect(4700, 0, 64 + 356, 1440) and z.right == SW          # from the fade's left edge to the screen edge
    assert layout.cover_zone(None, 64, SW) is None and layout.cover_zone(side, 0, SW) is None


def test_the_sidebar_candidate_keeps_its_window_id_so_it_can_be_stacked_under():
    c = dict(SIDEBAR, xid=123)
    assert layout.find_sidebar_candidate([c], SW, SH)["xid"] == 123
    assert layout.find_sidebar_candidate([cand("NORMAL", Rect(5119, 12, 1676, 1356), ())], SW, SH) is None


def test_directly_below_checks_the_stacking_order_bottom_to_top():
    assert layout.directly_below([1, 2, 3, 4], 2, 3) is True
    assert layout.directly_below([1, 2, 3, 4], 2, 4) is False             # something sits between
    assert layout.directly_below([1, 2, 3, 4], 3, 2) is False             # wrong way round: it is above
    assert layout.directly_below([1, 2], 9, 2) is False and layout.directly_below([1, 2], 1, 9) is False


def test_the_fade_curve_removes_the_window_quickly_and_is_monotonic():
    assert layout.fade_alpha(0) == 0 and layout.fade_alpha(1) == 1
    assert layout.fade_alpha(-3) == 0 and layout.fade_alpha(7) == 1                  # clamped
    assert layout.fade_alpha(0.5) > 0.7                                               # window opacity below 30% at half way
    assert layout.fade_alpha(0.1) < 0.3                                               # but not an abrupt start
    vals = [layout.fade_alpha(i / 50) for i in range(51)]
    assert vals == sorted(vals)


# ---------------------------------------------------- zones clipped around windows over a gap (#9)
from sdesk.layout import clip_zones, subtract


def test_subtract_leaves_a_rect_without_overlap_alone():
    r = Rect(0, 0, 40, 100)
    assert subtract(r, Rect(50, 0, 10, 10)) == [r]


def test_subtract_cuts_bands_and_side_pieces():
    parts = subtract(Rect(0, 0, 40, 100), Rect(10, 20, 10, 30))
    assert sorted(parts, key=lambda p: (p.y, p.x)) == [
        Rect(0, 0, 40, 20), Rect(0, 20, 10, 30), Rect(20, 20, 20, 30), Rect(0, 50, 40, 50)]
    assert sum(p.w * p.h for p in parts) == 40 * 100 - 10 * 30


def test_clip_zone_around_a_panel_docked_against_the_right_window():
    # the tasktree case: a tool window whose right part lies over the gap, from y=100 to y=700
    zones = [(1, Rect(500, 0, 40, 1000))]
    out = clip_zones(zones, [Rect(300, 100, 224, 600)], min_w=6, min_h=6)
    assert sorted(out, key=lambda z: (z[1].y, z[1].x)) == [
        (1, Rect(500, 0, 40, 100)), (1, Rect(524, 100, 16, 600)), (1, Rect(500, 700, 40, 300))]
    hole = Rect(300, 100, 224, 600)
    assert not any(r.intersects(hole) for _, r in out)


def test_clip_drops_a_fully_covered_zone_and_slivers():
    zones = [(0, Rect(0, 0, 30, 1000)), (2, Rect(600, 0, 20, 1000))]
    out = clip_zones(zones, [Rect(-10, -10, 100, 2000), Rect(603, 0, 100, 997)], min_w=6, min_h=6)
    # zone 0 is gone; zone 2 keeps a 3px-wide sliver (dropped) and a 3px-high band (dropped)
    assert out == []


def test_clip_without_holes_keeps_zones():
    zones = [(0, Rect(0, 0, 30, 1000)), (1, Rect(500, 0, 20, 1000))]
    assert clip_zones(zones, [], 6, 6) == zones


def test_clip_with_several_holes_in_one_zone():
    out = clip_zones([(3, Rect(0, 0, 20, 300))], [Rect(0, 0, 20, 100), Rect(0, 200, 20, 100)], 6, 6)
    assert out == [(3, Rect(0, 100, 20, 100))]
