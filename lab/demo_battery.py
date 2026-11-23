"""Run SlopSlide in the lab with stand-in battery readings: no mouse or keyboard is read.

    lab/start_lab.sh
    echo charging > /tmp/sdesk-lab/battery.txt          # normal | low | charging | off  (default: normal)
    DISPLAY=:99 .venv/bin/python lab/demo_battery.py --sound off

The readings are taken from $LAB/battery.txt every 2 seconds, so one running bar shows every state in turn.
Extra arguments go to sdesk.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdesk import app, battery  # noqa: E402
from sdesk.battery import Reading  # noqa: E402

LAB = os.environ.get("LAB", "/tmp/sdesk-lab")
STATES = {     # mouse, left half, right half
    "normal": (Reading("mouse", 85), Reading("left", 92), Reading("right", 88)),
    "low": (Reading("mouse", 85), Reading("left", 64), Reading("right", 18)),
    "charging": (Reading("mouse", 85), Reading("left", 64, True, "charging"), Reading("right", 88)),
    "off": (Reading("mouse", None, False, "mouse off or asleep"), Reading("left", 92), Reading("right", 88)),
}


def current():
    try:
        with open(os.path.join(LAB, "battery.txt")) as f:
            name = f.read().strip()
    except OSError:
        name = "normal"
    now = time.time()
    return {r.key: Reading(r.key, r.level, r.charging, r.note, now) for r in STATES.get(name, STATES["normal"])}


battery.read_mouse = lambda: current()["mouse"]
battery.read_defy = lambda: {k: v for k, v in current().items() if k != "mouse"}
battery.INTERVAL = 2
_init = battery.Poller.__init__
battery.Poller.__init__ = lambda self, on_update, interval=2, **kw: _init(self, on_update, interval, **kw)

sys.exit(app.main(sys.argv[1:]))
