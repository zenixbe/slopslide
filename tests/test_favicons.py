import sqlite3

from sdesk import favicons


def profile(tmp_path, pages, icons):
    """A fake Chrome profile: pages = [(url, title, visited)], icons = [(page_url, width, bytes)]."""
    h = sqlite3.connect(tmp_path / "History")
    h.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, last_visit_time INTEGER)")
    h.executemany("INSERT INTO urls (url, title, last_visit_time) VALUES (?,?,?)", pages)
    h.commit(); h.close()
    f = sqlite3.connect(tmp_path / "Favicons")
    f.execute("CREATE TABLE icon_mapping (id INTEGER PRIMARY KEY, page_url TEXT, icon_id INTEGER)")
    f.execute("CREATE TABLE favicon_bitmaps (id INTEGER PRIMARY KEY, icon_id INTEGER, width INTEGER, image_data BLOB)")
    for n, (url, width, data) in enumerate(icons, 1):
        f.execute("INSERT INTO icon_mapping (page_url, icon_id) VALUES (?,?)", (url, n))
        f.execute("INSERT INTO favicon_bitmaps (icon_id, width, image_data) VALUES (?,?,?)", (n, width, data))
    f.commit(); f.close()
    return str(tmp_path)


def test_the_window_title_loses_chromes_suffix():
    assert favicons.page_title("Inbox (3) - Gmail - Google Chrome") == "Inbox (3) - Gmail"
    assert favicons.page_title("Docs - Google Chrome - Work") == "Docs"
    assert favicons.page_title("Chromium") == "Chromium"                      # no suffix: left alone
    assert favicons.page_title("") == ""


def test_only_chrome_windows_are_looked_up():
    assert favicons.is_chrome("Google-chrome") and favicons.is_chrome("chromium")
    assert not favicons.is_chrome("slack") and not favicons.is_chrome("")


def test_a_title_finds_the_icon_of_its_latest_visit(tmp_path):
    d = profile(tmp_path,
                [("https://old.example/a", "Dashboard", 1), ("https://new.example/a", "Dashboard", 9)],
                [("https://old.example/a", 32, b"OLD"), ("https://new.example/a", 32, b"NEW")])
    assert favicons.Chrome([d]).icon_png("Dashboard - Google Chrome") == b"NEW"


def test_the_biggest_icon_wins_and_another_page_of_the_site_will_do(tmp_path):
    d = profile(tmp_path,
                [("https://site.example/deep/page", "Deep page", 5)],
                [("https://site.example/", 16, b"SMALL"), ("https://site.example/", 64, b"BIG")])
    assert favicons.Chrome([d]).icon_png("Deep page - Google Chrome") == b"BIG"


def test_unknown_titles_and_missing_profiles_give_nothing(tmp_path):
    d = profile(tmp_path, [("https://a.example/", "Known", 1)], [])
    c = favicons.Chrome([d, str(tmp_path / "nope")])
    assert c.icon_png("Known - Google Chrome") is None and c.icon_png("Unknown - Google Chrome") is None
    assert c.icon_png("") is None


def test_a_miss_is_retried_after_a_while_but_a_hit_is_remembered(tmp_path):
    now = [100.0]
    d = profile(tmp_path, [], [])
    c = favicons.Chrome([d], clock=lambda: now[0])
    assert c.icon_png("Fresh - Google Chrome") is None
    h = sqlite3.connect(tmp_path / "History")
    h.execute("INSERT INTO urls (url, title, last_visit_time) VALUES ('https://f.example/', 'Fresh', 1)"); h.commit(); h.close()
    f = sqlite3.connect(tmp_path / "Favicons")
    f.execute("INSERT INTO icon_mapping (page_url, icon_id) VALUES ('https://f.example/', 1)")
    f.execute("INSERT INTO favicon_bitmaps (icon_id, width, image_data) VALUES (1, 32, X'AA')"); f.commit(); f.close()
    assert c.icon_png("Fresh - Google Chrome") is None                         # cached miss, too soon
    now[0] += favicons.MISS_TTL + 1
    assert c.icon_png("Fresh - Google Chrome") == b"\xaa"
    now[0] += 10_000
    assert c.icon_png("Fresh - Google Chrome") == b"\xaa"                      # a hit stays


def test_a_known_url_finds_its_icon_without_the_history(tmp_path):
    d = profile(tmp_path, [], [("https://site.example/", 16, b"S"), ("https://site.example/", 32, b"B")])
    (tmp_path / "History").unlink()                                            # not needed when the URL is known
    c = favicons.Chrome([d])
    assert c.icon_png_for_url("https://site.example/deep/page") == b"B"       # another page of the site
    assert c.icon_png_for_url("chrome://newtab/") is None and c.icon_png_for_url("") is None
    assert c.icon_png_for_url("https://unknown.example/x") is None
