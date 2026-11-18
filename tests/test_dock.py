import pytest

from sdesk import dock

UNIT = """# /usr/lib/systemd/user/dde-shell@DDE.service
[Service]
Type=dbus
ExecStart=/usr/bin/dde-shell -C %I --serviceName=org.deepin.dde.shell -d org.deepin.ds.desktop
Restart=always
"""


def test_dropin_keeps_the_original_command_and_adds_the_dock_to_the_disabled_applets():
    text = dock.dropin_text(UNIT)
    lines = text.splitlines()
    assert lines[0] == "[Service]"
    assert lines[1] == "ExecStart="                      # clears the original, then redefines it
    assert lines[2] == ("ExecStart=/usr/bin/dde-shell -C %I --serviceName=org.deepin.dde.shell "
                        "-d org.deepin.ds.desktop -d org.deepin.ds.dock")


def test_dropin_waits_for_the_shell_bus_name_not_the_dock_one():
    # Type=dbus waits for BusName; the dock owned org.deepin.dde.Dock1, which won't exist without it
    assert dock.dropin_text(UNIT).splitlines()[-1] == "BusName=org.deepin.dde.shell"


def test_dropin_is_idempotent_if_dock_already_disabled():
    once = dock.dropin_text(UNIT)
    again = dock.dropin_text("[Service]\n" + once.splitlines()[2] + "\n")
    assert again.count("org.deepin.ds.dock") == 1


def test_unit_without_execstart_is_refused():
    with pytest.raises(ValueError):
        dock.dropin_text("[Service]\nType=dbus\n")


def test_dropin_lives_in_the_volatile_runtime_dir():
    assert "/systemd/user/dde-shell@DDE.service.d/" in dock.DROPIN
    assert not dock.DROPIN.startswith(("/etc", "/usr", "/home"))      # wiped at logout, never persisted
