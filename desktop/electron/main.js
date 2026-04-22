const { app, BrowserWindow, ipcMain, nativeTheme, session, shell, Notification, Tray, nativeImage } = require("electron");
const path = require("path");
const fs = require("fs");
const { PythonManager } = require("./python-manager");
const { PtyManager } = require("./pty-manager");
const { TectonicManager } = require("./tectonic-manager");
const { CanvasFs } = require("./canvas-fs");
const { PowermetricsManager } = require("./powermetrics-manager");
const { buildMenu } = require("./menu");

// Prevent EPIPE crashes when stdout/stderr pipe closes during shutdown
process.stdout?.on("error", (err) => { if (err.code !== "EPIPE") throw err; });
process.stderr?.on("error", (err) => { if (err.code !== "EPIPE") throw err; });

let mainWindow = null;
let pythonManager = null;
const ptyManager = new PtyManager();
const tectonicManager = new TectonicManager();
const canvasFs = new CanvasFs({
  getServerPort: () => pythonManager?.port || null,
});
let pendingIPC = []; // Queue IPC messages until renderer is ready
let tray = null;
let _trayStatus = "idle";
// Cached snapshot of the latest session list from the renderer poll.
// Used by tray click handlers to navigate to the oldest waiting session.
let _lastSessions = [];

/* ───── macOS menu bar tray icon (agent status) ───── */

// Status colors — the white strokes of the tray glyph get tinted to
// indicate state; transparent background stays transparent.
const _STATUS_COLORS = {
  idle:    [230, 230, 230], // soft white — blends into dark menu bar
  working: [34,  197,  94], // emerald green
  waiting: [245, 158,  11], // amber
};

// Cache: we tint the icon once per status and reuse the result
const _trayIconCache = {};

// Accept a status name ("idle", "working", "waiting") OR an [r, g, b] array.
function _trayIcon(colorOrRGB) {
  const cacheKey = Array.isArray(colorOrRGB) ? colorOrRGB.join(",") : colorOrRGB;
  if (_trayIconCache[cacheKey]) return _trayIconCache[cacheKey];

  // Load the tray glyph (white strokes on transparent bg), 22×22 @2x for Retina
  const iconPath = path.join(__dirname, "..", "resources", "tray-icon.png");
  const base = nativeImage.createFromPath(iconPath).resize({
    width: 44, height: 44, quality: "best",
  });
  const bitmap = base.toBitmap(); // BGRA on macOS, RGBA on others
  const size = base.getSize();
  const len = bitmap.length;

  const [tr, tg, tb] = Array.isArray(colorOrRGB)
    ? colorOrRGB
    : (_STATUS_COLORS[colorOrRGB] || _STATUS_COLORS.idle);
  const out = Buffer.alloc(len);

  // Detect platform byte order: Electron's toBitmap is BGRA on Win/macOS, RGBA on Linux
  const isBGRA = process.platform === "darwin" || process.platform === "win32";

  // Tint the white strokes to the status color; preserve per-pixel alpha so
  // anti-aliased edges stay smooth against the menu bar background.
  for (let i = 0; i < len; i += 4) {
    const a = bitmap[i + 3];
    if (a < 1) {
      out[i] = 0; out[i+1] = 0; out[i+2] = 0; out[i+3] = 0;
      continue;
    }
    if (isBGRA) { out[i] = tb; out[i+1] = tg; out[i+2] = tr; }
    else        { out[i] = tr; out[i+1] = tg; out[i+2] = tb; }
    out[i+3] = a;
  }

  const img = nativeImage.createFromBuffer(out, {
    width: size.width, height: size.height, scaleFactor: 2.0,
  });
  _trayIconCache[cacheKey] = img;
  return img;
}

// Build the right-click context menu with current activity summary
function _buildTrayMenu(summary) {
  const { Menu } = require("electron");
  const counts = summary?.counts || { working: 0, idle: 0, waiting: 0 };
  const autoXps = summary?.autoXps || [];
  const total = counts.working + counts.idle + counts.waiting;

  const items = [];

  // Header — overall state
  if (total === 0 && autoXps.length === 0) {
    items.push({ label: "No active sessions", enabled: false });
  } else {
    if (counts.waiting > 0) {
      items.push({
        label: `${counts.waiting} agent${counts.waiting !== 1 ? "s" : ""} waiting for input`,
        enabled: false,
      });
    }
    if (counts.working > 0) {
      items.push({
        label: `${counts.working} agent${counts.working !== 1 ? "s" : ""} working`,
        enabled: false,
      });
    }
    if (counts.idle > 0) {
      items.push({
        label: `${counts.idle} agent${counts.idle !== 1 ? "s" : ""} idle`,
        enabled: false,
      });
    }
  }

  // Auto-experiments section
  if (autoXps.length > 0) {
    items.push({ type: "separator" });
    items.push({
      label: `${autoXps.length} auto-experiment${autoXps.length !== 1 ? "s" : ""} running`,
      enabled: false,
    });
    // Show up to 5 by name
    for (const xp of autoXps.slice(0, 5)) {
      items.push({
        label: `   ${xp.name}`,
        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
            mainWindow.webContents.send("focus-experiment", xp.id);
          }
        },
      });
    }
    if (autoXps.length > 5) {
      items.push({ label: `   …and ${autoXps.length - 5} more`, enabled: false });
    }
  }

  items.push({ type: "separator" });
  items.push({
    label: "Show Distillate",
    click: _showDistillateFocusingWaiting,
  });
  items.push({ type: "separator" });
  items.push({ label: "Quit", click: () => app.quit() });

  return Menu.buildFromTemplate(items);
}

