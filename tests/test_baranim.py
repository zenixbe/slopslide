from sdesk.baranim import SPACER, Row


def settle(row, n=400):
    for _ in range(n):
        moving, _ = row.step()
        if not moving:
            return
    raise AssertionError("never settled")


def make(keys=("a", "b", "c"), width=100, tail=("+",)):
    row = Row(tail=tail)
    keys = list(keys) + list(tail)
    row.set_items(keys, {k: width if k not in tail else 40 for k in keys})
    settle(row)
    return row


def xs(row):
    return {k: round(v[0], 3) for k, v in row.positions().items()}


def test_items_start_at_their_natural_width():
    row = make()
    assert xs(row) == {"a": 0, "b": 100, "c": 200, "+": 300}
    assert row.total() == 340


def test_a_width_change_moves_neighbours_smoothly_never_in_one_jump():
    row = make()
    nat = dict(row.nat)
    nat["a"] = 160                                            # e.g. a longer title, or the active item turning bold
    row.set_items(["a", "b", "c", "+"], nat)
    seen = [xs(row)["b"]]
    for _ in range(60):
        row.step()
        seen.append(xs(row)["b"])
    steps = [b - a for a, b in zip(seen, seen[1:])]
    assert seen[0] == 100 and abs(seen[-1] - 160) < 1          # ends at the new place
    assert all(s >= 0 for s in steps) and max(steps) < 30      # monotonic, eased, no sudden change
    assert steps[0] > steps[-1]                                # fast at first, slowing down


def test_a_new_item_grows_in_from_nothing_and_pushes_the_rest_aside_gradually():
    row = make(("a", "b"))
    nat = dict(row.nat); nat["n"] = 120
    row.set_items(["a", "b", "n", "+"], nat)
    assert row.cur["n"] == 0 and xs(row)["+"] == 200          # nothing moved at the instant it appears
    row.step()
    assert 0 < row.cur["n"] < 120 and 200 < xs(row)["+"] < 320
    settle(row)
    assert xs(row)["n"] == 200 and xs(row)["+"] == 320


def test_a_removed_item_shrinks_away_and_is_then_dropped():
    row = make()
    nat = {k: v for k, v in row.nat.items() if k != "b"}
    row.set_items(["a", "c", "+"], nat)
    assert "b" in row.order and "b" in row.dying              # still there, about to shrink
    assert xs(row)["c"] == 200                                # nobody jumped
    gone_total = []
    for _ in range(400):
        moving, gone = row.step()
        gone_total += gone
        if not moving:
            break
    assert gone_total == ["b"] and "b" not in row.order
    assert xs(row) == {"a": 0, "c": 100, "+": 200}


def test_reordering_keeps_everything_where_it_is_drawn_and_then_glides():
    row = make()
    before = xs(row)
    row.set_items(["c", "a", "b", "+"], dict(row.nat))        # c jumps to the front
    assert xs(row) == before                                  # at that instant: no visible change at all
    row.step()
    mid = xs(row)
    assert mid["c"] < before["c"] and mid["a"] > before["a"]   # now they glide
    settle(row)
    assert xs(row) == {"c": 0, "a": 100, "b": 200, "+": 300}


def test_picking_up_an_item_leaves_a_gap_of_exactly_its_size_so_nothing_moves():
    row = make()
    before, total = xs(row), row.total()
    assert row.pick_up("b")
    assert "b" not in row.order and row.dragged == "b"
    assert row.total() == total                                # same total width
    after = xs(row)
    assert after["a"] == before["a"] and after["c"] == before["c"] and after["+"] == before["+"]
    assert row.gap_position() == (100, 100)                    # the gap sits exactly where b was


def test_the_gap_keeps_its_size_and_travels_to_the_new_place():
    row = make(("a", "b", "c", "d"))
    row.pick_up("a")
    settle(row)
    assert xs(row)["b"] == 100 and row.gap_position() == (0, 100)   # gap at the front, as picked up
    row.set_gap(3)                                                  # move it behind c  (items: b c d)
    row.step()
    gx, gw = row.gap_position()
    assert gw == 100                                                # same size: only its position animates
    assert 0 < gx < 300                                             # on its way
    assert 0 < xs(row)["b"] < 100                                   # b is sliding left, out of its way
    settle(row)
    assert xs(row)["b"] == 0 and xs(row)["d"] == 200
    assert row.gap_position() == (300, 100) and xs(row)["+"] == 400


def test_the_gap_never_goes_after_the_tail_button():
    row = make(("a", "b"))
    row.pick_up("a")
    row.set_gap(99)
    settle(row)
    assert row.gap_position()[0] < xs(row)["+"]


