"""Record the demo videos (MP4 + GIF) and screenshots for the README, in the isolated lab only.

    lab/start_lab.sh                 # first: the lab desktop (Xvfb :99 + its own kwin)
    .venv/bin/python lab/record_media.py [clip ...]       # all clips, or just the named ones

Nothing of the real desktop is recorded: the lab has invented windows (Files, Mail, Music, ...) and a made-up wallpaper.
The clips drive the lab with xdotool (the real mouse and keyboard are not touched, DISPLAY is :99) and are written to
docs/media/. Needs ffmpeg and xdotool.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "media")
LAB = os.environ.get("LAB", "/tmp/sdesk-lab")
DISPLAY = os.environ.get("DISPLAY") if os.environ.get("XDG_RUNTIME_DIR") == f"{LAB}/run" else ":99"   # . "$LAB/env"
ENV = {**os.environ, "DISPLAY": DISPLAY, "XDG_CONFIG_HOME": f"{LAB}/config", "XDG_DATA_HOME": f"{LAB}/data",
       "XDG_CACHE_HOME": f"{LAB}/cache", "XDG_RUNTIME_DIR": f"{LAB}/run"}
PY = os.path.join(ROOT, ".venv", "bin", "python")
W, H = 5120, 1440
BAR_Y = 1406                                        # the middle of the taskbar boxes
PORT = 47651                                        # the lab's tab server (the real sdesk keeps 47631)
HOME = f"{LAB}/home"                                # a made-up home: the thread states sdesk reads are invented ones
WINDOWS = [("Projects - Files", "files", "3b6ea5"), ("Mail", "mail", "3ba37a"), ("Music", "music", "b23b6e"),
           ("Terminal", "terminal", "2f3b4a"), ("Claude", "com.anthropic.claude", "c9693f"),
           ("T3 Code", "com.t3tools.t3code", "5b4bb7")]          # plus the stand-in Chrome (lab/demo_chrome.py)
# Taskbar box centres on the 5120 px lab screen (measured on a screenshot; the row is centred, so they only hold for this set
# of windows). The Chrome tab buttons are named after their tab.
BUTTON_X = {"Files": 1756, "Mail": 1850, "Music": 1936, "Terminal": 2040, "Claude": 2156, "T3": 2294,
            "Inbox": 2410, "Dashboard": 2506, "Pricing": 2626, "Docs": 2764, "Design": 2896, "More": 2978, "Plus": 3052}
BAR_CROP = (1500, 1352, 1900, 88)                   # x, y, w, h of the bar: for the clips that are about the bar only


def xdo(*args):
    subprocess.run(["xdotool", *args], env=ENV, check=False)


def pause(s):
    time.sleep(s)


def move(x, y, steps=1, pace=0.0):
    xdo("mousemove", str(int(x)), str(int(y)))
    pause(pace)


def glide(x0, y0, x1, y1, steps=24, pace=0.02):
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)
        move(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, pace=pace)


def click_button(name, wait=1.2):
    move(BUTTON_X[name], BAR_Y)
    pause(0.35)
    xdo("click", "1")
    pause(wait)


def window_geometry(title):
    out = subprocess.run(["xdotool", "search", "--name", f"^{title}$"], env=ENV, capture_output=True, text=True).stdout.split()
    for wid in out:
        g = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid], env=ENV, capture_output=True, text=True).stdout
        d = dict(line.split("=") for line in g.split())
        if int(d["WIDTH"]) > 300:
            return int(d["X"]), int(d["Y"]), int(d["WIDTH"]), int(d["HEIGHT"])
    raise SystemExit(f"window {title!r} not found")


class Recording:
    def __init__(self, name, seconds, crop=None, width=1280):
        self.crop, self.width = crop, width
        os.makedirs(OUT, exist_ok=True)
        self.name, self.raw = name, os.path.join(OUT, f"{name}.raw.mkv")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-framerate", "25", "-video_size", f"{W}x{H}", "-i", DISPLAY,
             "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", self.raw], env=ENV)
        pause(0.6)

    def finish(self):
        self.proc.wait()
        mp4, gif = (os.path.join(OUT, f"{self.name}.{ext}") for ext in ("mp4", "gif"))
        # the ultra-wide screen is cut to what matters (the strip and the bar), then scaled
        crop = f"crop={self.crop[2]}:{self.crop[3]}:{self.crop[0]}:{self.crop[1]}" if self.crop else f"crop={W - 400}:{H}:0:0"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", self.raw, "-vf", f"{crop},scale={max(self.width, 1280) * 3 // 2}:-2", "-c:v", "libx264",
                        "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4], check=True)
        palette = os.path.join(OUT, f"{self.name}.palette.png")
        vf = f"{crop},fps=12,scale={self.width}:-2:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", self.raw, "-vf", f"{vf},palettegen=max_colors=96", palette], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", self.raw, "-i", palette, "-lavfi",
                        f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", gif], check=True)
        os.remove(self.raw)
        os.remove(palette)
        print(f"{self.name}: {os.path.getsize(mp4) // 1024} KB mp4, {os.path.getsize(gif) // 1024} KB gif", flush=True)


def shot(name, crop=None):
    path = os.path.join(OUT, f"{name}.png")
    vf = f"crop={crop}" if crop else f"crop={W - 400}:{H}:0:0,scale=2000:-2"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", f"{W}x{H}", "-i", DISPLAY, "-frames:v", "1",
                    "-vf", vf, path], env=ENV, check=True)
    print(f"{name}: {os.path.getsize(path) // 1024} KB png", flush=True)


# ------------------------------------------------------------------ the lab's apps and fake thread states
class FakeThreads:
    """Invented Claude Code sessions (files in a made-up ~/.claude/sessions, owned by live sleep processes) and an invented
    T3 Code database, so the thread badges can be recorded without any real thread."""

    def __init__(self, reuse=False):
        import json
        import sqlite3
        import types
        self.json, self.sqlite3 = json, sqlite3
        self.sessions = os.path.join(HOME, ".claude", "sessions")
        os.makedirs(self.sessions, exist_ok=True)
        os.makedirs(os.path.join(HOME, ".t3", "userdata"), exist_ok=True)
        self.db = os.path.join(HOME, ".t3", "userdata", "statev2.sqlite")
        if reuse:                                           # the lab was set up before: use its sessions and database
            pids = sorted(int(f.split(".")[0]) for f in os.listdir(self.sessions) if f.endswith(".json"))
            self.procs = [types.SimpleNamespace(pid=pid) for pid in pids]
            return
        self.procs = [subprocess.Popen(["sleep", "7200"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(3)]
        if os.path.exists(self.db):
            os.remove(self.db)
        con = sqlite3.connect(self.db)
        con.executescript("""
            create table orchestration_v2_projection_threads (thread_id, title, archived_at, deleted_at);
            create table orchestration_v2_projection_runs (thread_id, status, requested_at, completed_at);
            create table orchestration_v2_projection_runtime_requests (thread_id, status, created_at, resolved_at);""")
        con.close()
        self.claude("a", 0, "Refactor the invoice export", "busy")
        self.claude("b", 1, "Write release notes", "busy")
        self.claude("c", 2, "Fix flaky login test", "idle")
        self.t3("t1", "Add dark mode", "busy")
        self.t3("t2", "Migrate the database", "busy", asking=True)

    def claude(self, key, n, name, status):
        pid = self.procs[n].pid
        with open(f"/proc/{pid}/stat") as f:
            start = f.read().rsplit(")", 1)[1].split()[19]
        with open(os.path.join(self.sessions, f"{pid}.json"), "w") as f:
            self.json.dump({"pid": pid, "sessionId": f"demo-{key}", "cwd": "/demo", "entrypoint": "claude-desktop", "procStart": start,
                            "name": name, "status": status}, f)

    def t3(self, tid, title, status, asking=False, done=False):
        now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        con = self.sqlite3.connect(self.db)
        con.execute("delete from orchestration_v2_projection_threads where thread_id = ?", (tid,))
        con.execute("delete from orchestration_v2_projection_runs where thread_id = ?", (tid,))
        con.execute("delete from orchestration_v2_projection_runtime_requests where thread_id = ?", (tid,))
        con.execute("insert into orchestration_v2_projection_threads values (?, ?, null, null)", (tid, title))
        con.execute("insert into orchestration_v2_projection_runs values (?, ?, ?, ?)",
                    (tid, "completed" if done else "running", now, now if done else None))
        if asking:
            con.execute("insert into orchestration_v2_projection_runtime_requests values (?, 'pending', ?, null)", (tid, now))
        con.commit()
        con.close()

    def stop(self):
        for p in self.procs:
            p.terminate()


def setup():
    """Open the lab's windows and sdesk on :99 (the lab itself must run: lab/start_lab.sh)."""
    subprocess.run([PY, os.path.join(ROOT, "lab", "set_wallpaper.py")], env=ENV, capture_output=True)
    for title, cls, colour in WINDOWS:
        subprocess.Popen([PY, os.path.join(ROOT, "lab", "demo_window.py"), title, cls, colour], env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pause(0.5)
    subprocess.Popen([PY, os.path.join(ROOT, "lab", "demo_chrome.py"), str(PORT)], env=ENV, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    subprocess.Popen([PY, os.path.join(ROOT, "lab", "demo_sidebar.py")], env=ENV, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    pause(2)
    threads = FakeThreads()
    log = open(f"{LAB}/sdesk.log", "w")
    sd = subprocess.Popen([PY, "-m", "sdesk", "--sound", "off", "--chrome-port", str(PORT), "--nag-after", "1", "--nag-every", "1"],
                          env={**ENV, "HOME": HOME}, cwd=ROOT, stdout=log, stderr=log)
    open(f"{LAB}/sdesk.pid", "w").write(str(sd.pid))
    pause(7)
    return threads


# ------------------------------------------------------------------ the clips
def clip_scroll():
    r = Recording("scroll", 9)
    move(2500, 700)
    pause(0.4)
    xdo("keydown", "Super_L")
    for _ in range(9):
        xdo("click", "5")
        pause(0.13)
    pause(0.5)
    for _ in range(14):
        xdo("click", "4")
        pause(0.13)
    pause(0.4)
    xdo("keyup", "Super_L")
    r.finish()


def clip_focus():
    r = Recording("focus-and-minimise", 12)
    for name in ("Files", "Music", "Claude"):
        click_button(name, 1.4)
    click_button("Claude", 1.4)                      # the active one: minimise
    click_button("Claude", 1.4)                      # and back
    r.finish()


def drag_box(x_from, x_to):
    move(x_from, BAR_Y)
    pause(0.5)
    xdo("mousedown", "1")
    pause(0.2)
    step = 40 if x_to > x_from else -40
    glide(x_from, BAR_Y, x_from + step, BAR_Y - 6, 6)
    glide(x_from + step, BAR_Y - 6, x_to, BAR_Y - 10, 40, 0.025)
    pause(0.9)
    xdo("mouseup", "1")
    pause(1.6)


def clip_reorder():
    """Drag Mail to the right of T3 Code. Record this one last: the order is different afterwards."""
    r = Recording("reorder-taskbar", 9)
    drag_box(BUTTON_X["Mail"], BUTTON_X["T3"] + 20)
    r.finish()


def clip_resize():
    click_button("Terminal", 1.5)
    x, y, w, h = window_geometry("Terminal")
    gap_x = x + w + 4
    r = Recording("resize-by-the-gap", 8)
    move(gap_x, 600)
    pause(0.6)
    xdo("mousedown", "1")
    pause(0.3)
    glide(gap_x, 600, gap_x + 260, 600, 30, 0.03)
    pause(0.4)
    glide(gap_x + 260, 600, gap_x - 40, 600, 36, 0.03)
    pause(0.4)
    xdo("mouseup", "1")
    pause(1.0)
    r.finish()


def clip_chrome():
    """The Chrome window's tabs as one button group: click other tabs, the window title follows."""
    r = Recording("chrome-tab-group", 13, crop=BAR_CROP, width=1500)
    click_button("Dashboard", 1.0)                      # focus the Chrome window first (its active tab is lit)
    for name in ("Design", "Docs", "Inbox", "Pricing"):
        move(BUTTON_X[name], BAR_Y)
        pause(0.9)                                      # hover: the tooltip shows the full title
        xdo("click", "1")
        pause(1.5)
    r.finish()


def clip_threads():
    """Thread badges: orange = waits for you, green = done, blue (with a light running round it) = working."""
    t = FakeThreads(reuse=True)
    t.claude("a", 0, "Refactor the invoice export", "busy")
    t.claude("b", 1, "Write release notes", "busy")
    t.t3("t1", "Add dark mode", "busy")
    t.t3("t2", "Migrate the database", "busy", asking=True)
    pause(4)
    r = Recording("thread-badges", 16, crop=BAR_CROP, width=1500)
    pause(2.5)
    t.claude("a", 0, "Refactor the invoice export", "idle")       # one Claude thread finishes: a green badge
    pause(3.5)
    t.t3("t1", "Add dark mode", "busy", done=True)                # a T3 thread finishes too
    pause(3.5)
    move(BUTTON_X["Claude"], BAR_Y)                               # looking at it (a click) clears its green badge
    pause(0.5)
    xdo("click", "1")
    pause(3)
    r.finish()


def clip_nag():
    """A window minimised for long enough shakes now and then (the lab runs with --nag-after 1)."""
    click_button("Music", 1.0)
    click_button("Music", 1.0)                          # the active one: minimise
    print("waiting for the shake ...", flush=True)
    pause(56)
    r = Recording("minimised-window-shake", 10, crop=BAR_CROP, width=1500)
    r.finish()
    click_button("Music", 1.0)


def clip_menu():
    move(BUTTON_X["Plus"], BAR_Y)                      # the "+" at the end of the bar
    pause(0.3)
    xdo("click", "1")
    pause(1.0)
    shot("add-window-menu", crop=f"1400:700:{BUTTON_X['Plus'] - 900}:{H - 700}")
    xdo("key", "Escape")


CLIPS = {"chrome": clip_chrome, "threads": clip_threads, "scroll": clip_scroll, "focus": clip_focus, "resize": clip_resize,
         "menu": clip_menu, "nag": clip_nag, "reorder": clip_reorder}


def clip_shots():
    click_button("Dashboard", 1.0)
    shot("overview")
    shot("taskbar", crop=f"{BAR_CROP[2]}:{BAR_CROP[3]}:{BAR_CROP[0]}:{BAR_CROP[1]}")


CLIPS["shots"] = clip_shots


def main():
    if "setup" in sys.argv[1:]:
        threads = setup()
        print("lab ready; fake threads owned by pids", [p.pid for p in threads.procs], flush=True)
        return
    for n in sys.argv[1:] or list(CLIPS):
        CLIPS[n]()
        pause(0.6)


if __name__ == "__main__":
    main()
