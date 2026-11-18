"""Sound output switching for the taskbar, through ``pactl`` (PulseAudio / PipeWire's pulse server). No GTK in here.

The pure parts (reading sinks, short names, which output comes next) are unit tested; ``Watcher`` is the thin layer that
talks to the sound server in a background thread, so the taskbar never waits for ``pactl``.
"""
import json
import re
import select
import shutil
import subprocess
import threading
from dataclasses import dataclass

_DROP_WORDS = re.compile(r"\b(analog|digital|stereo|mono|surround\s*[\d.]*|output|adapter|audio/sink sink)\b", re.I)
_VENDOR_NOISE = re.compile(r"[,.]|\b(inc|ltd|corp|corporation|co|gmbh|llc)\b", re.I)


@dataclass(frozen=True)
class Sink:
    name: str               # what pactl calls it: pactl set-default-sink NAME
    description: str        # what the sound server calls it ("PRO Analog Stereo")
    label: str              # short, for the taskbar ("Logitech PRO")
    kind: str               # "headset" (usb / bluetooth), "display" (hdmi), "speakers"
    haystack: str           # lower-case text a --sound-match pattern is looked for in
    volume: int = 0         # percent of the loudest channel
    muted: bool = False


def vendor_short(vendor):
    """'Logitech, Inc.' -> 'Logitech'."""
    words = _VENDOR_NOISE.sub(" ", vendor or "").split()
    return words[0] if words else ""


def short_label(description, vendor=""):
    """'PRO Analog Stereo' + 'Logitech, Inc.' -> 'Logitech PRO';  'Logitech BT Adapter' -> 'Logitech BT';
    'Built-in Audio Digital Stereo (IEC958)' -> 'Built-in Audio'."""
    text = re.sub(r"\([^)]*\)", " ", description or "")
    text = " ".join(_DROP_WORDS.sub(" ", text).split())
    brand = vendor_short(vendor)
    if brand and len(text.split()) == 1 and brand.lower() not in text.lower():
        text = f"{brand} {text}"                      # a bare model name ("PRO") means little on its own
    return text or (description or "Audio output")


def _kind(name, props):
    bus = props.get("device.bus", "")
    if bus in ("usb", "bluetooth") or name.startswith("bluez"):
        return "headset"
    if "hdmi" in name.lower() or "hdmi" in props.get("device.profile.name", "").lower():
        return "display"
    return "speakers"


def _volume_percent(raw):
    """Loudest channel of a sink's ``volume`` object in percent ({"front-left": {"value_percent": "45%"}, ...})."""
    best = 0
    for ch in (raw or {}).values():
        try:
            best = max(best, int(str(ch.get("value_percent", "0")).rstrip("%")))
        except (ValueError, AttributeError):
            pass
    return best


def volume_from_x(x, left, width):
    """Slider position -> percent 0..100 (clamped), for a track starting at ``left`` and ``width`` wide."""
    if width <= 0:
        return 0
    return int(round(max(0.0, min(1.0, (x - left) / width)) * 100))


def step_volume(current, direction, step=5):
    """One wheel notch: ``direction`` > 0 louder, < 0 quieter; stays within 0..100 and lands on multiples of ``step``."""
    cur = int(round(current / step)) * step
    return max(0, min(100, cur + step * (1 if direction > 0 else -1)))


def parse_sinks(text):
    """Real output devices from ``pactl -f json list sinks`` (virtual null sinks are left out)."""
    try:
        raw = json.loads(text)
    except ValueError:
        return []
    out = []
    for s in raw:
        name, props = s.get("name", ""), s.get("properties", {}) or {}
        desc = s.get("description", "") or name
        if not name or name.startswith("null") or props.get("node.virtual") == "true":
            continue
        vendor = props.get("device.vendor.name", "")
        hay = " ".join([name, desc, vendor, props.get("device.product.name", "")]).lower()
        out.append(Sink(name, desc, short_label(desc, vendor), _kind(name, props), hay,
                        _volume_percent(s.get("volume")), bool(s.get("mute"))))
    return out


def candidates(sinks, match):
    """The outputs a click cycles through: those matching ``match`` (default 'logitech') if there are at least two, else every
    real output."""
    if match:
        wanted = [s for s in sinks if match.lower() in s.haystack]
        if len(wanted) >= 2:
            return wanted
    return list(sinks)


