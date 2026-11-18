#!/usr/bin/env bash
# Tear the lab down using recorded pids only (never pattern-kills, so the real kwin is safe).
LAB=${LAB:-/tmp/sdesk-lab}
for f in sdesk xvfb; do [ -f "$LAB/$f.pid" ] && kill "$(cat "$LAB/$f.pid")" 2>/dev/null; done
[ -f "$LAB/kwin.pid" ] && kill -TERM -- "-$(cat "$LAB/kwin.pid")" 2>/dev/null   # whole kwin + dbus group
sleep 1
rm -rf "$LAB"
