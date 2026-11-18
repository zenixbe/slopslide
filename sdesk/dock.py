"""Switch Deepin's own dock off while sdesk runs, and back on afterwards.

The dock is an applet inside the main ``dde-shell@DDE.service`` (next to notifications, launcher, OSD...), so it can
only be removed by starting that service with ``-d org.deepin.ds.dock`` (``-d`` = disable applet). That is done with a
*runtime* systemd drop-in under $XDG_RUNTIME_DIR: it vanishes at logout/reboot, so a crash can never leave the dock
permanently gone. Disabling the dock also removes what lives in it (system tray, clock, volume/network menus).

Fallback when that fails: the legacy "keep hidden" mode, which still slides out when the pointer touches the bottom edge.
"""
import os
import re
import subprocess

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

UNIT = "dde-shell@DDE.service"
DOCK_APPLET = "org.deepin.ds.dock"
DROPIN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
                      "systemd", "user", f"{UNIT}.d", "sdesk-no-dock.conf")

# legacy hide mode (fallback)
DOCK_BUS = "org.deepin.dde.daemon.Dock1"
DOCK_PATH = "/org/deepin/dde/daemon/Dock1"
STATE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "sdesk")
HIDEMODE_STATE = os.path.join(STATE_DIR, "dock_hidemode")     # exists only while we hold the dock hidden
KEEP_HIDDEN = 1


def dropin_text(unit_cat):
    """Drop-in that starts the unit's own ExecStart with the dock applet disabled.

    The unit is Type=dbus and waits for ``BusName`` (the dock's name, org.deepin.dde.Dock1) to appear; without the dock
    that never happens and systemd times out, so BusName is pointed at the shell's own ``--serviceName`` instead.
    """
    for line in unit_cat.splitlines():
        m = re.match(r"\s*ExecStart=(?![\s-]*$)(.+)$", line)
        if m:
            cmd = m.group(1).strip()
            if f"-d {DOCK_APPLET}" not in cmd:
                cmd += f" -d {DOCK_APPLET}"
            text = f"[Service]\nExecStart=\nExecStart={cmd}\n"
            name = re.search(r"--serviceName=(\S+)", cmd)
            if name:
                text += f"BusName={name.group(1)}\n"
            return text
    raise ValueError(f"no ExecStart in {UNIT}")


def _systemctl(*args, timeout=25):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def disable():
    """Restart dde-shell without its dock. Returns True on success."""
    try:
        if os.path.exists(DROPIN):
            return True                                   # left by an earlier run; already disabled
        text = dropin_text(_systemctl("cat", UNIT).stdout)
        os.makedirs(os.path.dirname(DROPIN), exist_ok=True)
        with open(DROPIN, "w") as f:
            f.write(text)
        _systemctl("daemon-reload")
        r = _systemctl("restart", UNIT)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "restart failed")
        return True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"sdesk: could not disable the DDE dock ({e})", flush=True)
        enable()                                           # never leave a half-applied override behind
        return False


def enable(restart="block"):
    """Remove the override and restart dde-shell, which brings the dock back.

    restart: "block" (wait for it), "no-block" (fire and forget, for shutdown paths) or "never" (the session is ending:
    only remove the file, so nothing can delay a logout or reboot)."""
    if not os.path.exists(DROPIN):
        return
    try:
        os.remove(DROPIN)
        os.rmdir(os.path.dirname(DROPIN))
    except OSError:
        pass
    if restart == "never":
        return
    try:
        _systemctl("daemon-reload", timeout=5)
        _systemctl("restart", *(["--no-block"] if restart == "no-block" else []), UNIT, timeout=5 if restart == "no-block" else 25)
    except subprocess.SubprocessError as e:
        print(f"sdesk: could not restart dde-shell: {e}", flush=True)


# ------------------------------------------------------------ legacy fallback
def _prop(method, params, sig):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(DOCK_BUS, DOCK_PATH, "org.freedesktop.DBus.Properties", method,
                         GLib.Variant(sig, params), None, Gio.DBusCallFlags.NONE, 2000, None)


def hide():
    """Fallback: dock in "keep hidden" mode (still reveals itself at the very bottom edge)."""
    try:
        cur = _prop("Get", (DOCK_BUS, "HideMode"), "(ss)").unpack()[0]
        if not os.path.exists(HIDEMODE_STATE):            # don't clobber state left by a crashed run
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(HIDEMODE_STATE, "w") as f:
                f.write(str(cur))
        _prop("Set", (DOCK_BUS, "HideMode", GLib.Variant("i", KEEP_HIDDEN)), "(ssv)")
    except GLib.Error as e:
        print(f"sdesk: could not hide the DDE dock ({e.message})", flush=True)


def restore_hidemode():
    try:
        with open(HIDEMODE_STATE) as f:
            mode = int(f.read().strip())
        _prop("Set", (DOCK_BUS, "HideMode", GLib.Variant("i", mode)), "(ssv)")
        os.remove(HIDEMODE_STATE)
    except FileNotFoundError:
        pass
    except (GLib.Error, ValueError) as e:
        print(f"sdesk: could not restore the DDE dock's hide mode: {e}", flush=True)


def restore(restart="block"):
    """Undo everything this module did."""
    restore_hidemode()
    enable(restart)