// Find the oldest session that's waiting for user input. Returns null if
// nothing needs attention. Used for the smart tray click — jumping straight
// to whatever the user should look at first.
function _oldestWaitingSession() {
  const waiting = _lastSessions.filter((s) => s.status === "waiting");
  if (!waiting.length) return null;
  // Sort by startedAt ASC (oldest first). Missing timestamps sort last.
  waiting.sort((a, b) => {
    const ta = a.startedAt ? Date.parse(a.startedAt) : Number.POSITIVE_INFINITY;
    const tb = b.startedAt ? Date.parse(b.startedAt) : Number.POSITIVE_INFINITY;
    return ta - tb;
  });
  return waiting[0];
}

// Bring the window to focus. If any session needs attention, send an IPC
// event telling the renderer to navigate to it.
function _showDistillateFocusingWaiting() {
  if (!mainWindow) return;
  mainWindow.show();
  mainWindow.focus();
  const target = _oldestWaitingSession();
  if (target) {
    mainWindow.webContents.send("focus-waiting-session", target);
  }
}

// Smooth breathing animation for the tray icon when waiting for user attention.
// Pre-generates frames along a sine curve between amber and bright amber,
// then steps through them on a timer for a fluid glow effect.
const _BREATHE_LO   = [245, 158,  11];  // amber (matches waiting status)
const _BREATHE_HI   = [255, 220,  80];  // bright warm amber
const _BREATHE_STEPS = 36;              // frames per full cycle
const _BREATHE_MS    = 80;              // ms between frames → 36×80 = 2.88s cycle
let _breatheFrames = null;               // pre-computed nativeImage[]
let _breatheIndex  = 0;
let _breatheTimer  = null;

function _ensureBreatheFrames() {
  if (_breatheFrames) return;
  _breatheFrames = [];
  for (let i = 0; i < _BREATHE_STEPS; i++) {
    // Sine wave: smoothly 0→1→0 over the cycle (starts at trough)
    const t = (Math.sin((i / _BREATHE_STEPS) * Math.PI * 2 - Math.PI / 2) + 1) / 2;
    const rgb = [
      Math.round(_BREATHE_LO[0] + (_BREATHE_HI[0] - _BREATHE_LO[0]) * t),
      Math.round(_BREATHE_LO[1] + (_BREATHE_HI[1] - _BREATHE_LO[1]) * t),
      Math.round(_BREATHE_LO[2] + (_BREATHE_HI[2] - _BREATHE_LO[2]) * t),
    ];
    _breatheFrames.push(_trayIcon(rgb));
  }
}

function _startTrayPulse() {
  if (_breatheTimer) return;
  _ensureBreatheFrames();
  _breatheIndex = 0;
  _breatheTimer = setInterval(() => {
    if (!tray || !_breatheFrames) return;
    tray.setImage(_breatheFrames[_breatheIndex]);
    _breatheIndex = (_breatheIndex + 1) % _BREATHE_STEPS;
  }, _BREATHE_MS);
}

function _stopTrayPulse() {
  if (_breatheTimer) {
    clearInterval(_breatheTimer);
    _breatheTimer = null;
    _breatheIndex = 0;
  }
}

