const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");
const { PythonManager } = require("./python-manager");

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
    },
  });

  mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.on("ready", async () => {
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

app.on("window-all-closed", () => {
  if (pythonManager) {
    pythonManager.stop();
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

// Expose server port to renderer
ipcMain.handle("get-server-port", () => {
  return pythonManager ? pythonManager.port : null;
});
