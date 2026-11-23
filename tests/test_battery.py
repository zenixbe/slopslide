import os

import pytest

from sdesk import battery as b
from sdesk.battery import Reading


# ------------------------------------------------------------------------------------------------------ states, tip
def test_states():
    assert b.state(Reading("mouse", 85)) == "normal"
    assert b.state(Reading("mouse", 20)) == "low"
    assert b.state(Reading("mouse", 21)) == "normal"
    assert b.state(Reading("left", 10, charging=True)) == "charging"
    assert b.state(Reading("right")) == "off"
    assert b.state(None) == "off"


def test_tooltip_has_exact_values_notes_and_time():
    t = 1795427781.0
    tip = b.tooltip({"mouse": Reading("mouse", 85, when=t), "left": Reading("left", 64, True, "charging", t)})
    lines = tip.split("\n")
    assert lines[0].startswith("Mouse: 85% (") and lines[0].endswith(")")
    assert lines[1].startswith("Defy left: 64%, charging (")
    assert lines[2] == "Defy right: –"


# ------------------------------------------------------------------------------------------------------ Dygma Focus
def test_split_focus_reply_waits_for_the_terminator():
    assert b.split_focus_reply(b"40\r\n") == (None, b"40\r\n")
    assert b.split_focus_reply(b"40\r\n.\r\n") == ("40", b"")
    assert b.split_focus_reply(b"40\r\n.") == (None, b"40\r\n.")          # "." without its newline: not yet whole
    reply, rest = b.split_focus_reply(b"a\nb\n.\n0\n.\n")
    assert reply == "a\nb" and rest == b"0\n.\n"


def test_defy_reading():
    assert b.defy_reading("left", "40", "0") == Reading("left", 40, False, "")
    assert b.defy_reading("left", "64", "1") == Reading("left", 64, True, "charging")
    assert b.defy_reading("right", "100", "2") == Reading("right", 100, True, "charged")
    assert b.defy_reading("right", "100", "3") == Reading("right", 100, False, "battery fault")
    assert b.defy_reading("right", "0", "4") == Reading("right", None, False, "not connected")
    assert b.defy_reading("right", "", "") == Reading("right", None, False, "no answer")
    assert b.defy_reading("right", "255", "0x00").level is None
    assert b.defy_reading("right", "18", "0x00") == Reading("right", 18, False, "")      # unknown status: just the level


class FakeSerial:
    """A Defy: answers Focus commands; ``stale`` is a late reply still waiting in the input buffer."""

    def __init__(self, answers, stale=b""):
        self.answers, self.pending, self.sent, self.closed = answers, stale, [], False

    def flush_input(self):
        self.pending = b""

    def write(self, data):
        cmd = data.decode().strip()
        self.sent.append(cmd)
        self.pending += self.answers.get(cmd, "").encode() + b"\r\n.\r\n"

    def read(self, timeout):
        data, self.pending = self.pending, b""
        return data

    def close(self):
        self.closed = True


ANSWERS = {"wireless.battery.left.level": "100", "wireless.battery.left.status": "1",
           "wireless.battery.right.level": "18", "wireless.battery.right.status": "0"}


def test_read_defy_asks_both_halves_and_closes_the_port():
    port = FakeSerial(ANSWERS, stale=b"help\r\nversion\r\n.\r\n")          # a late reply must not shift the answers
    out = b.read_defy(["/dev/fake"], opener=lambda path: port, when=5.0)
    assert out["left"] == Reading("left", 100, True, "charging", 5.0)
    assert out["right"] == Reading("right", 18, False, "", 5.0)
    assert port.closed
    assert port.sent == ["wireless.battery.left.level", "wireless.battery.left.status",
                         "wireless.battery.right.level", "wireless.battery.right.status"]


def test_read_defy_without_keyboard_or_with_busy_port():
    out = b.read_defy([], when=1.0)
    assert out["left"].note == "keyboard not plugged in" and not out["right"].available

    def busy(path):
        raise b.Busy(path)
    with pytest.raises(b.Busy):
        b.read_defy(["/dev/fake"], opener=busy)


