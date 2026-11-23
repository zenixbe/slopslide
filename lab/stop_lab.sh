#!/usr/bin/env bash
# Tear the lab down using recorded pids only (never pattern-kills, so the real kwin is safe).
# A pid is only signalled if that process runs with the lab's own XDG_RUNTIME_DIR ($LAB/run, set by start_lab.sh): a
# stale pid file whose pid now belongs to the real sdesk, or a lab directory shared with another thread, cannot stop
# anything outside this lab (issue #18).
LAB=${LAB:-/tmp/sdesk-lab}
in_lab() { tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | grep -qxF "XDG_RUNTIME_DIR=$LAB/run"; }
for f in sdesk xvfb; do
  if [ -f "$LAB/$f.pid" ]; then
    p=$(cat "$LAB/$f.pid")
    if in_lab "$p"; then kill "$p" 2>/dev/null; else echo "stop_lab: $f pid $p is not in $LAB, left alone" >&2; fi
  fi
done
if [ -f "$LAB/kwin.pid" ]; then
  p=$(cat "$LAB/kwin.pid")
  if in_lab "$p"; then kill -TERM -- "-$p" 2>/dev/null; else echo "stop_lab: kwin pid $p is not in $LAB, left alone" >&2; fi
fi
sleep 1
rm -rf "$LAB"
