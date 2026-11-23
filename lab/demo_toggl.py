"""Run SlopSlide in the lab with a stand-in Toggl API (tests/fake_toggl.py): never the real Second Brain or Toggl.

    lab/start_lab.sh
    DISPLAY=:99 .venv/bin/python lab/demo_toggl.py --sound off --battery off [--no-patch]

The stand-in listens on 127.0.0.1:18769. Change its state from a shell, for example:

    curl -s -XPOST localhost:18769/__fake -d '{"running": {"project_id": "100000", "description": "Nieuwsbrief", "hours": 17}}'
    curl -s -XPOST localhost:18769/__fake -d '{"fail": 502}'          # Second Brain / Toggl down ({"fail": 0}: back)
    curl -s -XPOST localhost:18769/__fake -d '{"patch": false}'       # Second Brain without PATCH /current

Extra arguments go to sdesk.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sdesk import app, toggl  # noqa: E402
from tests.fake_toggl import FakeToggl  # noqa: E402

argv = [a for a in sys.argv[1:] if a != "--no-patch"]
fake = FakeToggl(patch="--no-patch" not in sys.argv[1:])
url = fake.serve(18769)
toggl.INTERVAL = 5                       # the lab wants to see changes quickly
toggl.ENTRIES_EVERY = 10
print("stand-in Toggl API on", url, flush=True)
sys.exit(app.main(argv if "--toggl-url" in argv else [*argv, "--toggl-url", url]))     # a given URL wins (e.g. a closed port)