def test_serial_port_refuses_a_port_another_program_has_locked(tmp_path):
    import fcntl
    path = tmp_path / "tty"
    path.write_bytes(b"")
    holder = os.open(path, os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX)                                     # like Bazecor (node-serialport) does
    try:
        with pytest.raises(b.Busy):
            b.SerialPort(str(path))
    finally:
        os.close(holder)
    b.SerialPort(str(path)).close()                                        # free again: opens (not a tty: no raw mode)


# ------------------------------------------------------------------------------------------------- Logitech HID++
UEVENT = "DRIVER=hid-generic\nHID_ID=0003:0000046D:0000C54D\nHID_NAME=Logitech USB Receiver\n"
HIDPP_DESC = bytes.fromhex("0600ff0901a1018510950675081500 26ff0009018100090191 00c0"
                           "0600ff0902a1018511951375081500 26ff00090281000902 9100c0".replace(" ", ""))
MOUSE_DESC = bytes.fromhex("05010902a1010901a100c006 00ff09f1 750895058100c0".replace(" ", ""))


def test_uevent_ids_and_descriptor():
    assert b.uevent_ids(UEVENT) == (0x046D, 0xC54D)
    assert b.uevent_ids("HID_NAME=x") is None
    assert b.is_hidpp_descriptor(HIDPP_DESC)
    assert not b.is_hidpp_descriptor(MOUSE_DESC)


def test_find_receiver_picks_the_hidpp_interface_and_skips_the_headset(tmp_path):
    def node(name, uevent, desc):
        d = tmp_path / name / "device"
        d.mkdir(parents=True)
        (d / "uevent").write_text(uevent)
        (d / "report_descriptor").write_bytes(desc)
    node("hidraw1", "HID_ID=0003:0000046D:00000AA7\nHID_NAME=Logitech PRO\n", HIDPP_DESC)     # the headset
    node("hidraw2", UEVENT, MOUSE_DESC)
    node("hidraw4", UEVENT, HIDPP_DESC)
    assert b.find_receiver(str(tmp_path)) == "/dev/hidraw4"
    assert b.find_receiver(str(tmp_path / "none")) is None


def test_request_and_reply_matching():
    req = b.hidpp_request(1, 0, 0, b"\x10\x04")
    assert len(req) == 20 and req[:6] == bytes([0x11, 1, 0, 0x0A, 0x10, 0x04])
    assert b.match_reply(bytes([0x11, 1, 0, 0x0A, 5, 0, 0]) + bytes(13), 1, 0, 0)[:1] == b"\x05"
    assert b.match_reply(bytes([0x10, 1, 0x8F, 0, 0x0A, 0x09, 0]), 1, 0, 0) == 9          # HID++ 1.0 error
    assert b.match_reply(bytes([0x11, 1, 0xFF, 5, 0x1A, 0x02, 0]) + bytes(13), 1, 5, 1) == 2   # HID++ 2.0 error
    assert b.match_reply(bytes([0x11, 1, 4, 0x00, 1, 2, 3]) + bytes(13), 1, 0, 0) is None    # someone else's report
    assert b.match_reply(bytes([0x11, 2, 0, 0x0A, 5, 0, 0]) + bytes(13), 1, 0, 0) is None    # another device


