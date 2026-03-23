const { app, BrowserWindow, ipcMain, shell, Notification } = require("electron");
const path = require("path");
const fs = require("fs");
const { PythonManager } = require("./python-manager");
const { PtyManager } = require("./pty-manager");
const { buildMenu } = require("./menu");

// Prevent EPIPE crashes when stdout/stderr pipe closes during shutdown
process.stdout?.on("error", (err) => { if (err.code !== "EPIPE") throw err; });
process.stderr?.on("error", (err) => { if (err.code !== "EPIPE") throw err; });

let mainWindow = null;
let pythonManager = null;
const ptyManager = new PtyManager();
let pendingIPC = []; // Queue IPC messages until renderer is ready

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
    width: saved?.width || 700,
    height: saved?.height || 850,
    minWidth: 480,
    minHeight: 600,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#0f0f23",
    icon: path.join(__dirname, "..", "resources", "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // allow require() in preload for highlight.js
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
<html><head><style>
  body { background: #0f0f23; color: #8888a0; font-family: -apple-system, system-ui, sans-serif;
         display: flex; flex-direction: column; align-items: center; justify-content: center;
         height: 100vh; margin: 0; -webkit-app-region: drag; }
  .logo-wrap { position: relative; width: 64px; height: 90px; margin-bottom: 12px; }
  .logo-wrap svg { position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
                    filter: drop-shadow(0 0 20px rgba(115,102,241,0.7)) drop-shadow(0 0 44px rgba(115,102,241,0.35));
                    animation: flask-glow 4s ease-in-out infinite; }
  @keyframes flask-glow {
    0%,100% { filter: drop-shadow(0 0 20px rgba(115,102,241,0.7)) drop-shadow(0 0 44px rgba(115,102,241,0.35)); }
    50% { filter: drop-shadow(0 0 24px rgba(251,191,36,0.6)) drop-shadow(0 0 48px rgba(115,102,241,0.4)); }
  }
  .bubble { position: absolute; border-radius: 50%; opacity: 0; animation: rise 2.5s ease-out infinite; }
  .b1 { width:6px; height:6px; background:#fcd34d; left:28px; animation-duration:2.5s; }
  .b2 { width:4px; height:4px; background:#818cf8; left:22px; animation-duration:3.2s; animation-delay:0.7s; }
  .b3 { width:5px; height:5px; background:#fbbf24; left:38px; animation-duration:2.8s; animation-delay:1.4s; }
  .b4 { width:5px; height:5px; background:#4ade80; left:30px; animation-duration:3.5s; animation-delay:0.3s; }
  .b5 { width:4px; height:4px; background:#e879f9; left:24px; animation-duration:2.2s; animation-delay:1.0s; }
  .b6 { width:3px; height:3px; background:#34d399; left:42px; animation-duration:3.0s; animation-delay:1.8s; }
  .b7 { width:4px; height:4px; background:#f472b6; left:18px; animation-duration:2.7s; animation-delay:0.5s; }
  @keyframes rise {
    0% { bottom: 24px; opacity: 0; transform: translateX(0); }
    10% { opacity: 0.9; }
    40% { opacity: 0.5; transform: translateX(5px); }
    100% { bottom: 90px; opacity: 0; transform: translateX(-3px); }
  }
  #msg { font-size: 13px; margin-top: 4px; }
</style></head><body>
  <div class="logo-wrap">
    <div class="bubble b1"></div><div class="bubble b2"></div><div class="bubble b3"></div>
    <div class="bubble b4"></div><div class="bubble b5"></div><div class="bubble b6"></div>
    <div class="bubble b7"></div>
    <svg width="48" height="48" viewBox="0 0 32 32">
      <circle cx="16" cy="16" r="15.5" fill="#7366f1"/>
      <g fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity="0.35">
        <polyline points="5,11 10,15 5,19"/>
      </g>
      <path d="M11.6,20 L17.1,20 L14.6,24 A2.2,2.2 0 0,1 10.6,22 Z" fill="white" fill-opacity="0.4"/>
      <g fill="none" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <line x1="10.6" y1="22" x2="19.6" y2="7"/>
        <line x1="14.6" y1="24" x2="23.6" y2="9"/>
        <path d="M10.6,22 A2.2,2.2 0,0,0 14.6,24"/>
        <line x1="18.1" y1="6" x2="25.1" y2="10"/>
      </g>
    </svg>
  </div>
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

  // Build app menu
  buildMenu({
    onNewConversation: newConversation,
    onOpenSettings: openSettings,
    getWindow: () => mainWindow,
  });

  // Create window first so progress messages can reach the renderer
  createWindow();

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

    // Load the UI from the Python server
    await mainWindow.loadURL(`http://127.0.0.1:${port}/ui/`);
    sendToRenderer("server-ready", { port });
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

// ---------------------------------------------------------------------------
// IPC handlers
// ---------------------------------------------------------------------------

ipcMain.handle("get-server-port", () => {
  return pythonManager ? pythonManager.port : null;
});

ipcMain.handle("open-external", (_event, url) => {
  shell.openExternal(url);
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

ipcMain.handle("terminal:input", (_event, { projectId, data }) => {
  ptyManager.write(projectId, data);
});

ipcMain.handle("terminal:resize", (_event, { projectId, cols, rows }) => {
  ptyManager.resize(projectId, cols, rows);
});

ipcMain.handle("terminal:detach", (_event, { projectId }) => {
  ptyManager.detach(projectId);
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
