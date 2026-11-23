"""Battery levels of the mouse (a Logitech receiver, HID++ 2.0) and the two halves of a Dygma Defy (Focus protocol over
its serial port), for the taskbar.

Everything that decodes bytes is a pure function, so it is tested without hardware. The readers open a device, ask,
and close it again at once: Bazecor (Dygma's app) uses the same serial port, so the keyboard is read only once a minute
and never while another program holds the port.
"""
import fcntl
import glob
import os
import select
import termios
import threading
import time
import tty
from dataclasses import dataclass, replace

LOW = 20                    # percent: at or below this the level shows in orange
INTERVAL = 60               # seconds between two reads

DEFY_GLOB = "/dev/serial/by-id/usb-DYGMA_DEFY_*"
HIDRAW_SYSFS = "/sys/class/hidraw"
LOGITECH = 0x046D
RECEIVERS = {0xC52B, 0xC532, 0xC534, 0xC539, 0xC53A, 0xC53D, 0xC53F, 0xC541, 0xC545, 0xC547, 0xC548, 0xC54D}

NAMES = {"mouse": "Mouse", "left": "Defy left", "right": "Defy right"}


@dataclass(frozen=True)
class Reading:
    """What is known about one battery. ``level`` is a percentage, or None when the device cannot be read."""
    key: str                        # mouse | left | right
    level: int | None = None
    charging: bool = False          # on a cable (charging, or full and still plugged in)
    note: str = ""                  # extra words for the tooltip: "charging", "not connected", ...
    when: float | None = None       # time.time() of the read

    @property
    def available(self):
        return self.level is not None


def state(reading, low=LOW):
    """How the taskbar shows a reading: off | charging | low | normal."""
    if reading is None or not reading.available:
        return "off"
    if reading.charging:
        return "charging"
    return "low" if reading.level <= low else "normal"