function updateTray(status, sessions, summary) {
  if (!tray) return;
  const previous = _trayStatus;

  // Cache the latest session snapshot for the smart click handler
  _lastSessions = sessions || [];

  // Always rebuild the context menu so counts stay fresh
  tray.setContextMenu(_buildTrayMenu(summary));

  if (status === _trayStatus) return; // icon/tooltip don't need refresh
  _trayStatus = status;
  tray.setImage(_trayIcon(status));

  // Start or stop tray pulse animation
  if (status === "waiting") {
    _startTrayPulse();
  } else {
    _stopTrayPulse();
  }

  const tips = {
    idle: "Distillate",
    working: "Distillate — agents working",
    waiting: "Distillate — needs attention",
  };
  tray.setToolTip(tips[status] || "Distillate");

  // Push notification on transition INTO "waiting" (not on every poll)
  if (status === "waiting" && previous !== "waiting" && Notification.isSupported()) {
    const waiting = (sessions || []).filter((s) => s.status === "waiting");
    const count = waiting.length;
    const firstName = waiting[0]?.name || "an agent";
    const body = count > 1
      ? `${count} agents need your input — including ${firstName}`
      : `${firstName} needs your input`;
    const n = new Notification({
      title: "Distillate",
      body,
      silent: false,
    });
    n.on("click", _showDistillateFocusingWaiting);
    n.show();
    // Bounce dock icon once for extra attention (macOS only)
    if (process.platform === "darwin" && app.dock) {
      app.dock.bounce("informational");
    }
  }
}

function initTray() {
  tray = new Tray(_trayIcon("idle"));
  tray.setToolTip("Distillate");
  tray.on("click", _showDistillateFocusingWaiting);
  tray.setContextMenu(_buildTrayMenu(null));
}

/* ───── Window state persistence ───── */

function _windowStatePath() {
  const home = process.env.HOME || process.env.USERPROFILE;
  return path.join(home, ".config", "distillate", "window-state.json");
}

function _loadWindowState() {
  try {
    return JSON.parse(fs.readFileSync(_windowStatePath(), "utf-8"));
  } catch {
    return null;
  }
}

let _saveTimer = null;
function _saveWindowState() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const bounds = mainWindow.getBounds();
    const data = { ...bounds, isMaximized: mainWindow.isMaximized() };
    try {
      const dir = path.dirname(_windowStatePath());
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(_windowStatePath(), JSON.stringify(data), "utf-8");
    } catch {
      // Non-critical
    }
  }, 500);
}

