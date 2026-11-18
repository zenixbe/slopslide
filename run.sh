#!/usr/bin/env bash
# Start the scrolling desktop on the current (real) session. Ctrl+C restores everything.
cd "$(dirname "$0")" && exec .venv/bin/python -m sdesk --disable-dock "$@"
