# SlopSlide in detail

The full reference: every option, how it works, what survives a crash. The short version with videos is the [README](../README.md).
The Python package and the command are called `sdesk`.

Windows never stack: they sit side by side in one horizontal strip with margins, and you scroll
along it. A custom taskbar mirrors the strip, one button per window.

    ./run.sh            # start (switches Deepin's own dock off while running)
    Ctrl+C              # stop: every window goes back where it was, dock comes back

| You do | What happens |
|---|---|
| **Super + mouse wheel** | scroll the desktop left/right (plain wheel and Ctrl+wheel stay untouched, so apps keep working). You can scroll until the first or last window is in the middle of the screen |
| drag a window by its titlebar | siblings slide aside and a blue outline shows the drop slot; release to reorder. Dragging near the screen edge auto-scrolls |
| drag a taskbar button | the box lifts out as a tilted, glass-like ghost (frosted over what is under it), a gap glides to the drop spot, the desktop previews the same drop; release to reorder. Order of buttons == order of windows, always |
| click a taskbar item | focuses the window and centres it. Clicking the item that is already active (highlighted and focused) minimises it; click again to bring it back |
| resize a window (any edge) | the width is remembered; neighbours are pushed along and keep their gap. The height always snaps back: every window has the same one |
| drag the gap between two windows | resizes the window left of it (the ones to the right are pushed along), with a double-arrow cursor. Works for every window, also those without a border of their own (Chrome, Electron apps); the margin before the first window resizes it from the left |
| click a taskbar button | focus the window and **centre** it on the screen, even the first or last one (then there is simply empty desktop on one side) |
| **right-click a taskbar button** | menu: **Close** (asks nicely, like the × button) or **Force Quit** (kills the whole application, so all of its windows go, and unsaved work is lost; the menu says how many) |
| middle-click a taskbar button | close the window |
| minimise a window, or *Show Desktop* | its button stays (dimmed) and keeps its place in the order; its slot closes up. Click the button to bring it back exactly where it was. When *every* window is hidden nothing is moved, so it all returns as it was |
| **right-click between two windows** (the gap), or just outside the first/last window | menu to add a window at exactly that spot: the slot opens, the app launches, its window lands in it |
| **right-click between two taskbar buttons**, or click the **+** at the end | same menu, anchored above the bar |
| open a new window / new instance any other way | appears right of the focused window and gets its own button |

Each window — also several of the same app — is its own button, so button order always equals window order.
The taskbar is just a row of separate glass boxes (no wrapper, no background), centred and only as wide as its boxes;
each box is as long as its title (up to a cap). Only the focused window's box is highlighted (teal), by colour only: a box
never changes size when it becomes active or is minimised.

Everything on it is animated, so nothing changes size suddenly: widths ease (a new title, a window opening or closing),
hover and active colours fade, and when you drag a box it is lifted out as a tilted ghost that follows the pointer while
a gap of its own size glides to wherever it would land, with the neighbours sliding aside. Dropping puts it into the gap
without a single pixel jumping. The bar floats 8px above the screen edge (`--bar-gap`).

### Window effects (compositor) and the glass

sdesk has two looks and picks one automatically, also when you switch Deepin's window effects ("best visuals" etc.) on or
off while it runs (the bar rebuilds itself, the log says so):

* **Compositor on:** the bar window has an alpha channel. The gaps between boxes are really transparent, the boxes are
  translucent, kwin's blur effect frosts what is behind each box (`_KDE_NET_WM_BLUR_BEHIND_REGION`, rounded like the boxes),
  and the tilted drag ghost is translucent and blurred too. The alpha channel is also what keeps Deepin's compositor from
  drawing its 1px outline around the bar: it outlines every *opaque* window, and an opaque full-width bar showed up as
  full-width lines above and below the boxes.
* **No compositor:** nothing can be see-through, so the bar paints a copy of the wallpaper behind it: sharp between the
  boxes (so they look transparent) and blurred inside them (the glass), plus a flat tint and a thin light edge (no gradients).
  The copy is taken 2.5 s after start, once the windows have settled above the bar. If you change the wallpaper, refresh it
  with `kill -USR1 $(cat /tmp/sdesk/sdesk.pid)`.

## Launcher integration

A key-driven launcher (tap Super: search apps, switch windows) talks to sdesk over a
small local control socket, `$XDG_RUNTIME_DIR/sdesk.sock` (mode 0600, same user only): it lists the windows in strip order
and asks sdesk to focus *and centre* one. With sdesk running Deepin's own launcher is gone, so such a launcher (or the add-window menu) is how you start apps.

## If something goes wrong (and what survives a reboot)

