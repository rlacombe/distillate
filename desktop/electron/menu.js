const { app, Menu, shell } = require("electron");

/**
 * Build the application menu.
 * @param {object} opts
 * @param {Function} opts.onNewConversation  — called when user picks File > New Conversation
 * @param {Function} opts.onOpenSettings     — called when user picks Settings (Cmd+,)
 * @param {Function} opts.getWindow          — returns the current BrowserWindow (or null)
 */
function buildMenu({ onNewConversation, onOpenSettings, getWindow }) {
  const isMac = process.platform === "darwin";

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
          label: "New Conversation",
          accelerator: "CmdOrCtrl+N",
          click: () => onNewConversation(),
        },
        { type: "separator" },
        // On Windows/Linux, put Settings in File menu
        ...(!isMac
          ? [
              {
                label: "Settings\u2026",
                accelerator: "Ctrl+,",
                click: () => onOpenSettings(),
              },
              { type: "separator" },
            ]
          : []),
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },

    // Edit — essential for copy/paste to work on macOS
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        ...(isMac
          ? [
              { role: "pasteAndMatchStyle" },
              { role: "delete" },
              { role: "selectAll" },
            ]
          : [{ role: "delete" }, { type: "separator" }, { role: "selectAll" }]),
      ],
    },

    // View
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
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
