"""XWM.place_many sends the move route that KWin does not clamp (issue #26), without a real X server."""
from sdesk.layout import Rect
from sdesk.xwm import XWM


class _Root:
    def __init__(self):
        self.sent = []

    def send_event(self, ev, event_mask=0):
        self.sent.append(ev)


class _Display:
    flushed = 0

    def flush(self):
        self.flushed += 1


def _xwm(extents):
    x = XWM.__new__(XWM)                                    # no display connection
    x.d, x.root = _Display(), _Root()
    x.win = lambda wid: wid
    x.atom = lambda name: {"_NET_MOVERESIZE_WINDOW": 77}[name]
    x._frame_extents = lambda w: extents.get(w, (0, 0, 0, 0))
    return x


def test_place_many_uses_net_moveresize_window_as_pager_and_keeps_negative_x():
    x = _xwm({5: (2, 2, 30, 2)})
    x.place_many([(5, Rect(-662, 40, 364, 934)), (6, Rect(-302, 40, 1100, 900))])
    assert x.d.flushed == 1                                 # one batch: parent and attached window move together
    (e5, e6) = x.root.sent
    assert e5.client_type == 77 and e5.window == 5
    flags, ex, ey, ew, eh = e5.data[1]
    assert flags >> 12 & 0xF == 2                           # source indication 2 = pager: KWin does not clamp it
    assert flags & 0xF00 == 0xF00 and flags & 0xFF == 1     # x, y, width, height given; NorthWest gravity
    assert ex - (1 << 32) == -662 and ey == 40              # negative x is sent as is (two's complement), not clamped
    assert (ew, eh) == (360, 902)                           # outer frame minus _NET_FRAME_EXTENTS
    assert e6.data[1][1] - (1 << 32) == -302
