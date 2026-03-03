const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const fsp = require("fs/promises");
const { PythonManager } = require("./python-manager");
const { buildMenu } = require("./menu");

let mainWindow = null;
let pythonManager = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 700,
    height: 850,
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
  });

  mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
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

app.on("ready", async () => {
  buildMenu({
    onNewConversation: newConversation,
    onOpenSettings: openSettings,
    getWindow: () => mainWindow,
  });

  // Create window first so we can show progress during Python startup
  createWindow();

  // Queue IPC messages until the renderer finishes loading
  let rendererReady = false;
  const messageQueue = [];

  const sendToRenderer = (channel, data) => {
    if (rendererReady && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send(channel, data);
    } else {
      messageQueue.push({ channel, data });
    }
  };

  mainWindow.webContents.on("did-finish-load", () => {
    rendererReady = true;
    for (const { channel, data } of messageQueue) {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send(channel, data);
      }
    }
    messageQueue.length = 0;
  });

  // Start Python server (with auto-update in production)
  pythonManager = new PythonManager();

  try {
    const port = await pythonManager.start((info) => {
      sendToRenderer("update-progress", info);
    });
    sendToRenderer("server-ready", { port });
  } catch (err) {
    console.error("Failed to start Python server:", err);
    sendToRenderer("server-error", {
      message: err.message || "Failed to start Python server",
    });
  }
});

app.on("window-all-closed", async () => {
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
      apiKey: vars.ANTHROPIC_API_KEY || "",
    };
  } catch {
    return { apiKey: "" };
  }
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
  if (settings.apiKey !== undefined) {
    vars.ANTHROPIC_API_KEY = settings.apiKey;
  }

  fs.writeFileSync(envFile, _serializeEnv(vars), "utf-8");

  // Restart the Python server so it picks up the new API key
  if (pythonManager) {
    (async () => {
      try {
        await pythonManager.stop();
        const port = await pythonManager.start((info) => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send("update-progress", info);
          }
        });
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send("server-ready", { port });
        }
      } catch (err) {
        console.error("Failed to restart Python server:", err);
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send("server-error", {
            message: err.message || "Failed to restart",
          });
        }
      }
    })();
  }

  return { ok: true };
});

// ---------------------------------------------------------------------------
// Reset Python environment (recovery)
// ---------------------------------------------------------------------------

ipcMain.handle("reset-python-env", async () => {
  const userData = app.getPath("userData");
  const extDir = path.join(userData, "python-env");
  const versionFile = path.join(userData, "distillate-version.txt");

  await fsp.rm(extDir, { recursive: true, force: true });
  await fsp.rm(versionFile, { force: true });

  app.relaunch();
  app.exit(0);
});
