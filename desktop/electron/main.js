const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("path");
const fs = require("fs");
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
  // Build app menu
  buildMenu({
    onNewConversation: newConversation,
    onOpenSettings: openSettings,
    getWindow: () => mainWindow,
  });

  pythonManager = new PythonManager();

  try {
    const port = await pythonManager.start();
    createWindow();
    mainWindow.webContents.on("did-finish-load", () => {
      mainWindow.webContents.send("server-ready", { port });
    });
  } catch (err) {
    console.error("Failed to start Python server:", err);
    createWindow();
    mainWindow.webContents.on("did-finish-load", () => {
      mainWindow.webContents.send("server-error", {
        message: err.message || "Failed to start Python server",
      });
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

// Handle nicolas:// deep links (for auth callback)
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
      authToken: vars.DISTILLATE_AUTH_TOKEN || "",
    };
  } catch {
    return { apiKey: "", authToken: "" };
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
  if (settings.authToken !== undefined) {
    if (settings.authToken) {
      vars.DISTILLATE_AUTH_TOKEN = settings.authToken;
    } else {
      delete vars.DISTILLATE_AUTH_TOKEN;
    }
  }

  fs.writeFileSync(envFile, _serializeEnv(vars), "utf-8");
  return { ok: true };
});