function createWindow() {
  const saved = _loadWindowState();
  const opts = {
    width: saved?.width || 1280,
    height: saved?.height || 850,
    minWidth: 480,
    minHeight: 600,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#12100e",
    icon: path.join(__dirname, "..", "resources", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      // sandbox: false is required for require() in preload (highlight.js, xterm).
      // Security: contextIsolation=true and nodeIntegration=false are both ON,
      // so the renderer has no direct Node access — acceptable trade-off.
      sandbox: false,
    },
  };
  if (saved?.x != null && saved?.y != null) {
    opts.x = saved.x;
    opts.y = saved.y;
  }

  mainWindow = new BrowserWindow(opts);

  if (saved?.isMaximized) {
    mainWindow.maximize();
  }

  // Show inline splash while the Python server starts
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`<!DOCTYPE html>
<html><head>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..700;1,9..144,300..700&display=swap" rel="stylesheet">
<style>
  body { background: #0a0817; color: #8888a0; font-family: -apple-system, system-ui, sans-serif;
         font-optical-sizing: auto;
         display: flex; flex-direction: column; align-items: center; justify-content: center;
         height: 100vh; margin: 0; -webkit-app-region: drag; }
  .logo-wrap { position: relative; width: 64px; height: 90px; margin-bottom: 20px; }
  .logo-wrap svg { position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
                    filter: drop-shadow(0 0 20px rgba(138,128,216,0.6)) drop-shadow(0 0 44px rgba(138,128,216,0.3));
                    animation: flask-glow 4s ease-in-out infinite; }
  @keyframes flask-glow {
    0%,100% { filter: drop-shadow(0 0 20px rgba(138,128,216,0.6)) drop-shadow(0 0 44px rgba(138,128,216,0.3)); }
    50% { filter: drop-shadow(0 0 24px rgba(224,138,82,0.5)) drop-shadow(0 0 48px rgba(138,128,216,0.35)); }
  }
  .bubble { position: absolute; border-radius: 50%; opacity: 0; animation: rise 2.5s ease-out infinite; }
  .b1 { width:6px; height:6px; background:#a89eef; left:28px; animation-duration:2.5s; }
  .b2 { width:4px; height:4px; background:#8a80d8; left:22px; animation-duration:3.2s; animation-delay:0.7s; }
  .b3 { width:5px; height:5px; background:#e08a52; left:38px; animation-duration:2.8s; animation-delay:1.4s; }
  .b4 { width:5px; height:5px; background:#5db76a; left:30px; animation-duration:3.5s; animation-delay:0.3s; }
  .b5 { width:4px; height:4px; background:#a89eef; left:24px; animation-duration:2.2s; animation-delay:1.0s; }
  .b6 { width:3px; height:3px; background:#e8a566; left:42px; animation-duration:3.0s; animation-delay:1.8s; }
  .b7 { width:4px; height:4px; background:#8a80d8; left:18px; animation-duration:2.7s; animation-delay:0.5s; }
  @keyframes rise {
    0% { bottom: 24px; opacity: 0; transform: translateX(0); }
    10% { opacity: 0.9; }
    40% { opacity: 0.5; transform: translateX(5px); }
    100% { bottom: 90px; opacity: 0; transform: translateX(-3px); }
  }
  .wordmark {
    font-family: "Fraunces", Georgia, "Times New Roman", serif;
    font-size: 60px;
    font-weight: 300;
    font-variation-settings: "opsz" 144, "SOFT" 100;
    letter-spacing: 0.015em;
    color: #e8e4f4;
    margin-bottom: 32px;
    opacity: 0;
    animation: wordmark-fade 0.6s ease 0.2s forwards;
  }
  @keyframes wordmark-fade {
    from { opacity: 0; transform: translateY(3px); }
    to { opacity: 1; transform: translateY(0); }
  }
  #msg { font-size: 12px; margin-top: 24px; color: #6a6480; font-family: "SF Mono", ui-monospace, monospace; letter-spacing: 0.04em; }
</style></head><body>
  <div class="logo-wrap">
    <div class="bubble b1"></div><div class="bubble b2"></div><div class="bubble b3"></div>
    <div class="bubble b4"></div><div class="bubble b5"></div><div class="bubble b6"></div>
    <div class="bubble b7"></div>
    <svg width="48" height="48" viewBox="0 0 32 32">
      <defs>
        <clipPath id="splashClip"><circle cx="16" cy="16" r="15.5"/></clipPath>
        <linearGradient id="splashBg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stop-color="#7d72d0"/>
          <stop offset="100%" stop-color="#4a3cc4"/>
        </linearGradient>
        <radialGradient id="splashWarm" cx="92%" cy="8%" r="75%">
          <stop offset="0%"  stop-color="#f4b07a" stop-opacity="0.45"/>
          <stop offset="65%" stop-color="#f4b07a" stop-opacity="0"/>
        </radialGradient>
        <linearGradient id="splashLiquid" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#5db76a"/>
          <stop offset="100%" stop-color="#a8eab1"/>
        </linearGradient>
      </defs>
      <circle cx="16" cy="16" r="15.5" fill="url(#splashBg)"/>
      <g clip-path="url(#splashClip)"><rect width="32" height="32" fill="url(#splashWarm)"/></g>
      <polyline points="7,7.5 11,11.5 7,16" fill="none" stroke="#f0ead8" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.45"/>
      <path d="M15.1,15.72 Q17.35,16.5 19.64,16.28 L16.59,24.63 A2.23,2.23 0 0,1 12.4,23.1 Z" fill="url(#splashLiquid)"/>
      <g fill="none" stroke="#f0ead8" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12.4" y1="23.1" x2="18.4" y2="6.6"/>
        <line x1="16.59" y1="24.63" x2="22.59" y2="8.13"/>
        <path d="M12.4,23.1 A2.23,2.23 0,0,0 16.59,24.63"/>
        <line x1="17.18" y1="6.16" x2="23.82" y2="8.57"/>
      </g>
    </svg>
  </div>
  <div class="wordmark">Distillate</div>
  <div id="msg">Starting server\u2026</div>
</body></html>`)}`);

  // Save window state on move/resize
  mainWindow.on("resize", _saveWindowState);
  mainWindow.on("move", _saveWindowState);

  // Flush queued IPC messages once the renderer is ready
  mainWindow.webContents.on("did-finish-load", () => {
    for (const [channel, data] of pendingIPC) {
      mainWindow.webContents.send(channel, data);
    }
    pendingIPC = [];
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // Broadcast system theme changes to the renderer for live xterm switching
  nativeTheme.on("updated", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("theme-changed", nativeTheme.shouldUseDarkColors);
    }
  });

  // Prevent external URLs from navigating inside the app
  mainWindow.webContents.on("will-navigate", (event, url) => {
    // Allow local server and data: URLs (splash screen)
    if (url.startsWith("http://127.0.0.1") || url.startsWith("data:")) return;
    event.preventDefault();
    shell.openExternal(url);
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://127.0.0.1") || url.startsWith("data:")) {
      return { action: "allow" };
    }
    shell.openExternal(url);
    return { action: "deny" };
  });
}

/**
 * Send an IPC message to the renderer, queuing if not yet loaded.
 */
function sendToRenderer(channel, data) {
  if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isLoading()) {
    mainWindow.webContents.send(channel, data);
  } else {
    pendingIPC.push([channel, data]);
  }
}

function newConversation() {
  if (mainWindow) {
    mainWindow.webContents.send("new-conversation");
  }
}

function openSettings() {
  if (mainWindow) {
    mainWindow.webContents.send("open-settings");
  }
}

// Register distillate:// protocol for auth deep links
app.setAsDefaultProtocolClient("distillate");

// Set app name for dock/taskbar (overrides "Electron" in dev mode)
app.setName("Distillate");

app.on("ready", async () => {
  // Dock icon comes from Electron.app bundle (patched by postinstall)

  // Restore persisted theme before window creation so the splash doesn't flash
  // the system theme if the user explicitly chose light/dark.
  try {
    const prefs = _loadPrefs();
    if (prefs.themeSource === "light" || prefs.themeSource === "dark" || prefs.themeSource === "system") {
      nativeTheme.themeSource = prefs.themeSource;
    }
  } catch {}

  // Build app menu
  buildMenu({
    onNewConversation: newConversation,
    onOpenSettings: openSettings,
    getWindow: () => mainWindow,
  });

  // Create window first so progress messages can reach the renderer
  createWindow();
  initTray();

  pythonManager = new PythonManager();

  try {
    const port = await pythonManager.start((message) => {
      // Update splash screen progress while server starts
      if (mainWindow && !mainWindow.isDestroyed()) {
        const escaped = message.replace(/'/g, "\\'");
        mainWindow.webContents.executeJavaScript(
          `document.getElementById('msg').textContent='${escaped}'`
        ).catch(() => {});
      }
    });

    // Clear cached CSS/JS so dev changes always take effect
    await session.defaultSession.clearCache();
    // Load the UI from the Python server
    await mainWindow.loadURL(`http://127.0.0.1:${port}/ui/`);
    sendToRenderer("server-ready", { port });
    _flushPendingOpenFile();
  } catch (err) {
    console.error("Failed to start Python server:", err);
    sendToRenderer("server-error", {
      message: err.message || "Failed to start Python server",
    });
  }
});