def test_dropping_into_the_gap_does_not_change_the_total_or_move_other_items():
    row = make()
    row.pick_up("a")
    row.set_gap(2)
    for _ in range(4):
        row.step()                                             # gap only partly open: the awkward case
    before = {k: v for k, v in xs(row).items()}
    total = row.total()
    assert row.drop() == 2
    assert row.order[:3] == ["b", "c", "a"] and row.dragged is None
    assert abs(row.total() - total) < 1e-9                     # landing is seamless
    after = xs(row)
    assert after["b"] == before["b"] and after["c"] == before["c"] and after["+"] == before["+"]
    settle(row)
    assert xs(row) == {"b": 0, "c": 100, "a": 200, "+": 300}


def test_drop_index_uses_the_closed_layout_so_the_opening_gap_cannot_cause_flicker():
    row = make(("a", "b", "c"))
    row.pick_up("a")                                           # others: b, c with natural width 100 each
    assert row.drop_index(10) == 0 and row.drop_index(60) == 1 and row.drop_index(160) == 2
    row.set_gap(2)
    for _ in range(30):
        row.step()
    assert row.drop_index(10) == 0 and row.drop_index(160) == 2   # unchanged although the gap opened


def test_the_centre_of_the_opened_gap_sits_on_the_boundary_the_index_is_computed_from():
    row = make(("a", "b", "c", "d"))
    row.pick_up("a")                                           # others b c d
    row.set_gap(2)
    settle(row)
    closed, closed_total = row.closed_layout()
    d_x, d_w = closed["d"]                                     # gap index 2 = in front of the third item, d
    boundary_screen = (500 - closed_total / 2) + d_x           # screen centre 500, closed row centred
    gx, gw = row.gap_position()
    gap_centre_screen = (500 - row.total() / 2) + gx + gw / 2  # open row also centred
    assert abs(gap_centre_screen - boundary_screen) < 1e-6


def test_gap_state_after_the_dragged_item_vanishes_is_cleaned_up():
    row = make()
    row.pick_up("b")
    row.set_items(["a", "c", "+"], {"a": 100, "c": 100, "+": 40})   # b's window was closed while dragging
    assert row.dragged is None and row.gap["idx"] is None
    settle(row)
    assert xs(row) == {"a": 0, "c": 100, "+": 200}


def test_spacer_key_never_collides_with_a_window_id():
    assert SPACER not in (1, 2, "add")


def test_moving_the_gap_never_makes_anything_jump():
    row = make(("a", "b", "c", "d"))
    row.pick_up("a")
    settle(row)
    before = xs(row)
    row.set_gap(3)                                             # from the front to behind c
    assert xs(row) == before                                   # at that instant nothing moved on screen
    gx0, _ = row.gap_position()
    track = {k: [v] for k, v in before.items()}
    for _ in range(50):
        row.step()
        for k, v in xs(row).items():
            track[k].append(v)
    for k, seq in track.items():                               # every item moves in small, eased steps
        assert max(abs(b - a) for a, b in zip(seq, seq[1:])) < 40, k
    assert track["b"][-1] < track["b"][0]                      # b did slide left, out of the way
    assert row.gap_position()[0] > gx0                         # the gap itself travelled right, gradually
    settle(row)
    assert xs(row)["b"] == 0 and row.gap_position() == (300, 100)


# ------------------------------------------------ head items (clock, sound): first, fixed, not counted as windows
def make_head(keys=("a", "b", "c")):
    row = Row(tail=("+",), head=("clock", "snd"))
    all_keys = ["clock", "snd", *keys, "+"]
    row.set_items(all_keys, {k: 100 for k in all_keys})
    settle(row)
    return row


def test_head_items_sit_first_and_are_not_live_items():
    row = make_head()
    assert xs(row)["clock"] == 0 and xs(row)["snd"] == 100 and xs(row)["a"] == 200
    assert row._live() == ["a", "b", "c"]
    assert row.drop_index(0) == 0 and row.drop_index(10_000) == 3     # indices count windows only


def test_head_items_cannot_be_picked_up():
    row = make_head()
    assert row.pick_up("clock") is False and row.pick_up("snd") is False
    assert row.dragged is None


def test_a_gap_at_the_start_opens_after_the_head_items():
    row = make_head()
    assert row.pick_up("b")
    row.set_gap(0)
    settle(row)
    pos = xs(row)
    gap_x = row.gap_position()[0]
    assert gap_x == pos["a"] - row.gap["cur"]                         # right behind snd, before a
    assert pos["clock"] == 0 and pos["snd"] == 100


def test_a_vanished_first_window_never_ends_up_in_front_of_the_head_items():
    row = make_head()
    row.set_items(["clock", "snd", "b", "c", "+"], {k: 100 for k in ("clock", "snd", "b", "c", "+")})
    assert row.order[:2] == ["clock", "snd"] and row.order[2] == "a"  # "a" shrinks away behind the head items
    settle(row)
    assert row.order == ["clock", "snd", "b", "c", "+"]
