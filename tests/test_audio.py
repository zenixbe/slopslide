import json

from sdesk import audio


def sink(name, desc, vendor="", product="", bus="", virtual=None):
    props = {"device.vendor.name": vendor, "device.product.name": product, "device.bus": bus}
    if virtual:
        props["node.virtual"] = virtual
    return {"name": name, "description": desc, "properties": props}


SINKS = json.dumps([
    sink("alsa_output.pci-0000_00_1f.3.iec958-stereo", "Built-in Audio Digital Stereo (IEC958)", "Intel Corporation", "Comet Lake", "pci"),
    sink("alsa_output.usb-Logitech_PRO_000.analog-stereo", "PRO Analog Stereo", "Logitech, Inc.", "PRO", "usb"),
    sink("alsa_output.pci-0000_01_00.1.hdmi-stereo", "TU104 HD Audio Controller Digital Stereo (HDMI)", "NVIDIA Corporation", "TU104", "pci"),
    sink("null-sink", "null-sink Audio/Sink sink"),
    sink("bluez_output.C0_28.1", "Logitech BT Adapter", "", "", "bluetooth"),
])


def test_labels_are_short_and_name_the_brand():
    sinks = {s.name: s for s in audio.parse_sinks(SINKS)}
    assert sinks["alsa_output.usb-Logitech_PRO_000.analog-stereo"].label == "Logitech PRO"
    assert sinks["bluez_output.C0_28.1"].label == "Logitech BT"
    assert sinks["alsa_output.pci-0000_00_1f.3.iec958-stereo"].label == "Built-in Audio"


def test_virtual_null_sinks_are_left_out_and_kinds_are_guessed():
    sinks = audio.parse_sinks(SINKS)
    assert "null-sink" not in [s.name for s in sinks] and len(sinks) == 4
    kinds = {s.label: s.kind for s in sinks}
    assert kinds["Logitech PRO"] == "headset" and kinds["Logitech BT"] == "headset" and kinds["Built-in Audio"] == "speakers"
    assert [s.kind for s in sinks if "HDMI" in s.description] == ["display"]


def test_garbage_from_pactl_gives_no_sinks():
    assert audio.parse_sinks("not json") == [] and audio.parse_sinks("[]") == []


def test_a_click_cycles_between_the_logitech_outputs_only():
    sinks = audio.parse_sinks(SINKS)
    pro, bt = "alsa_output.usb-Logitech_PRO_000.analog-stereo", "bluez_output.C0_28.1"
    assert audio.next_sink(sinks, bt, "logitech").name == pro
    assert audio.next_sink(sinks, pro, "logitech").name == bt                 # wraps, skipping built-in and HDMI
    assert audio.next_sink(sinks, "alsa_output.pci-0000_01_00.1.hdmi-stereo", "logitech").name == pro   # from elsewhere: first one


def test_without_two_matches_every_real_output_takes_part():
    sinks = audio.parse_sinks(SINKS)
    assert len(audio.candidates(sinks, "nomatch")) == 4
    only_one = [s for s in sinks if "pro" in s.haystack.split("analog")[0] or s.label == "Built-in Audio"]
    assert len(audio.candidates(only_one, "logitech")) == len(only_one)       # one Logitech is not a cycle: use all


def test_nothing_to_switch_to_gives_none():
    one = audio.parse_sinks(json.dumps([sink("a", "A")]))
    assert audio.next_sink(one, "a", "logitech") is None and audio.next_sink([], "", "logitech") is None


def test_volume_and_mute_are_read_from_the_loudest_channel():
    raw = json.loads(SINKS)
    raw[1]["volume"] = {"front-left": {"value_percent": "45%"}, "front-right": {"value_percent": "52%"}}
    raw[1]["mute"] = True
    pro = [s for s in audio.parse_sinks(json.dumps(raw)) if s.label == "Logitech PRO"][0]
    assert pro.volume == 52 and pro.muted is True
    other = [s for s in audio.parse_sinks(json.dumps(raw)) if s.label == "Logitech BT"][0]
    assert other.volume == 0 and other.muted is False                  # nothing reported: quiet, not muted


def test_slider_positions_map_to_percent_and_stay_in_range():
    assert audio.volume_from_x(110, 10, 200) == 50
    assert audio.volume_from_x(-50, 10, 200) == 0 and audio.volume_from_x(999, 10, 200) == 100
    assert audio.volume_from_x(5, 10, 0) == 0                          # a zero-width track must not divide by zero


def test_a_wheel_notch_moves_five_percent_and_stays_within_zero_and_hundred():
    assert audio.step_volume(50, +1) == 55 and audio.step_volume(50, -1) == 45
    assert audio.step_volume(52, +1) == 55                             # lands on a multiple of five
    assert audio.step_volume(100, +1) == 100 and audio.step_volume(0, -1) == 0