def tooltip(readings):
    """One line per device: the exact value, what it is doing and when it was read."""
    lines = []
    for key in ("mouse", "left", "right"):
        r = readings.get(key) or Reading(key)
        value = f"{r.level}%" if r.available else "–"
        note = f", {r.note}" if r.note else ""
        when = time.strftime(" (read %H:%M)", time.localtime(r.when)) if r.when else ""
        lines.append(f"{NAMES[key]}: {value}{note}{when}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------ Dygma Focus
DEFY_STATUS = {0: ("", False), 1: ("charging", True), 2: ("charged", True), 3: ("battery fault", False)}
# 4 = that half is not connected. Source: Bazecor, src/renderer/components/atoms/battery/BatteryStatusSide.tsx


def split_focus_reply(buf):
    """Take one reply off the front of what the keyboard sent. A Focus reply is its lines and then a line with just
    ".". Returns (reply text, rest of the buffer), or (None, buf) when the terminator has not come in yet."""
    text = buf.decode("utf-8", "replace") if isinstance(buf, bytes) else buf
    lines = text.split("\n")
    for i, line in enumerate(lines[:-1]):                    # the last part has no newline yet: not a whole line
        if line.strip() == ".":
            reply = "\n".join(l.rstrip("\r") for l in lines[:i]).strip()
            rest = "\n".join(lines[i + 1:])
            return reply, rest.encode() if isinstance(buf, bytes) else rest
    return None, buf


def parse_int(text):
    try:
        return int(text.strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return None


def defy_reading(side, level_text, status_text, when=None):
    """A Reading for one half from the replies to ``wireless.battery.<side>.level`` and ``.status``."""
    level, status = parse_int(level_text), parse_int(status_text)
    if status == 4:
        return Reading(side, None, False, "not connected", when)
    if level is None or not 0 <= level <= 100:
        return Reading(side, None, False, "no answer", when)
    note, charging = DEFY_STATUS.get(status, ("", False))
    return Reading(side, level, charging, note, when)


class Busy(Exception):
    """Another program (Bazecor) has the serial port: do not touch it now."""


class SerialPort:
    """A raw serial port, opened only if no other program holds it (node-serialport, which Bazecor uses, takes a
    flock on the port; so do we, without waiting)."""

    def __init__(self, path, timeout=1.0):
        self.timeout = timeout
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise Busy(path) from None
        try:
            tty.setraw(self.fd)
        except termios.error:
            pass

    def flush_input(self):
        """Throw away anything an earlier command left behind (a late reply would shift every answer by one)."""
        try:
            termios.tcflush(self.fd, termios.TCIFLUSH)
        except termios.error:
            pass
        while select.select([self.fd], [], [], 0.05)[0]:
            if not os.read(self.fd, 4096):
                break

    def write(self, data):
        os.write(self.fd, data)

    def read(self, timeout):
        if not select.select([self.fd], [], [], timeout)[0]:
            return b""
        try:
            return os.read(self.fd, 4096)
        except BlockingIOError:
            return b""

    def close(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def focus_command(port, command, timeout=1.0):
    """Send one Focus command and return its reply, or None if the terminator did not come in time."""
    port.flush_input()
    port.write(command.encode() + b"\n")
    buf, end = b"", time.monotonic() + timeout
    while time.monotonic() < end:
        buf += port.read(max(0.0, end - time.monotonic()))
        reply, _rest = split_focus_reply(buf)
        if reply is not None:
            return reply
    return None


def read_defy(paths=None, opener=SerialPort, when=None):
    """Both halves of the keyboard: {"left": Reading, "right": Reading}. Raises Busy when Bazecor has the port."""
    when = time.time() if when is None else when
    paths = sorted(glob.glob(DEFY_GLOB)) if paths is None else paths
    if not paths:
        return {s: Reading(s, None, False, "keyboard not plugged in", when) for s in ("left", "right")}
    try:
        port = opener(paths[0])
    except OSError as e:
        return {s: Reading(s, None, False, f"cannot open the keyboard ({e.strerror or e})", when) for s in ("left", "right")}
    try:
        out = {}
        for side in ("left", "right"):
            level = focus_command(port, f"wireless.battery.{side}.level")
            status = focus_command(port, f"wireless.battery.{side}.status")
            out[side] = defy_reading(side, level or "", status or "", when)
        return out
    finally:
        port.close()


# ------------------------------------------------------------------------------------------------- Logitech HID++
SHORT, LONG = 0x10, 0x11            # HID++ report ids: 7 and 20 bytes
SW_ID = 0x0A                        # our software id, in the low nibble of the function byte
ROOT = 0x0000
UNIFIED_BATTERY, BATTERY_STATUS, BATTERY_VOLTAGE = 0x1004, 0x1000, 0x1001
# charging bytes of features 0x1000 and 0x1004 (Solaar's BatteryStatus): 1 recharging, 2 almost full, 3 full,
# 4 slow recharge; 0 discharging; 5, 6 errors
HIDPP_CHARGE = {1: "charging", 2: "almost full, charging", 3: "charged", 4: "charging slowly"}
UNIFIED_LEVELS = {8: 90, 4: 50, 2: 20, 1: 5}       # approximate level flags -> percent, when no exact percent is given
VOLTAGE_CURVE = [(4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60), (3811, 50), (3778, 40), (3751, 30),
                 (3717, 20), (3671, 10), (3646, 5), (3579, 2), (3500, 0)]         # a Li-ion cell, from Solaar


def uevent_ids(text):
    """(vendor, product) from a hidraw device's uevent (``HID_ID=0003:0000046D:0000C54D``), or None."""
    for line in text.splitlines():
        if line.startswith("HID_ID="):
            parts = line.split("=", 1)[1].split(":")
            if len(parts) == 3:
                try:
                    return int(parts[1], 16), int(parts[2], 16)
                except ValueError:
                    return None
    return None


def is_hidpp_descriptor(desc):
    """True for the receiver's HID++ interface: a vendor usage page (0xFF00) with long reports (id 0x11)."""
    return b"\x06\x00\xff" in desc and b"\x85\x11" in desc


def find_receiver(sysfs=HIDRAW_SYSFS):
    """/dev path of the Logitech receiver's HID++ interface, or None. The headset (046d:0aa7) is not a receiver."""
    for node in sorted(glob.glob(os.path.join(sysfs, "hidraw*"))):
        try:
            with open(os.path.join(node, "device", "uevent")) as f:
                ids = uevent_ids(f.read())
            if ids is None or ids[0] != LOGITECH or ids[1] not in RECEIVERS:
                continue
            with open(os.path.join(node, "device", "report_descriptor"), "rb") as f:
                if is_hidpp_descriptor(f.read()):
                    return "/dev/" + os.path.basename(node)
        except OSError:
            continue
    return None


def hidpp_request(device, feature_index, function, params=b""):
    """A long HID++ 2.0 request (20 bytes)."""
    body = bytes([LONG, device, feature_index, (function << 4) | SW_ID]) + bytes(params)
    return body + bytes(20 - len(body))


def match_reply(report, device, feature_index, function):
    """The parameters of the answer to our request, None if ``report`` is not that answer, or an int error code."""
    if len(report) < 7 or report[0] not in (SHORT, LONG) or report[1] != device:
        return None
    fn = (function << 4) | SW_ID
    if report[2] in (0x8F, 0xFF) and report[3] == feature_index and report[4] == fn:      # HID++ 1.0 / 2.0 error
        return int(report[5])
    if report[2] == feature_index and report[3] == fn:
        return bytes(report[4:])
    return None


def battery_from_unified(params):
    """Feature 0x1004 get_status: (percent, charging, note)."""
    percent, level, status = params[0], params[1], params[2]
    if not percent:
        percent = next((v for bit, v in UNIFIED_LEVELS.items() if level & bit), 0)
    return percent, status in HIDPP_CHARGE, HIDPP_CHARGE.get(status, "")


def battery_from_status(params):
    """Feature 0x1000 get_battery_level_status: (percent, charging, note)."""
    return params[0], params[2] in HIDPP_CHARGE, HIDPP_CHARGE.get(params[2], "")


def percent_from_voltage(mv):
    if mv >= VOLTAGE_CURVE[0][0]:
        return 100
    if mv <= VOLTAGE_CURVE[-1][0]:
        return 0
    for (hi_v, hi_p), (lo_v, lo_p) in zip(VOLTAGE_CURVE, VOLTAGE_CURVE[1:]):
        if lo_v <= mv <= hi_v:
            return round(lo_p + (hi_p - lo_p) * (mv - lo_v) / (hi_v - lo_v))
    return 0


def battery_from_voltage(params):
    """Feature 0x1001 get_battery_voltage: (percent, charging, note)."""
    mv, flags = (params[0] << 8) | params[1], params[2]
    if not flags & 0x80:
        return percent_from_voltage(mv), False, f"{mv} mV"
    full = (flags & 0x03) == 1
    return (100 if full else percent_from_voltage(mv)), True, "charged" if full else "charging"


class Hidraw:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def write(self, data):
        os.write(self.fd, data)

    def read(self, timeout):
        if not select.select([self.fd], [], [], timeout)[0]:
            return b""
        try:
            return os.read(self.fd, 64)
        except BlockingIOError:
            return b""

    def close(self):
        os.close(self.fd)


def hidpp_call(dev, device, feature_index, function, params=b"", timeout=0.6):
    """Send a request and wait for its answer (other reports, like the mouse's own notifications, are skipped).
    Returns the parameters, an int error code, or None on time-out."""
    dev.write(hidpp_request(device, feature_index, function, params))
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        report = dev.read(max(0.0, end - time.monotonic()))
        if report:
            got = match_reply(report, device, feature_index, function)
            if got is not None:
                return got
    return None


def feature_index(dev, device, feature):
    """Index of a feature on the device (root feature, function 0), 0 if it has none, None if the device is off."""
    got = hidpp_call(dev, device, 0, 0, bytes([feature >> 8, feature & 0xFF]))
    return got[0] if isinstance(got, bytes) else None


def read_mouse(path=None, opener=Hidraw, devices=range(1, 7), when=None):
    """The mouse behind the receiver: a Reading for "mouse"."""
    when = time.time() if when is None else when
    path = find_receiver() if path is None else path
    if path is None:
        return Reading("mouse", None, False, "no Logitech receiver", when)
    try:
        dev = opener(path)
    except PermissionError:
        return Reading("mouse", None, False, "no access to the receiver: install the udev rule (see README)", when)
    except OSError as e:
        return Reading("mouse", None, False, f"cannot open the receiver ({e.strerror or e})", when)
    try:
        for device in devices:
            for feature, function, decode in ((UNIFIED_BATTERY, 1, battery_from_unified),
                                              (BATTERY_STATUS, 0, battery_from_status),
                                              (BATTERY_VOLTAGE, 0, battery_from_voltage)):
                idx = feature_index(dev, device, feature)
                if idx is None:
                    break                                           # nothing (awake) at this device number
                if not idx:
                    continue                                        # not this feature: try the next one
                got = hidpp_call(dev, device, idx, function)
                if isinstance(got, bytes):
                    percent, charging, note = decode(got)
                    return Reading("mouse", max(0, min(100, percent)), charging, note, when)
        return Reading("mouse", None, False, "mouse off or asleep", when)
    finally:
        dev.close()


# ---------------------------------------------------------------------------------------------------------- poller
BUSY_NOTE = "Bazecor has the port, last value kept"


class Poller:
    """Reads every battery now and then on a worker thread. ``on_update({key: Reading})`` is called from that thread
    (wrap it in GLib.idle_add). When Bazecor holds the keyboard's port, the last keyboard values are kept."""

    def __init__(self, on_update, interval=INTERVAL, read_mouse=None, read_defy=None):
        self.on_update = on_update
        self.interval = interval
        self._read_mouse, self._read_defy = read_mouse, read_defy           # None: this module's readers
        self.readings = {}
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="sdesk-battery", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def poll(self):
        """One round of reads; returns the new readings (also used by the tests)."""
        out = dict(self.readings)
        try:
            out["mouse"] = (self._read_mouse or read_mouse)()
        except Exception as e:                                       # a reader must never stop the loop
            out["mouse"] = Reading("mouse", None, False, f"error: {e}", time.time())
        try:
            out.update((self._read_defy or read_defy)())
        except Busy:
            for side in ("left", "right"):
                old = out.get(side) or Reading(side)
                if BUSY_NOTE not in old.note:
                    out[side] = replace(old, note=", ".join(n for n in (old.note, BUSY_NOTE) if n))
        except Exception as e:
            out.update({s: Reading(s, None, False, f"error: {e}", time.time()) for s in ("left", "right")})
        self.readings = out
        return out

    def _loop(self):
        while not self._stop.is_set():
            self.on_update(self.poll())
            self._stop.wait(self.interval)