app.on("window-all-closed", async () => {
  ptyManager.cleanup();
  if (pythonManager) {
    await pythonManager.stop();
  }
  app.quit();
});

// Ensure the Python server is killed on quit (Cmd+Q, dock quit, etc.)
app.on("will-quit", () => {
  if (pythonManager && pythonManager.process) {
    try { pythonManager.process.kill("SIGTERM"); } catch (_) {}
  }
});

app.on("activate", () => {
  if (mainWindow === null) {
    createWindow();
  }
});

// Handle distillate:// deep links (for auth callback)
app.on("open-url", (event, url) => {
  event.preventDefault();
  if (mainWindow) {
    mainWindow.webContents.send("deep-link", url);
  }
});

// ── Drag PDF to Dock icon → import to Zotero ────────────────────────────
// macOS sends open-file when the user drags a file onto the app icon in
// the Dock (or double-clicks a PDF associated with the app). We read
// the bytes, POST to /papers/import, and tell the renderer to show it.

let _pendingOpenFile = null; // stash path if event fires before server is ready

app.on("open-file", (event, filePath) => {
  event.preventDefault();
  if (!filePath.toLowerCase().endsWith(".pdf")) return;
  const port = pythonManager?.port;
  if (!port) {
    // Server isn't up yet — stash the path and handle after ready.
    _pendingOpenFile = filePath;
    return;
  }
  _importPdfFile(filePath, port);
});

