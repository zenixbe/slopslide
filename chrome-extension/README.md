# sdesk tabs (Chrome extension)

Lets sdesk's taskbar show the tabs of each Chrome window as a group of buttons, and switch to the tab you click.

* It sends the title, address, position and last-used time of your tabs to `ws://127.0.0.1:47631` (this computer only, never
  the internet) and listens there for "switch to this tab". It asks for the `tabs` permission for that, and `alarms` to find
  sdesk again after a restart. It does not read page content, cookies or history, and is off in incognito windows.
* sdesk refuses connections that do not come from a browser extension, so no web page can talk to it.

## Install (once)

1. Open `chrome://extensions` in Chrome.
2. Switch on **Developer mode** (top right).
3. Click **Load unpacked** and choose this folder (`chrome-extension`).

It then runs by itself and reconnects whenever sdesk (re)starts. To remove it: `chrome://extensions` > Remove.
