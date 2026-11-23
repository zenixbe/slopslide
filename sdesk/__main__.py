import sys

from . import exitlog

# Block the stop signals before any thread exists, so every thread inherits the mask and exitlog's thread can read who
# sent them (sigwaitinfo). The guardian and --restore get the mask from sdesk when it starts them: open it again.
if "--guardian" in sys.argv or "--restore" in sys.argv:
    exitlog.unblock()
else:
    exitlog.block()

from .app import main  # noqa: E402

sys.exit(main())