**Nothing sdesk does survives a reboot or logout.** It is never started automatically, it does not edit any Deepin or kwin
configuration, and its one system-level change (starting `dde-shell` without its dock) is a temporary override kept in
`/run/user/<uid>/` (tmpfs). So a reboot always gives you a normal Deepin desktop, whatever happened before.

While it runs:

* **Normal exit** (Ctrl+C, `kill`, closing the terminal, logout): windows go back to where they were, the dock comes back.
  During a logout or reboot it does this without waiting on anything, so it cannot delay shutdown.
* **Crash, `kill -9`, out-of-memory kill:** a tiny independent *guardian* process (started with sdesk) notices within a
  second and puts every window back and brings the dock back. Window positions are kept in `/run/user/<uid>/sdesk/`.
* **Panic button**, from any terminal (also if the guardian itself is gone):

      cd /path/to/slopslide && ./run.sh --restore

  It stops sdesk (only processes started as `python -m sdesk`; never an editor or terminal that merely mentions the name),
  moves every window back where it was and restores the dock. Without a terminal: log out and in, or reboot.

### What happens to Deepin's dock

`./run.sh` starts `dde-shell` without its dock applet (`-d org.deepin.ds.dock`), through a temporary systemd override in
`$XDG_RUNTIME_DIR` that is removed when sdesk exits (and wiped at logout/reboot, so a crash can't make it permanent).
Starting and stopping sdesk therefore restarts dde-shell for a few seconds (notifications blink).
**The dock is more than a taskbar, so these go with it while sdesk runs:** the system tray area (clock, volume, network,
power/session menu), tray icons, and the app launcher / Super-key start menu (they are applets of the same panel).
Notifications keep working. Use a launcher or the add-window menu to start apps.
If you only want it out of the way, `.venv/bin/python -m sdesk --hide-dock` just sets it to "keep hidden" (it still
slides out at the bottom edge).

### The add-window menu

Keyboard driven: ↑↓ / j k / Ctrl+P N to move, Enter or 1–9 to pick, Esc or a click elsewhere to cancel.
Defaults are Chrome, File Manager and Terminal (only those that are installed). To change them create
`~/.config/sdesk/menu.json`:

    {"items": [
      {"title": "Chrome", "description": "New browser window", "exec": "google-chrome-stable --new-window", "icon": "google-chrome"},
      {"title": "File Manager", "description": "New file manager window", "exec": "dde-file-manager -n", "icon": "system-file-manager"}
    ]}

`exec` runs through the shell, so `a || b` fallbacks work. If the app never opens a window (for example it just
raised an existing one) the open slot closes by itself after 15 seconds.

## A floating sidebar on the right

If you keep a floating widget sidebar on the right edge (a widget panel, say), the windows use the area *left* of it:
the strip ends at the sidebar's left edge minus a small padding, windows centre on the middle of that area (the taskbar
does too), and windows parked on the right hide under the sidebar. sdesk finds the sidebar by itself (a mapped,
always-on-top *utility* window at the right edge that is tall and not huge; notification layers, ordinary windows and
sdesk's own parked windows never count) and looks again every ~0.6 s, so moving or resizing it just works.

    ./run.sh --edge-padding 24          # more room between the windows and the sidebar (default 16)
    ./run.sh --right-edge 400           # use exactly 400px instead of detecting
    ./run.sh --right-edge off           # ignore it

**Windows fold under a gap.** Windows never show under the sidebar: a click-through *dock-type* window holding a copy of
the wallpaper covers everything from one gap (default: the edge padding, 16px) left of the sidebar to the screen edge, fully
opaque and hard-edged. A window that slides right disappears under that edge, leaving a strip of wallpaper before the
sidebar; nothing shows through the gaps of a translucent or rounded sidebar either. The cover sits between the windows and
the sidebar (an override-redirect window would be painted above the sidebar, so it can't be that). It needs the compositor
("best visuals"); without it nothing is drawn and the windows simply stop at the sidebar. If the wallpaper can't be read
reliably, the cover is skipped rather than drawing a wrong colour.

    ./run.sh --edge-gap 32              # a wider gap (px)
    ./run.sh --edge-gap off             # no cover at all
    ./run.sh --edge-fade 64             # optional: a soft fade this wide left of the gap (off by default)

## Scrolling and centring

While the strip is narrower than the screen it sits centred as a whole. As soon as you scroll with the wheel or click a
taskbar button the strip may travel past its ends, so every window, including the first and last one, can be brought
to the exact middle of the screen. Windows that open on their own are only scrolled into view, not forced to the middle.

## Size of windows

A window that opens *after* sdesk has started is at most a third of the screen wide (`--new-width 0.25` for a quarter);
apps like the file manager or Chrome tend to open far wider. Windows that were already open keep their size, smaller new
windows keep their width, and you can still drag any window wider by hand: the cap only decides the initial size.

Every window gets the full height of the strip (a fixed height, whatever it opened with). Many apps put their own size
back a moment after opening, so sdesk checks about eight times a second and says the size again, a few times in a row. A
window that really cannot comply (a minimum or maximum size) is then taken as it is and the layout follows it. Heights you
set by hand are not remembered: all windows always share one height, so dragging a top or bottom edge snaps back (the width
you drag stays).

## Taskbar names, icons and forgotten windows

* **Names.** A box shows the program's name (from its desktop file: "Google Chrome", "Slack"). A file manager shows the
  folder instead ("smoor", "Home"), because there the folder tells two windows apart. A Chrome box shows the title of the
  tab it is showing, cut to 15 characters with an ellipsis ("Dashboard ‹ El…"). The tooltip always has the full title.
* **Chrome favicon.** A Chrome box shows the icon of the tab that window is showing. Chrome gives the window manager only its
  own logo, so sdesk looks the page up in your profile: the window title is the active tab's title, `History` gives that
  title's latest URL, `Favicons` gives the icon (read-only, never written; no restart or debug port needed). It is a good
  guess, not a certainty: two tabs with the same title show the icon of the one visited last, and a page too new for the
  history keeps Chrome's logo for a moment. Other Chrome-based programs and other profiles directories are not looked at.
* **Forgotten windows.** sdesk notes when a window was minimised (kept in `$XDG_RUNTIME_DIR/sdesk/minimized.json`, so a restart
  does not reset it). After 30 minutes its box shakes and lights up for a moment, then again every 5 minutes, until you
  bring it back or close it. "Show desktop" counts like any minimise.

        ./run.sh --nag-after 60 --nag-every 10      # 60 minutes, then every 10 minutes
        ./run.sh --nag-after 0                      # never shake

## Chrome tabs as a button group

With the **sdesk tabs** Chrome extension installed, a Chrome window's box becomes a group of buttons, one per tab: the page's
icon and its title (15 characters at most), thin dividers between them, the tab on screen lit. Click a tab button to switch to
that tab (the window also comes to the middle); click the lit one like any other box (focus, or minimise when it is the active
item). At most 5 tabs are shown: the one on screen plus the most recently used ones, in tab-strip order; the rest is a "+ 2"
button (a click on it acts on the window). Without the extension, or while Chrome is closed, a Chrome box is the single box
with the active tab, as before.

Why an extension: Chrome tells no other program which tabs it has, and since Chrome 136 it also refuses a debugging port on
your normal profile. The extension (`chrome-extension/`, Manifest V3) sends each window's tabs (title, address, position, last-used
time) to sdesk and carries out "switch to this tab". Everything goes over a WebSocket on `127.0.0.1:47631`, this computer
only, and sdesk refuses any connection that does not come from a browser extension (a web page cannot feed the taskbar).
The page icons come from your Chrome profile's own icon database, as for the single box.

Install it once: `chrome://extensions` > **Developer mode** > **Load unpacked** > the `chrome-extension` folder. It reconnects
by itself whenever sdesk restarts.

    ./run.sh --chrome-tabs off          # never
    ./run.sh --chrome-port 47777        # another port (change it in chrome-extension/background.js too)

Which X window is which Chrome window is found by title (an X window is named after its active tab) and then remembered.
Two windows that show the same title at the same moment can swap their groups until one of them changes tab.

## Clock and sound output

Left of the window icons the taskbar shows the time, a volume slider and the current sound output (for example *USB Headset*). The window
icons stay where they are: these two boxes hang off the left side of the row. Hover the clock for the date.

* click the sound box: switch to the next output, and whatever is playing moves along, so you hear the change at once;
* right-click it: pick any output from a list;
* the slider left of it is the volume (0-100%) of the current output: drag the knob or click on the track, or scroll the
  mouse wheel over the slider or the sound box (5% per notch); middle-click either one to mute or unmute (the slider greys
  out while muted). It follows volume changes made elsewhere, such as the keyboard's volume keys.

A click cycles through the outputs whose name contains `logitech` (the default of `--sound-match`; change it to match your
headset), skipping the built-in audio and HDMI; if fewer than two match it cycles through every real output. It talks to the sound server with
`pactl` in a background thread, so the bar never waits for it, and it follows changes made elsewhere (the Deepin control
center, a headset being plugged in).

    ./run.sh --sound-match "usb"    # cycle through the outputs containing "usb" instead
    ./run.sh --sound off            # no sound box

## Thread badges (Claude Code, T3 Code)

The taskbar boxes of Claude and T3 Code carry small coloured count badges after their name: **orange** = threads that wait
for you, **green** = done since you last looked, **blue** = working (a light runs slowly round its edge). A box without a thread in any of these states shows
none, and nothing shows while the program has no window. Hover for the thread names; clicking the box does what it always
does and also marks its done threads as seen.

Only states the programs keep themselves are read (`sdesk/threads.py`):

- **Claude Code**: `status` ("busy", "idle", "waiting") in `~/.claude/sessions/<pid>.json`, for live processes only.
  Sessions started by T3 Code and headless SDK sessions are left out.
- **T3 Code**: its database `~/.t3/userdata/statev2.sqlite` (read-only): a running run = working; with an open question for
  you = waiting.
- "Done" is not kept by the programs: sdesk makes it from the change busy/waiting -> idle while it runs. It is lost when sdesk
  restarts.

Read every 3 s. `--threads off` disables it.

## Margins

`--gap` is the space between windows, `--margin` the space at the strip ends, `--vmargin` (default 12) the space above and
below the windows. One catch: kwin keeps a mostly-off-screen window's frame at least one of *its own* titlebar heights
from the top edge and moves it there if it is closer. Far-away windows in the strip are off-screen, so a window placed
higher than that would sit lower while parked and visibly *jump* up when a taskbar click scrolls it in. So the top is the
tallest titlebar among the windows that are showing (e.g. Shotcut: 51px), or `--vmargin` if none is taller, and it is the
same for every window: all windows share one top and one bottom, i.e. one height. Minimise the window with the tall
titlebar and everything moves back up to `--vmargin`.

## Options

    ./run.sh --gap 32 --margin 32 --scroll-step 600 --bar top --bar-height 48
    ./run.sh --restore-dock       # only if a crash left the dock off or hidden
    .venv/bin/python -m sdesk     # same, leaving Deepin's dock alone (use with --bar top)

## How it works

`sdesk` runs next to Deepin's own window manager instead of replacing it, so decorations, alt-tab,
the session and everything else stay as they are. It reads/moves windows via standard EWMH
(`_NET_CLIENT_LIST`, `_NET_MOVERESIZE_WINDOW`, `_NET_WORKAREA`), grabs Super+wheel on the root
window, and reserves the taskbar's strip with `_NET_WM_STRUT_PARTIAL`.

- `sdesk/layout.py` – pure strip maths · `sdesk/controller.py` – model, drag/resize, animation, add-window slot
- `sdesk/xwm.py` – X11 backend (also the invisible input-only windows that catch right-clicks in the gaps)
- `sdesk/taskbar.py` – GTK taskbar + drop outline · `sdesk/menu.py` – add-window menu · `sdesk/app.py` – wiring · `sdesk/dock.py` – switch Deepin's dock off/on

## A desktop icon to start and stop it

    launcher/install.sh            # puts "Scrolling Desktop" on the desktop and in the application menu
    launcher/install.sh --remove

Click the icon: if sdesk is running it is stopped (windows and the Deepin dock come back as they were), otherwise it is
started; a notification says what happened. Right-click the icon (or its menu entry) for separate *Start* / *Stop*. The
same from a terminal: `./sdesk-toggle [toggle|start|stop|status]`. It only ever matches processes started as
`python -m sdesk`, and a stop that hangs is escalated to a kill, after which the guardian restores everything. sdesk does
not start by itself at login; to make it, add `Exec=/path/to/slopslide/sdesk-toggle start` as an
autostart entry.

## Development

    .venv/bin/python -m pytest              # layout + controller logic, no X needed
    .venv/bin/python lab/record_media.py    # re-record the videos and screenshots of the README (needs the lab, see below)
    lab/start_lab.sh                        # isolated Xvfb :99 + its own kwin_x11 + own D-Bus (real session untouched)
    COMPOSE=X lab/start_lab.sh              # same, with kwin's own compositor (transparency; Xvfb has no real OpenGL)
    DISPLAY=:99 .venv/bin/python lab/demo_window.py Files nautilus 3b6ea5 &    # spawn test windows
    DISPLAY=:99 .venv/bin/python -m sdesk &
    DISPLAY=:99 .venv/bin/python lab/shot.py out.png                            # screenshot
    lab/stop_lab.sh

The lab never sets `KWIN_COMPOSE`: Deepin saves that in a dconfig shared with the real session, which silently switched the real desktop to "performance" mode. It suspends the lab compositor over the lab's own D-Bus instead.

## Known limits (v1)

- Single monitor . Windows on other virtual desktops, fullscreen windows, dialogs and
  other transient windows are left alone and not shown in the taskbar. Windows that were already minimised when
  sdesk started do get a (dimmed) button.
- Windows are moved over X, so a fast scroll can show slight tearing without compositing.
- If kwin ever refuses the Super+wheel grab (another program owns it), sdesk says so and the taskbar
  still works.
- The lab cannot show Deepin's own effects (rounded corners, the outline, blur); those were checked on the real session.
