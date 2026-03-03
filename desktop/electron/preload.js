const { contextBridge, ipcRenderer } = require("electron");

// Load highlight.js in Node context and expose to renderer
const hljs = require("highlight.js/lib/core");
hljs.registerLanguage("python", require("highlight.js/lib/languages/python"));
hljs.registerLanguage("javascript", require("highlight.js/lib/languages/javascript"));
hljs.registerLanguage("typescript", require("highlight.js/lib/languages/typescript"));
hljs.registerLanguage("bash", require("highlight.js/lib/languages/bash"));
hljs.registerLanguage("shell", require("highlight.js/lib/languages/bash"));
hljs.registerLanguage("json", require("highlight.js/lib/languages/json"));
hljs.registerLanguage("markdown", require("highlight.js/lib/languages/markdown"));
hljs.registerLanguage("yaml", require("highlight.js/lib/languages/yaml"));
hljs.registerLanguage("xml", require("highlight.js/lib/languages/xml"));
hljs.registerLanguage("html", require("highlight.js/lib/languages/xml"));
hljs.registerLanguage("css", require("highlight.js/lib/languages/css"));
hljs.registerLanguage("sql", require("highlight.js/lib/languages/sql"));
hljs.registerLanguage("latex", require("highlight.js/lib/languages/latex"));
hljs.registerLanguage("tex", require("highlight.js/lib/languages/latex"));

contextBridge.exposeInMainWorld("nicolas", {
  // Server lifecycle
  onServerReady: (callback) =>
    ipcRenderer.on("server-ready", (_event, data) => callback(data)),
  onServerError: (callback) =>
    ipcRenderer.on("server-error", (_event, data) => callback(data)),
  getServerPort: () => ipcRenderer.invoke("get-server-port"),

  // Navigation
  onDeepLink: (callback) =>
    ipcRenderer.on("deep-link", (_event, url) => callback(url)),
  onNewConversation: (callback) =>
    ipcRenderer.on("new-conversation", () => callback()),
  onOpenSettings: (callback) =>
    ipcRenderer.on("open-settings", () => callback()),

  // Settings
  getSettings: () => ipcRenderer.invoke("get-settings"),
  saveSettings: (settings) => ipcRenderer.invoke("save-settings", settings),

  // Shell
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
});

// Expose hljs separately (functions can't be passed through contextBridge directly,
// but we can wrap the needed methods)
contextBridge.exposeInMainWorld("hljs", {
  highlight: (code, opts) => hljs.highlight(code, opts),
  highlightAuto: (code) => hljs.highlightAuto(code),
  getLanguage: (name) => !!hljs.getLanguage(name),
});