def next_sink(sinks, default, match):
    """The output after ``default`` in the cycle (the first one if the default is not part of it); None if nothing to
    switch to."""
    cand = candidates(sinks, match)
    if not cand:
        return None
    names = [s.name for s in cand]
    if default in names:
        nxt = cand[(names.index(default) + 1) % len(cand)]
    else:
        nxt = cand[0]
    return None if nxt.name == default else nxt


def available():
    return shutil.which("pactl") is not None


def _run(*args, timeout=3):
    try:
        done = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout)
        return done.stdout if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def read_state():
    """(sinks, default sink name) now, or None if the sound server cannot be reached."""
    text = _run("-f", "json", "list", "sinks")
    if text is None:
        return None
    default = (_run("get-default-sink") or "").strip()
    return parse_sinks(text), default


def set_default(name):
    """Make ``name`` the default output and move whatever is playing to it, so the change is heard at once."""
    if _run("set-default-sink", name) is None:
        return False
    for line in (_run("list", "short", "sink-inputs") or "").splitlines():
        parts = line.split("\t")
        if parts and parts[0].isdigit():
            _run("move-sink-input", parts[0], name)
    return True


class Watcher:
    """Keeps the taskbar's idea of the sound output current. ``on_state((sinks, default))`` is called from a worker thread
    whenever it changes (wrap it in GLib.idle_add)."""

    def __init__(self, on_state, match="logitech"):
        self.on_state = on_state
        self.match = match
        self.state = None
        self._stop = threading.Event()
        self._proc = None
        self._thread = None
        self._lock = threading.Lock()
        self._vol = None
        self._vol_thread = None

    def start(self):
        if self._thread is None and available():
            self._thread = threading.Thread(target=self._loop, name="sdesk-audio", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass

    def _publish(self):
        st = read_state()
        if st is not None and (self.state is None or (st[1], st[0]) != (self.state[1], self.state[0])):
            self.state = st
            self.on_state(st)

    def _loop(self):
        while not self._stop.is_set():
            self._publish()
            try:
                self._proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              text=True)
            except OSError:
                self._stop.wait(5)
                continue
            while not self._stop.is_set():
                ready, _, _ = select.select([self._proc.stdout], [], [], 5.0)     # also look every 5 s, whatever happens
                if ready:
                    line = self._proc.stdout.readline()
                    if not line:
                        break                                                      # the server went away: reconnect
                    if " sink " not in line and "server" not in line:
                        continue
                    self._stop.wait(0.15)                                          # one burst of events -> one look
                    while select.select([self._proc.stdout], [], [], 0)[0]:
                        if not self._proc.stdout.readline():
                            break
                self._publish()
            try:
                self._proc.kill()
            except OSError:
                pass
            self._stop.wait(2)

    def set_volume(self, percent):
        """Slider: set the default output's volume. Calls arriving while one is being applied are merged into the latest."""
        with self._lock:
            self._vol = max(0, min(100, int(percent)))
            if self._vol_thread is not None and self._vol_thread.is_alive():
                return
            self._vol_thread = threading.Thread(target=self._apply_volume, name="sdesk-audio-vol", daemon=True)
            self._vol_thread.start()

    def _apply_volume(self):
        while True:
            with self._lock:
                value, self._vol = self._vol, None
            if value is None:
                break
            _run("set-sink-mute", "@DEFAULT_SINK@", "0")
            _run("set-sink-volume", "@DEFAULT_SINK@", f"{value}%")
        self._publish()

    def toggle_mute(self):
        def work():
            _run("set-sink-mute", "@DEFAULT_SINK@", "toggle")
            self._publish()
        threading.Thread(target=work, name="sdesk-audio-mute", daemon=True).start()

    def cycle(self):
        """Click on the taskbar item: next output."""
        if self.state is None:
            return
        nxt = next_sink(self.state[0], self.state[1], self.match)
        if nxt is not None:
            self.choose(nxt.name)

    def choose(self, name):
        def work():
            set_default(name)
            self._publish()
        threading.Thread(target=work, name="sdesk-audio-set", daemon=True).start()
