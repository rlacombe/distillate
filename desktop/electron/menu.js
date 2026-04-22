const { app, Menu, shell, dialog } = require("electron");
const fs = require("fs");
const path = require("path");

/**
 * Build the application menu.
 * @param {object} opts
 * @param {Function} opts.onNewConversation  — called when user picks File > New Conversation
 * @param {Function} opts.onOpenSettings     — called when user picks Settings (Cmd+,)
 * @param {Function} opts.getWindow          — returns the current BrowserWindow (or null)
 */
function buildMenu({ onNewConversation, onOpenSettings, getWindow }) {
  const isMac = process.platform === "darwin";

  // Read current settings for menu checkbox state
  let privateRepos = false;
  try {
    const envPath = path.join(
      process.env.HOME || process.env.USERPROFILE,
      ".config", "distillate", ".env",
    );
    const text = fs.readFileSync(envPath, "utf-8");
    privateRepos = /PRIVATE_REPOS\s*=\s*true/i.test(text);
  } catch {}

  const template = [
    // macOS app menu
    ...(isMac
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" },
              { type: "separator" },
              {
                label: "Settings\u2026",
                accelerator: "CmdOrCtrl+,",
                click: () => onOpenSettings(),
              },
              { type: "separator" },
              { role: "services" },
              { type: "separator" },
              { role: "hide" },
              { role: "hideOthers" },
              { role: "unhide" },
              { type: "separator" },
              { role: "quit" },
            ],
          },
        ]
      : []),

    // File
    {
      label: "File",
      submenu: [
        {
          label: "New Thread",
          accelerator: "CmdOrCtrl+N",
          click: () => onNewConversation(),
        },
        { type: "separator" },
        {
          label: "Export State\u2026",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.send("menu-export-state");
          },
        },
        {
          label: "Import State\u2026",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.send("menu-import-state");
          },
        },
        { type: "separator" },
        {
          label: "Private GitHub Repos",
          type: "checkbox",
          checked: privateRepos,
          click: (menuItem) => {
            const val = menuItem.checked;
            const envPath = path.join(
              process.env.HOME || process.env.USERPROFILE,
              ".config", "distillate", ".env",
            );
            let vars = {};
            try {
              const text = fs.readFileSync(envPath, "utf-8");
              for (const line of text.split("\n")) {
                const eq = line.indexOf("=");
                if (eq > 0) vars[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
              }
            } catch {
              fs.mkdirSync(path.dirname(envPath), { recursive: true });
            }
            vars.PRIVATE_REPOS = val ? "true" : "false";
            const out = Object.entries(vars).map(([k, v]) => `${k}=${v}`).join("\n") + "\n";
            fs.writeFileSync(envPath, out, "utf-8");
          },
        },
        { type: "separator" },
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },

    // Edit — registerAccelerator:false lets Cmd+C/V/A reach the xterm.js handler
    // instead of being consumed by the native menu. Chromium handles them for DOM inputs.
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { label: "Copy", accelerator: "CmdOrCtrl+C", registerAccelerator: false, role: "copy" },
        { label: "Paste", accelerator: "CmdOrCtrl+V", registerAccelerator: false, role: "paste" },
        ...(isMac
          ? [
              { role: "pasteAndMatchStyle" },
              { role: "delete" },
              { label: "Select All", accelerator: "CmdOrCtrl+A", registerAccelerator: false, role: "selectAll" },
            ]
          : [{ role: "delete" }, { type: "separator" }, { label: "Select All", accelerator: "CmdOrCtrl+A", registerAccelerator: false, role: "selectAll" }]),
      ],
    },

    // View
    {
      label: "View",
      submenu: [
        {
          label: "Focus Nicolas",
          accelerator: "CmdOrCtrl+K",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.send("focus-nicolas");
          },
        },
        {
          label: "Toggle Sidebar",
          accelerator: "CmdOrCtrl+B",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.executeJavaScript(
              'if(typeof togglePane==="function"){togglePane("sidebar-left");}'
            ).catch(() => {});
          },
        },
        { type: "separator" },
        {
          label: "Refresh Data",
          accelerator: "CmdOrCtrl+R",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.executeJavaScript(
              "if(typeof reloadCurrentProject==='function'){reloadCurrentProject();fetchPapersData();}"
            ).catch(() => {});
          },
        },
        {
          label: "Hard Reload",
          accelerator: "CmdOrCtrl+Shift+R",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.reloadIgnoringCache();
          },
        },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },

    // Window
    {
      label: "Window",
      submenu: [
        { role: "minimize" },
        { role: "zoom" },
        ...(isMac
          ? [
              { type: "separator" },
              { role: "front" },
              { type: "separator" },
              { role: "window" },
            ]
          : [{ role: "close" }]),
      ],
    },

    // Help
    {
      label: "Help",
      submenu: [
        {
          label: "Keyboard Shortcuts",
          accelerator: "CmdOrCtrl+/",
          click: () => {
            const win = getWindow();
            if (win) win.webContents.executeJavaScript(
              'if(typeof openShortcutsOverlay==="function"){openShortcutsOverlay();}'
            ).catch(() => {});
          },
        },
        { type: "separator" },
        {
          label: "Documentation",
          click: () => shell.openExternal("https://distillate.dev"),
        },
        {
          label: "Report an Issue",
          click: () =>
            shell.openExternal(
              "https://github.com/rlacombe/distillate/issues"
            ),
        },
        { type: "separator" },
        {
          label: "Reset Python Environment",
          click: async () => {
            const { response } = await dialog.showMessageBox({
              type: "warning",
              buttons: ["Cancel", "Reset"],
              defaultId: 0,
              cancelId: 0,
              title: "Reset Python Environment",
              message: "This will delete the bundled Python environment and restart the app. Use this if the app is not working correctly.",
            });
            if (response === 1) {
              const userData = app.getPath("userData");
              const venvDir = path.join(userData, "python-env");
              const versionFile = path.join(userData, "distillate-version.txt");
              try { fs.rmSync(venvDir, { recursive: true, force: true }); } catch (_) {}
              try { fs.unlinkSync(versionFile); } catch (_) {}
              app.relaunch();
              app.exit(0);
            }
          },
        },
        { type: "separator" },
        {
          label: "View on GitHub",
          click: () =>
            shell.openExternal("https://github.com/rlacombe/distillate"),
        },
      ],
    },
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

module.exports = { buildMenu };