def test_battery_decoders():
    assert b.battery_from_unified(bytes([85, 4, 0, 0])) == (85, False, "")
    assert b.battery_from_unified(bytes([64, 4, 1, 1])) == (64, True, "charging")
    assert b.battery_from_unified(bytes([0, 2, 0, 0])) == (20, False, "")             # only the level flags
    assert b.battery_from_status(bytes([50, 30, 0])) == (50, False, "")
    assert b.battery_from_status(bytes([100, 90, 3])) == (100, True, "charged")
    assert b.percent_from_voltage(4200) == 100 and b.percent_from_voltage(3400) == 0
    assert b.percent_from_voltage(3811) == 50 and 50 < b.percent_from_voltage(3835) < 60
    assert b.battery_from_voltage(bytes([0x0E, 0xE3, 0x00])) == (50, False, "3811 mV")
    assert b.battery_from_voltage(bytes([0x0E, 0xE3, 0x80])) == (50, True, "charging")
    assert b.battery_from_voltage(bytes([0x10, 0x5A, 0x81])) == (100, True, "charged")


class FakeReceiver:
    """A receiver with one mouse at device 1 that has ``features`` ({feature id: (index, reply params)})."""

    def __init__(self, features, device=1, noise=True):
        self.features, self.device, self.noise = features, device, noise
        self.queue, self.closed = [], False

    def write(self, data):
        dev, idx, fn = data[1], data[2], data[3]
        if self.noise:
            self.queue.append(bytes([0x11, dev, 0x04, 0x00, 1, 2, 3]) + bytes(13))     # a notification in between
        if dev != self.device:
            self.queue.append(bytes([0x10, dev, 0x8F, idx, fn, 0x09, 0]))             # no such device
        elif idx == 0:
            feature = (data[4] << 8) | data[5]
            index = self.features.get(feature, (0, b""))[0]
            self.queue.append(bytes([0x11, dev, 0, fn, index]) + bytes(15))
        else:
            params = next(p for i, p in self.features.values() if i == idx)
            self.queue.append(bytes([0x11, dev, idx, fn]) + params + bytes(16 - len(params)))

    def read(self, timeout):
        return self.queue.pop(0) if self.queue else b""

    def close(self):
        self.closed = True


def test_read_mouse_unified_battery():
    rx = FakeReceiver({0x1004: (6, bytes([85, 4, 0, 0]))})
    assert b.read_mouse("/dev/fake", opener=lambda p: rx, when=2.0) == Reading("mouse", 85, False, "", 2.0)
    assert rx.closed


def test_read_mouse_falls_back_to_voltage_and_other_device_numbers():
    rx = FakeReceiver({0x1001: (7, bytes([0x0E, 0xE3, 0x80]))}, device=2)
    assert b.read_mouse("/dev/fake", opener=lambda p: rx, when=2.0) == Reading("mouse", 50, True, "charging", 2.0)


def test_read_mouse_when_off_unreadable_or_absent():
    rx = FakeReceiver({}, device=9)
    assert b.read_mouse("/dev/fake", opener=lambda p: rx).note == "mouse off or asleep"

    def denied(path):
        raise PermissionError(13, "Permission denied")
    r = b.read_mouse("/dev/hidraw4", opener=denied)
    assert not r.available and "udev rule" in r.note
    assert b.read_mouse(None, opener=denied) is not None


# ---------------------------------------------------------------------------------------------------------- poller
def test_poller_keeps_keyboard_values_while_bazecor_has_the_port():
    calls = {"n": 0}

    def defy():
        calls["n"] += 1
        if calls["n"] > 1:
            raise b.Busy("/dev/ttyACM0")
        return {"left": Reading("left", 40, when=1.0), "right": Reading("right", 41, when=1.0)}

    p = b.Poller(lambda r: None, read_mouse=lambda: Reading("mouse", 85), read_defy=defy)
    p.poll()
    out = p.poll()
    assert out["left"].level == 40 and out["left"].when == 1.0 and b.BUSY_NOTE in out["left"].note
    out = p.poll()
    assert out["right"].note == b.BUSY_NOTE                       # said once, not once per round


def test_poller_survives_a_reader_that_crashes():
    def boom():
        raise RuntimeError("bad")
    p = b.Poller(lambda r: None, read_mouse=boom, read_defy=boom)
    out = p.poll()
    assert not out["mouse"].available and "bad" in out["mouse"].note
    assert not out["left"].available