async function _importPdfFile(filePath, port) {
  try {
    const fileBytes = fs.readFileSync(filePath);
    const fileName = path.basename(filePath);

    // Build a multipart/form-data body manually (no npm dep needed).
    const boundary = `----DistillateBoundary${Date.now()}`;
    const header = Buffer.from(
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="${fileName}"\r\n` +
      `Content-Type: application/pdf\r\n\r\n`
    );
    const footer = Buffer.from(`\r\n--${boundary}--\r\n`);
    const body = Buffer.concat([header, fileBytes, footer]);

    const http = require("http");
    const result = await new Promise((resolve, reject) => {
      const req = http.request(
        {
          hostname: "127.0.0.1",
          port,
          path: "/papers/import",
          method: "POST",
          headers: {
            "Content-Type": `multipart/form-data; boundary=${boundary}`,
            "Content-Length": body.length,
          },
        },
        (res) => {
          let data = "";
          res.on("data", (chunk) => { data += chunk; });
          res.on("end", () => {
            try { resolve(JSON.parse(data)); }
            catch { reject(new Error(`Non-JSON response: ${data.slice(0, 200)}`)); }
          });
        },
      );
      req.on("error", reject);
      req.write(body);
      req.end();
    });

    if (result.ok && result.paper_key) {
      sendToRenderer("paper-imported", {
        paperKey: result.paper_key,
        title: result.title || fileName,
      });
      // Also show a native notification so the user knows it worked even
      // if the app is in the background.
      if (Notification.isSupported()) {
        new Notification({
          title: "Paper imported",
          body: result.title || fileName,
        }).show();
      }
    } else {
      console.error("[open-file] import failed:", result);
    }
  } catch (err) {
    console.error("[open-file] import error:", err);
  }
}

// Flush any file that was dragged before the server started.
function _flushPendingOpenFile() {
  if (_pendingOpenFile && pythonManager?.port) {
    _importPdfFile(_pendingOpenFile, pythonManager.port);
    _pendingOpenFile = null;
  }
}

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------

ipcMain.handle("get-server-port", () => {
  return pythonManager ? pythonManager.port : null;
});

// Capture tmux pane + pane state (scroll_position, copy mode).
// Returns { ok, content, scrollPos, copyMode }.  scrollPos is lines above
// the live view (0 = user is at live, N = scrolled up N lines).  Used to
// place ydisp correctly when entering scroll-back mode — no text matching.
ipcMain.handle("terminal:capture-pane", async (_event, { tmuxName, lines }) => {
  const { execFile } = require("child_process");
  const exec = (args) => new Promise((resolve) => {
    execFile("tmux", args,
      { timeout: 3000, maxBuffer: 10 * 1024 * 1024 },
      (err, stdout) => err
        ? resolve({ ok: false, error: err.message, timeout: !!(err.killed || err.signal === "SIGTERM"), stdout: "" })
        : resolve({ ok: true, stdout }));
  });
  const cap = await exec(["capture-pane", "-p", "-t", tmuxName, "-S", String(-(lines || 5000))]);
  if (!cap.ok) return { ok: false, error: cap.error };
  const state = await exec(["display-message", "-p", "-t", tmuxName,
    "scroll=#{scroll_position} copy=#{pane_in_mode}"]);
  const scrollPos = state.ok
    ? (parseInt((state.stdout.match(/scroll=(\d*)/) || [])[1] || "0", 10) || 0)
    : 0;
  const copyMode = state.ok
    && ((state.stdout.match(/copy=(\d)/) || [])[1] === "1");
  return { ok: true, content: cap.stdout, scrollPos, copyMode };
});

ipcMain.handle("open-external", async (_event, url) => {
  try {
    await shell.openExternal(url);
  } catch (err) {
    // shell.openExternal can fail for custom protocols on some Electron
    // versions — fall back to macOS `open` command
    if (process.platform === "darwin") {
      require("child_process").exec(`open "${url.replace(/"/g, '\\"')}"`);
    }
    console.error("open-external fallback:", url, err.message);
  }
});

ipcMain.handle("notify", (_event, title, body) => {
  if (Notification.isSupported()) {
    new Notification({ title, body }).show();
  }
});

// Settings — read/write the distillate .env file
function _envPath() {
  const home = process.env.HOME || process.env.USERPROFILE;
  return path.join(home, ".config", "distillate", ".env");
}

// UI preferences (theme, etc.) — small JSON file persisted across launches
function _prefsPath() {
  const home = process.env.HOME || process.env.USERPROFILE;
  return path.join(home, ".config", "distillate", "ui-prefs.json");
}

function _loadPrefs() {
  try {
    return JSON.parse(fs.readFileSync(_prefsPath(), "utf-8"));
  } catch {
    return {};
  }
}

function _savePrefs(prefs) {
  try {
    const dir = path.dirname(_prefsPath());
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(_prefsPath(), JSON.stringify(prefs, null, 2), "utf-8");
  } catch (e) {
    console.error("[prefs] save failed:", e.message);
  }
}

function _parseEnv(text) {
  const vars = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    // Strip surrounding quotes
    if ((val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    vars[key] = val;
  }
  return vars;
}

function _serializeEnv(vars) {
  return Object.entries(vars)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n") + "\n";
}

ipcMain.handle("get-settings", () => {
  try {
    const text = fs.readFileSync(_envPath(), "utf-8");
    const vars = _parseEnv(text);
    return {
      authToken: vars.DISTILLATE_AUTH_TOKEN || "",
      experimentsRoot: vars.EXPERIMENTS_ROOT || "",
      privateRepos: vars.PRIVATE_REPOS === "true",
    };
  } catch {
    return { authToken: "" };
  }
});

ipcMain.handle("export-state", async () => {
  const { dialog } = require("electron");
  if (!pythonManager || !pythonManager.port) {
    return { ok: false, reason: "server_not_ready" };
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${pythonManager.port}/state/export`);
    const data = await resp.json();
    if (!data.ok) return { ok: false, reason: data.reason };

    const result = await dialog.showSaveDialog(mainWindow, {
      title: "Export Distillate State",
      defaultPath: "distillate-state.json",
      filters: [{ name: "JSON", extensions: ["json"] }],
    });
    if (result.canceled) return { ok: false, reason: "canceled" };

    fs.writeFileSync(
      result.filePath,
      JSON.stringify(data.state, null, 2),
      "utf-8"
    );
    return { ok: true, path: result.filePath };
  } catch (err) {
    return { ok: false, reason: err.message };
  }
});

ipcMain.handle("import-state", async () => {
  const { dialog } = require("electron");
  if (!pythonManager || !pythonManager.port) {
    return { ok: false, reason: "server_not_ready" };
  }
  try {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: "Import Distillate State",
      filters: [{ name: "JSON", extensions: ["json"] }],
      properties: ["openFile"],
    });
    if (result.canceled) return { ok: false, reason: "canceled" };

    const content = fs.readFileSync(result.filePaths[0], "utf-8");
    const stateData = JSON.parse(content);

    const resp = await fetch(`http://127.0.0.1:${pythonManager.port}/state/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: stateData }),
    });
    const data = await resp.json();
    return data;
  } catch (err) {
    return { ok: false, reason: err.message };
  }
});

