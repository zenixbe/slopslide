from sdesk.shapes import rounded_rects


def covered(rects):
    return {(px, py) for x, y, w, h in rects for px in range(x, x + w) for py in range(y, y + h)}


def test_a_plain_box_without_radius_is_one_rectangle():
    assert rounded_rects(10, 5, 40, 20, 0) == [(10, 5, 40, 20)]


def test_corners_are_cut_but_the_edges_are_not():
    cells = covered(rounded_rects(0, 0, 40, 30, 6))
    assert (0, 0) not in cells and (39, 0) not in cells and (0, 29) not in cells and (39, 29) not in cells   # corners cut
    assert (6, 0) in cells and (33, 0) in cells and (20, 0) in cells                                           # top edge intact
    assert (0, 15) in cells and (39, 15) in cells                                                              # side middle intact
    assert len(cells) < 40 * 30 and len(cells) > 40 * 30 - 4 * 6 * 6                                           # only the corners went


def test_it_is_compact():
    assert len(rounded_rects(0, 0, 120, 40, 6)) == 13            # 6 top rows + 1 middle + 6 bottom rows


def test_it_is_symmetric():
    cells = covered(rounded_rects(0, 0, 50, 40, 6))
    for x, y in list(cells):
        assert (49 - x, y) in cells and (x, 39 - y) in cells


def test_offsets_are_applied_and_nothing_leaves_the_box():
    cells = covered(rounded_rects(100, 20, 30, 20, 4))
    assert all(100 <= x < 130 and 20 <= y < 40 for x, y in cells)


def test_tiny_boxes_do_not_break():
    assert rounded_rects(0, 0, 0, 10, 6) == []
    assert covered(rounded_rects(0, 0, 4, 4, 6))                # radius is clamped to the box
