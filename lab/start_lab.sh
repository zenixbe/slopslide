#!/usr/bin/env bash
# Isolated test desktop: Xvfb :99 + its own kwin_x11 + its own D-Bus session and config.
# Nothing here touches the real session. Stop with lab/stop_lab.sh.
#
# No compositor by default; COMPOSE=X keeps kwin's own (transparency; Xvfb has no real OpenGL).
# Never use KWIN_COMPOSE=N/X for this: Deepin saves that choice in dconfig, which is shared with the real session over the
# system bus, and the real desktop then loses its window effects ("performance mode"). Suspending the compositor over the
# lab's own D-Bus is runtime-only and leaves the real settings alone.
set -e
LAB=${LAB:-/tmp/sdesk-lab}
mkdir -p "$LAB"/{config,data,cache,run}
chmod 700 "$LAB/run"
export DISPLAY=:99 XDG_CONFIG_HOME=$LAB/config XDG_DATA_HOME=$LAB/data XDG_CACHE_HOME=$LAB/cache XDG_RUNTIME_DIR=$LAB/run
Xvfb :99 -screen 0 5120x1440x24 -nolisten tcp >"$LAB/xvfb.log" 2>&1 &
echo $! > "$LAB/xvfb.pid"
sleep 1
setsid dbus-run-session -- kwin_x11 >"$LAB/kwin.log" 2>&1 &    # own process group: stop_lab kills exactly this tree
echo $! > "$LAB/kwin.pid"
sleep 2
if [ "${COMPOSE:-N}" != X ]; then
  for p in $(pgrep -x kwin_x11); do
    if tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -qx "DISPLAY=:99"; then
      DBUS_SESSION_BUS_ADDRESS=$(tr '\0' '\n' < /proc/$p/environ | grep ^DBUS_SESSION_BUS_ADDRESS= | cut -d= -f2-) \
        qdbus org.kde.KWin /Compositor org.kde.kwin.Compositing.suspend >/dev/null 2>&1 || true
    fi
  done
  sleep 1
fi
xprop -root _NET_SUPPORTING_WM_CHECK _NET_WORKAREA