ipcMain.handle("select-directory", async (_event, title) => {
  const { dialog } = require("electron");
  const result = await dialog.showOpenDialog(mainWindow, {
    title: title || "Select Directory",
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths.length) return null;
  return result.filePaths[0];
});

// ---------------------------------------------------------------------------
// Terminal (PTY) IPC handlers
// ---------------------------------------------------------------------------

ipcMain.handle("terminal:attach", (_event, { projectId, sessionName, cols, rows }) => {
  console.log(`[terminal] attach request: project=${projectId} session=${sessionName} cols=${cols} rows=${rows}`);
  try {
    const proc = ptyManager.attach(projectId, sessionName, cols, rows);
    proc.onData((data) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("terminal:data", { projectId, data });
      }
    });
    proc.onExit(({ exitCode }) => {
      console.log(`[terminal] process exited: project=${projectId} code=${exitCode}`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("terminal:exit", { projectId, exitCode });
      }
      ptyManager.detach(projectId);
    });
    return { ok: true };
  } catch (err) {
    console.error(`[terminal] attach error:`, err.message);
    return { ok: false, reason: err.message };
  }
});

// Fire-and-forget (no round-trip) — keystroke latency critical path
ipcMain.on("terminal:input", (_event, { projectId, data }) => {
  ptyManager.write(projectId, data);
});

ipcMain.handle("terminal:resize", (_event, { projectId, cols, rows }) => {
  ptyManager.resize(projectId, cols, rows);
});

ipcMain.handle("get-theme", () => {
  return { source: nativeTheme.themeSource, isDark: nativeTheme.shouldUseDarkColors };
});

ipcMain.handle("terminal:detach", (_event, { projectId }) => {
  ptyManager.detach(projectId);
});

// Menu bar tray icon — renderer pushes aggregate status + session details + summary
// Sessions: [{ status, name }, ...]
// Summary: { counts: { working, idle, waiting }, autoXps: [{ id, name }] }
ipcMain.on("tray:status", (_event, { status, sessions, summary }) => {
  updateTray(status, sessions, summary);
});

// Bell detected in terminal output from Claude Code / agent
ipcMain.handle("bell-detected", async (_event) => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("distillate:bell-detected");
  }
});

// Theme — sets nativeTheme.themeSource which changes prefers-color-scheme for the entire app
ipcMain.handle("set-theme", (_event, mode) => {
  // mode: "system" | "light" | "dark"
  nativeTheme.themeSource = mode;
  // Persist across launches
  const prefs = _loadPrefs();
  prefs.themeSource = mode;
  _savePrefs(prefs);
  return { ok: true, isDark: nativeTheme.shouldUseDarkColors };
});

ipcMain.handle("save-settings", (_event, settings) => {
  const envFile = _envPath();

  // Read existing vars to preserve other settings
  let vars = {};
  try {
    vars = _parseEnv(fs.readFileSync(envFile, "utf-8"));
  } catch {
    // File doesn't exist yet — ensure directory exists
    const dir = path.dirname(envFile);
    fs.mkdirSync(dir, { recursive: true });
  }

  // Update the keys we manage
  if (settings.authToken !== undefined) {
    if (settings.authToken) {
      vars.DISTILLATE_AUTH_TOKEN = settings.authToken;
    } else {
      delete vars.DISTILLATE_AUTH_TOKEN;
    }
  }
  if (settings.experimentsRoot !== undefined) {
    if (settings.experimentsRoot) {
      vars.EXPERIMENTS_ROOT = settings.experimentsRoot;
    } else {
      delete vars.EXPERIMENTS_ROOT;
    }
  }
  if (settings.privateRepos !== undefined) {
    vars.PRIVATE_REPOS = settings.privateRepos ? "true" : "false";
  }

  fs.writeFileSync(envFile, _serializeEnv(vars), "utf-8");
  return { ok: true };
});

