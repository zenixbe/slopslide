// sdesk tabs: report every window's tabs to sdesk (ws://127.0.0.1) and do what sdesk asks (switch to a tab).
// Nothing leaves this computer, and only the title, address, position and icon of tabs are sent.
const SDESK = "ws://127.0.0.1:47631/tabs";
let socket = null;
let heartbeat = null;
let pending = null;
const sentIcons = new Set();          // addresses whose icon sdesk already has on this connection

function isOpen() {
  return socket && socket.readyState === WebSocket.OPEN;
}

function connect() {
  if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) return;
  try {
    socket = new WebSocket(SDESK);
  } catch (e) {
    socket = null;
    return;
  }
  socket.onopen = () => {
    sentIcons.clear();
    // A message every 20 s keeps this service worker alive (Chrome 116 and later) and tells sdesk we are still here.
    heartbeat = setInterval(() => isOpen() && socket.send(JSON.stringify({ type: "hb" })), 20000);
    report();
  };
  socket.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (msg.cmd === "activate") activate(msg.tabId, msg.windowId);
  };
  const closed = () => {
    clearInterval(heartbeat);
    socket = null;
  };
  socket.onclose = closed;
  socket.onerror = closed;
}

async function activate(tabId, windowId) {
  try {
    await chrome.tabs.update(tabId, { active: true });
    if (windowId !== undefined) await chrome.windows.update(windowId, { focused: true });
  } catch (e) {
    // the tab was closed in the meantime: nothing to do
  }
}

async function snapshot() {
  const windows = await chrome.windows.getAll({ populate: true, windowTypes: ["normal"] });
  return {
    type: "tabs",
    windows: windows.map((w) => ({
      id: w.id,
      focused: w.focused,
      tabs: (w.tabs || []).map((t) => ({
        id: t.id,
        index: t.index,
        title: t.title || "",
        url: t.url || t.pendingUrl || "",
        active: t.active,
        lastAccessed: t.lastAccessed || 0,
      })),
    })),
  };
}

// The icon Chrome shows for a page in its own tab strip, as a base64 PNG (Chrome's _favicon service, so it is the right one
// for every tab, also for localhost or a page that is not in the history).
async function iconOf(url) {
  const res = await fetch(chrome.runtime.getURL("/_favicon/") + "?pageUrl=" + encodeURIComponent(url) + "&size=32");
  const bytes = new Uint8Array(await res.arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

async function sendIcons(snap) {
  const urls = new Set();
  for (const w of snap.windows) for (const t of w.tabs) if (t.url && !sentIcons.has(t.url)) urls.add(t.url);
  for (const url of urls) {
    sentIcons.add(url);
    try {
      const png = await iconOf(url);
      if (isOpen()) socket.send(JSON.stringify({ type: "icon", url, png }));
    } catch (e) {
      sentIcons.delete(url);          // try again at the next snapshot
    }
  }
}

// Many events come in a burst (a new tab fires several): send one snapshot shortly after the last one.
function report() {
  clearTimeout(pending);
  pending = setTimeout(async () => {
    if (!isOpen()) {
      connect();                      // sdesk may have started since; the snapshot is sent when the connection opens
      return;
    }
    const snap = await snapshot();
    socket.send(JSON.stringify(snap));
    sendIcons(snap);
  }, 120);
}

for (const ev of ["onCreated", "onRemoved", "onUpdated", "onActivated", "onMoved", "onAttached", "onDetached", "onReplaced"]) {
  chrome.tabs[ev].addListener(report);
}
for (const ev of ["onCreated", "onRemoved", "onFocusChanged"]) {
  chrome.windows[ev].addListener(report);
}
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });       // find sdesk again after it was restarted
chrome.alarms.onAlarm.addListener(connect);
connect();
