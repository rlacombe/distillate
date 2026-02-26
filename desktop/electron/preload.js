const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("nicolas", {
  onServerReady: (callback) =>
    ipcRenderer.on("server-ready", (_event, data) => callback(data)),
  onServerError: (callback) =>
    ipcRenderer.on("server-error", (_event, data) => callback(data)),
  onDeepLink: (callback) =>
    ipcRenderer.on("deep-link", (_event, url) => callback(url)),
  getServerPort: () => ipcRenderer.invoke("get-server-port"),
});