// ---------------------------------------------------------------------------
// Canvas editor IPC — file I/O, Tectonic compile, file watcher
// ---------------------------------------------------------------------------
//
// All handlers take (wsId, cvId) since a workspace may have multiple
// canvases. The (wsId, cvId) pair keys both the file sandbox and the
// Tectonic compile queue.

ipcMain.handle("canvas:list-files", async (_e, { wsId, cvId }) => {
  try { return await canvasFs.listFiles(wsId, cvId); }
  catch (err) { return { ok: false, error: err.message }; }
});

ipcMain.handle("canvas:read-file", async (_e, { wsId, cvId, relPath }) => {
  try { return await canvasFs.readFile(wsId, cvId, relPath); }
  catch (err) { return { ok: false, error: err.message }; }
});

ipcMain.handle("canvas:write-file", async (_e, { wsId, cvId, relPath, content }) => {
  try { return await canvasFs.writeFile(wsId, cvId, relPath, content); }
  catch (err) { return { ok: false, error: err.message }; }
});

ipcMain.handle("canvas:read-pdf", async (_e, { wsId, cvId }) => {
  try {
    const result = await canvasFs.readPdf(wsId, cvId);
    if (result.ok) return { ok: true, bytes: result.bytes };
    return result;
  } catch (err) { return { ok: false, error: err.message }; }
});

ipcMain.handle("canvas:invalidate-dir-cache", async (_e, { wsId, cvId }) => {
  canvasFs.invalidate(wsId, cvId);
  return { ok: true };
});

ipcMain.handle("tectonic:status", async () => {
  return tectonicManager.status();
});

ipcMain.handle("tectonic:install", async (_e) => {
  return await tectonicManager.install((progress) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("tectonic:install-progress", progress);
    }
  });
});

ipcMain.handle("tectonic:compile", async (_e, { wsId, cvId }) => {
  try {
    const { dir, entry } = await canvasFs.getDir(wsId, cvId);
    const port = pythonManager?.port;

    // Resolve citations (writes references.bib) before compile — non-fatal.
    // Skipped by the backend for non-LaTeX canvases.
    try {
      if (port) {
        await fetch(
          `http://127.0.0.1:${port}/workspaces/${wsId}/canvases/${cvId}/resolve-citations`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          }
        );
      }
    } catch {}

    const result = await tectonicManager.compile(wsId, cvId, dir, entry);

    // Post compile status back so the project card can show freshness.
    try {
      if (port) {
        const errCount = (result.errors || []).filter(
          (e) => (e.severity || "error") === "error"
        ).length;
        await fetch(
          `http://127.0.0.1:${port}/workspaces/${wsId}/canvases/${cvId}/compile-status`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ok: !!result.ok,
              duration_ms: result.durationMs || 0,
              error_count: errCount,
            }),
          }
        );
      }
    } catch {}

    return result;
  } catch (err) {
    return {
      ok: false,
      exitCode: -1,
      errors: [{ file: "main.tex", line: 0, message: err.message, severity: "error" }],
    };
  }
});

ipcMain.on("tectonic:abort", (_e, { wsId, cvId }) => {
  tectonicManager.abort(wsId, cvId);
});

// File-watcher IPC — push change events to the renderer so the inline
// editor can hot-reload when the agent (or any external tool) edits the
// entry file. The watcher is only active while the canvas editor is
// mounted; stop-watch is called on drill-out.
ipcMain.handle("canvas:start-watch", async (_e, { wsId, cvId }) => {
  return await canvasFs.startWatch(wsId, cvId, (change) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("canvas:file-changed", change);
    }
  });
});

ipcMain.on("canvas:stop-watch", (_e, { wsId, cvId }) => {
  canvasFs.stopWatch(wsId, cvId);
});

// ---------------------------------------------------------------------------
// Hardware metrics (Apple Silicon powermetrics) — started on demand by the
// renderer when experiments are running, stopped when none remain.
// ---------------------------------------------------------------------------

const _powerMetrics = new PowermetricsManager({
  onSample: (sample) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("powermetrics:sample", sample);
    }
  },
  onUnavailable: (reason) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("powermetrics:unavailable", { reason });
    }
  },
});

ipcMain.handle("powermetrics:start", () => {
  _powerMetrics.start();
  return { ok: true, running: _powerMetrics.running };
});

ipcMain.handle("powermetrics:stop", () => {
  _powerMetrics.stop();
  return { ok: true };
});

app.on("before-quit", () => { _powerMetrics.stop(); });
