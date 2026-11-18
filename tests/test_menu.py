import json

from sdesk import menu


def test_defaults_only_offer_installed_apps(monkeypatch):
    monkeypatch.setattr(menu.shutil, "which", lambda b: "/usr/bin/" + b if b in ("dde-file-manager", "deepin-terminal") else None)
    titles = [i.title for i in menu.load_menu_items("/nonexistent/menu.json")]
    assert titles == ["File Manager", "Terminal"]            # no Chrome here


def test_fallback_chain_counts_as_installed(monkeypatch):
    monkeypatch.setattr(menu.shutil, "which", lambda b: "/usr/bin/x-www-browser" if b == "x-www-browser" else None)
    assert [i.title for i in menu.load_menu_items("/nonexistent")] == ["Chrome"]


def test_user_config_replaces_defaults(tmp_path):
    f = tmp_path / "menu.json"
    f.write_text(json.dumps({"items": [{"title": "Shotcut", "exec": "shotcut", "description": "video"}]}))
    items = menu.load_menu_items(str(f))
    assert [(i.title, i.exec, i.icon) for i in items] == [("Shotcut", "shotcut", "application-x-executable")]


def test_broken_config_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(menu.shutil, "which", lambda b: "/bin/" + b)
    f = tmp_path / "menu.json"
    f.write_text("{not json")
    assert len(menu.load_menu_items(str(f))) == 3


def test_app_names_are_made_readable():
    assert menu.app_name("com.anthropic.Claude") == "Claude"
    assert menu.app_name("google-chrome") == "Google Chrome"
    assert menu.app_name("") == "this app"


def test_window_menu_offers_close_and_force_quit_with_an_honest_warning():
    called = []
    items = menu.window_menu_items("google-chrome", 3, lambda: called.append("close"), lambda: called.append("kill"))
    assert [i.title for i in items] == ["Close", "Force Quit"]
    assert "all 3 of its windows" in items[1].description          # force quit takes the whole app with it
    items[0].action(); items[1].action()
    assert called == ["close", "kill"]
    single = menu.window_menu_items("xterm", 1, None, None)
    assert "windows" not in single[1].description


def test_program_name_prefers_the_desktop_file_name_and_falls_back_to_the_class():
    from sdesk.menu import program_name
    names = {"google-chrome": "Google Chrome", "slack": "Slack"}
    assert program_name("Google-chrome", names) == "Google Chrome"
    assert program_name("slack", names) == "Slack"
    assert program_name("com.anthropic.Claude", names) == "Claude"            # no desktop file: made from the class
    assert program_name("org.example.slack", names) == "Slack"                # last part of a reverse-DNS class matches
    assert program_name("", names) == "this app"


def test_a_file_manager_box_shows_the_folder_other_programs_show_their_name(monkeypatch):
    from sdesk import menu
    monkeypatch.setattr(menu, "_PROGRAMS", {"dde-file-manager": "File Manager", "slack": "Slack"})
    assert menu.window_label("dde-file-manager", "smoor — File Manager") == "smoor"
    assert menu.window_label("dde-file-manager", "Home — File Manager") == "Home"
    assert menu.window_label("dde-file-manager", "/media/demo/Disk - Files") == "/media/demo/Disk"
    assert menu.window_label("dde-file-manager", "Some folder") == "Some folder"          # no suffix: as it is
    assert menu.window_label("dde-file-manager", "") == "File Manager"                    # no title yet: the program
    assert menu.window_label("slack", "Didier (DM) - Blue Fox Marketing") == "Slack"      # only file managers show the title


def test_a_chrome_box_shows_the_tab_title_cut_to_fifteen_characters(monkeypatch):
    from sdesk import menu
    monkeypatch.setattr(menu, "_PROGRAMS", {"google-chrome": "Google Chrome"})
    assert menu.window_label("Google-chrome", "Inbox - Google Chrome") == "Inbox"
    label = menu.window_label("Google-chrome", "Dashboard ‹ Ellen — WordPress - Google Chrome")
    assert label == "Dashboard ‹ El…" and len(label) == 15
    assert menu.window_label("Google-chrome", "Exactly 15 chars - Google Chrome") == "Exactly 15 cha…"
    assert menu.window_label("Google-chrome", "123456789012345 - Google Chrome") == "123456789012345"      # fits: no ellipsis
    assert menu.window_label("Google-chrome", "  Many   spaces   here   today - Google Chrome - Work") == "Many spaces he…"
    assert menu.window_label("Google-chrome", "") == "Google Chrome"                                      # no title yet: the program
    assert menu.window_label("Google-chrome", " - Google Chrome") == "Google Chrome"


def test_shorten_keeps_short_texts_and_never_exceeds_the_limit():
    from sdesk.menu import shorten
    assert shorten("abc") == "abc" and shorten("") == "" and shorten(None) == ""
    assert shorten("x" * 40) == "x" * 14 + "…" and len(shorten("x" * 40)) == 15
    assert shorten("ab cd ef gh ij kl mn op", 8) == "ab cd e…"
    assert shorten("trailing space here", 9) == "trailing…"                                              # no space before the ellipsis
