/* ───── Layout — panes, tabs, terminal, resize, keyboard shortcuts ───── */

/* ───── Pane management ───── */

function togglePane(paneName) {
  // Only sidebar-left is togglable via this helper. Bottom panel is retired;
  // sidebar-right is reserved for future content. Activity bar buttons
  // route exclusively to sidebar-left (via data-sidebar-view).
  const paneMap = {
    "sidebar-left": sidebarLeft,
  };
  const pane = paneMap[paneName];
  if (!pane) return;

  pane.classList.toggle("collapsed");

  // Refit terminal after pane toggle — the ResizeObserver sometimes misses
  // the layout change because the xterm container's dimensions settle after
  // a rAF or two. Use the centralized gatekeeper which debounces + deduplicates.
  if (terminalInitialized && window.xtermBridge) {
    requestAnimationFrame(() => resizeTerminal());
  }

  // Refit chart — same issue as xterm: ResizeObserver misses flex layout changes.
  requestAnimationFrame(() => {
    if (typeof window._distillateChartRedraw === "function") window._distillateChartRedraw();
  });

  // Left sidebar: update all left-sidebar activity buttons
  const isOpen = !pane.classList.contains("collapsed");
  document.querySelectorAll('.activity-btn[data-pane="sidebar-left"]').forEach((b) => {
    const isActiveView = typeof _activeSidebarView !== "undefined" && b.dataset.sidebarView === _activeSidebarView;
    b.classList.toggle("active", isOpen && isActiveView);
    if (isOpen) b.classList.remove("has-notification");
  });

  saveLayoutState();
}

// Activity bar buttons
document.querySelectorAll(".activity-btn[data-pane]").forEach((btn) => {
  btn.addEventListener("click", () => {
    // Close settings overlay if it's open
    if (typeof closeSettings === "function") {
      const overlay = document.getElementById("settings-overlay");
      if (overlay && !overlay.hidden) closeSettings();
    }

    const pane = btn.dataset.pane;
    const view = btn.dataset.sidebarView;
    const action = btn.dataset.action;

    // The Nicolas shell button does both: activate the main-window chat
    // AND open the sessions sidebar in one click.
    if (action === "show-nicolas" && typeof showNicolasMain === "function") {
      showNicolasMain();
    }

    if (pane === "sidebar-left" && view) {
      // Left sidebar view switching
      const sidebar = document.getElementById("sidebar-left");
      const isCollapsed = sidebar?.classList.contains("collapsed");
      const isSameView = typeof _activeSidebarView !== "undefined" && _activeSidebarView === view;

      if (isCollapsed) {
        // Open and switch to this view
        togglePane("sidebar-left");
        if (typeof switchSidebarView === "function") switchSidebarView(view);
      } else if (isSameView) {
        // Already showing this view — collapse (unless this is the Nicolas
        // shell button, which should stay open when clicked to re-focus chat)
        if (action !== "show-nicolas") togglePane("sidebar-left");
      } else {
        // Open but different view — switch
        if (typeof switchSidebarView === "function") switchSidebarView(view);
      }

      // Update active state on all left-sidebar buttons
      const sidebarOpen = !sidebar?.classList.contains("collapsed");
      document.querySelectorAll('.activity-btn[data-pane="sidebar-left"]').forEach((b) => {
        b.classList.toggle("active", sidebarOpen && b.dataset.sidebarView === view);
      });
    } else if (pane) {
      togglePane(pane);
    }
  });
});


// Editor tabs (Control Panel / Session / Notebook)
const editorViews = ["control-panel", "session", "results", "prompt-editor", "calibration"];

function switchEditorTab(viewName, { skipSessionAttach = false } = {}) {
  document.querySelectorAll(".editor-tab").forEach((t) => t.classList.remove("active"));
  document.querySelector(`.editor-tab[data-view="${viewName}"]`)?.classList.add("active");

  for (const v of editorViews) {
    const el = document.getElementById(`${v}-view`);
    if (el) el.classList.toggle("hidden", v !== viewName);
  }

  // Hide document viewer when leaving the session view
  if (viewName !== "session" && typeof hideDocumentViewer === "function") {
    hideDocumentViewer();
  }

  if (viewName === "control-panel") {
    const onPapers = typeof _activeSidebarView !== "undefined" && _activeSidebarView === "papers";
    if (currentProjectId && !onPapers) {
      renderProjectDetail(currentProjectId);
    }
  }
  if (viewName === "results") {
    if (currentProjectId) {
      loadResults(currentProjectId);
    } else {
      showResultsNoSelection();
    }
  }
  if (viewName === "prompt-editor") {
    if (currentProjectId) {
      showSetupWithContent();
      loadPromptEditor(currentProjectId);
    } else {
      showSetupNoSelection();
    }
  }
  if (viewName === "calibration") {
    if (currentProjectId) {
      loadCalibration(currentProjectId);
    } else {
      resetCalibrationTab();
    }
  }
  if (viewName === "session" && !skipSessionAttach) {
    if (currentProjectId) {
      showSessionTerminal(currentProjectId);
    } else {
      showSessionEmpty();
    }
    // Clear notification dot
    const sessionTab = document.querySelector('.editor-tab[data-view="session"]');
    if (sessionTab) sessionTab.classList.remove("has-update");
  }
}

document.querySelectorAll(".editor-tab").forEach((tab) => {
  tab.addEventListener("click", () => switchEditorTab(tab.dataset.view));
});

// Session tab — xterm.js terminal

let _termReadyPromise = null;

function ensureTerminalReady() {
  if (terminalInitialized) return Promise.resolve(true);
  if (_termReadyPromise) return _termReadyPromise; // coalesce concurrent calls

  _termReadyPromise = new Promise(async (resolve) => {
    if (!window.xtermBridge) { resolve(false); return; }

    // Wait for fonts (especially the bundled Nerd Font) before init —
    // xterm.js measures cell size at init time and won't re-measure.
    try { await document.fonts.ready; } catch {}

    let attempts = 0;
    async function tryInit() {
      const container = document.getElementById("xterm-container");
      if (!container || container.classList.contains("hidden") || container.offsetHeight === 0) {
        if (++attempts < 20) { requestAnimationFrame(tryInit); return; }
        _termReadyPromise = null; resolve(false); return;
      }
      const ok = await window.xtermBridge.init("xterm-container");
      if (ok) {
        terminalInitialized = true;
        window.xtermBridge.onData((data) => {
          if (currentTerminalProject && window.nicolas) {
            window.nicolas.terminalInput(currentTerminalProject, data);
          } else if (data && data.length > 1) {
            // Multi-char input (paste/drop) with no attached session — warn
            // the user instead of silently dropping their data.
            console.warn("[terminal] input dropped: no active session, data length=", data.length);
            if (window.xtermBridge) {
              window.xtermBridge.write("\r\n\x1b[33m[No active session] Paste was not delivered — launch or attach a session first.\x1b[0m\r\n");
            }
          }
        });
        // Clickable filenames — hover underlines, click opens as a canvas.
        if (window.xtermBridge.registerFileLinkProvider) {
          window.xtermBridge.registerFileLinkProvider(async (filename) => {
            // currentTerminalProject is the terminal key, not the workspace
            // ID. For workspace sessions the key is "ws_<wsId>_<sessionId>";
            // _currentSessionContext.workspaceId holds the real ID.
            const wsId = _currentSessionContext?.workspaceId || currentTerminalProject;
            if (!wsId || typeof window.openCanvasInline !== "function") return;
            // Resolve relative path against workspace root / first repo.
            let absPath = filename;
            if (!filename.startsWith("/")) {
              try {
                const r = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}`);
                const d = await r.json();
                const ws = d.workspace || d;
                const root = ws.root_path || (ws.repos?.[0]?.path) || "";
                if (root) absPath = `${root}/${filename}`;
              } catch {}
            }
            try {
              const resp = await fetch(`http://127.0.0.1:${serverPort}/workspaces/${wsId}/canvases`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ import_path: absPath }),
              });
              const data = await resp.json();
              if (!data.ok) {
                if (typeof showToast === "function") showToast(data.error || "Could not open file", "error");
                return;
              }
              window.openCanvasInline(wsId, data.canvas.id);
            } catch (err) {
              if (typeof showToast === "function") showToast(`Could not open file: ${err.message}`, "error");
            }
          });
        }
        // Wheel scroll uses the CombinedCircularList scrollback (same as drag-to-edge).
        // Nothing is written to the PTY — TUI modals are never corrupted.
        // Accumulate deltaY so trackpad micro-deltas don't over-scroll.
        const xtermEl = document.getElementById("xterm-container");
        if (xtermEl) {
          let _wheelAccum = 0;
          const WHEEL_THRESHOLD = 75;
          xtermEl.addEventListener("wheel", (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (!currentTerminalProject || !window.nicolas) return;
            // During an active mouse drag (buttons != 0) or while text is
            // selected, let xterm.js handle the wheel natively so the user
            // can scroll the viewport to extend a selection beyond the
            // visible area.  Scroll xterm's buffer directly for speed.
            if (e.buttons !== 0 || (window.xtermBridge && window.xtermBridge.hasSelection())) {
              const lines = Math.round(e.deltaY / 25) || (e.deltaY > 0 ? 1 : -1);
              window.xtermBridge.scrollLines(lines);
              return;
            }
            _wheelAccum += e.deltaY;
            while (Math.abs(_wheelAccum) >= WHEEL_THRESHOLD) {
              const direction = _wheelAccum > 0 ? "down" : "up";
              _wheelAccum -= Math.sign(_wheelAccum) * WHEEL_THRESHOLD;
              window.xtermBridge.wheelScroll(direction);
            }
          }, { capture: true, passive: false });
        }
      }
      _termReadyPromise = null; resolve(ok);
    }
    requestAnimationFrame(tryInit);
  });
  return _termReadyPromise;
}

let currentTerminalSession = null;
let _termTransitioning = false; // guard ResizeObserver during session switch

async function attachToTerminalSession(projectId, sessionName) {
  if (!window.nicolas || !window.xtermBridge) return;

  // Skip re-attach if already on this exact session, but still re-fit in case
  // the container height changed (e.g. session title bar shown/hidden between
  // the auto-attach on launch and the user manually clicking the sidebar item).
  if (currentTerminalProject === projectId && currentTerminalSession === sessionName) {
    if (terminalInitialized && window.xtermBridge) {
      requestAnimationFrame(() => resizeTerminal());
    }
    return;
  }

  // Detach previous
  if (currentTerminalProject && currentTerminalProject !== projectId) {
    window.nicolas.terminalDetach(currentTerminalProject);
  }

  _termTransitioning = true; // suppress ResizeObserver fits during swap
  // Give the tmux server a quiet window to flush the attach-session pane
  // redraw. The 1 Hz /workspaces/agent-status poll issues `tmux list-panes`
  // + `tmux capture-pane`, which wakes the server mid-attach and causes it
  // to spit the redraw out as a chunked burst — visible as "view scrolls
  // through history a couple seconds after clicking." 3 s gives Claude
  // Code's TUI time to finish repainting even on a busy server; at worst
  // the sidebar-status dots pause for that window, which is fine since
  // the user just clicked a session and isn't scanning other sessions.
  window._attachSettlingUntil = Date.now() + 3000;
  const ready = await ensureTerminalReady();
  if (!ready) { _termTransitioning = false; console.warn("[terminal] init failed"); return; }

  window.xtermBridge.clear();
  window.xtermBridge.setTmuxName(sessionName);   // enable drag-scroll capture
  currentTerminalProject = projectId;
  currentTerminalSession = sessionName;

  // Delay fit() until the layout has settled (tab switch + title bar change
  // can leave the container at stale dimensions for a frame or two).
  // Three frames gives the flex layout time to resolve final heights.
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(r))));
  window.xtermBridge.fit();
  _termTransitioning = false;

  // Reapply theme + 256-color palette after the container was hidden/shown.
  // xterm's canvas renderer can lose its color state when the container
  // cycles through display:none, causing washed-out / invisible text.
  if (window.xtermBridge.reapplyColors) window.xtermBridge.reapplyColors();

  const dims = window.xtermBridge.getDimensions();
  // Arm the attach-burst buffer before triggering the PTY attach so the
  // first PTY chunk lands in the buffer, not on screen. The accumulated
  // alt-screen redraw flushes in one synchronous _term.write() once the
  // burst goes idle (or 1.5 s hard cap) — xterm paints the final pane
  // state in a single frame instead of "scrolling through" row-by-row.
  if (window.xtermBridge.startAttachBurst) window.xtermBridge.startAttachBurst();
  const result = await window.nicolas.terminalAttach(projectId, sessionName, dims.cols, dims.rows);

  if (result && !result.ok) {
    console.error(`[terminal] PTY attach failed: ${result.reason}`);
    if (window.xtermBridge.flushAttachBurst) window.xtermBridge.flushAttachBurst();
    window.xtermBridge.write(`\r\n\x1b[31m[Terminal error] ${result.reason || "unknown"}\x1b[0m\r\n`);
    currentTerminalProject = null;
    currentTerminalSession = null;
    return { ok: false, reason: result.reason };
  }

  // Seed last-sent dims from the attach so the ResizeObserver doesn't
  // re-send the same dims it just attached with (would cause tmux to
  // re-validate the pane and burst a redraw).
  _lastSentCols = dims.cols;
  _lastSentRows = dims.rows;

  // After layout settles, re-fit and send the resize IPC directly.
  // This intentionally bypasses resizeTerminal()'s _attachSettlingUntil
  // guard — the attach flow itself set that window, and this post-attach
  // resize is the one authorized caller that must fire inside it.
  setTimeout(() => {
    if (currentTerminalProject !== projectId || !window.xtermBridge) return;
    if (window.xtermBridge.reapplyColors) window.xtermBridge.reapplyColors();
    window.xtermBridge.fit();
    const d = window.xtermBridge.getDimensions();
    if (d.cols < 10 || d.rows < 3) return;
    if (d.cols === _lastSentCols && d.rows === _lastSentRows) return;
    _lastSentCols = d.cols;
    _lastSentRows = d.rows;
    window.nicolas.terminalResize(projectId, d.cols, d.rows);
  }, 300);
  return { ok: true };
}

function detachTerminal() {
  if (currentTerminalProject && window.nicolas) {
    window.nicolas.terminalDetach(currentTerminalProject);
  }
  currentTerminalProject = null;
  currentTerminalSession = null;
}

function showSessionTerminal(projectId) {
  // Find the project and its active tmux session
  const proj = cachedProjects.find((p) => p.id === projectId);
  if (!proj || proj.active_sessions === 0) {
    // Don't flash "No active session" while launch is in flight
    if (_sessionTransition === "launching") {
      showSessionConnecting();
    } else {
      showSessionEmpty();
    }
    return;
  }

  // Get session name from project data
  const sessions = proj.sessions || {};
  const activeSession = Object.values(sessions).find((s) => s.tmux_session);
  const sessionName = activeSession?.tmux_session;
  if (!sessionName) {
    showSessionEmpty();
    return;
  }

  showTerminalForSession(projectId, sessionName);
}

// Track current session context for title bar actions
let _currentSessionContext = null; // { workspaceId, sessionId, tmuxName, terminalKey, type }

/**
 * Shared entry point: switch to Session tab, show xterm container, attach PTY.
 * Used by both experiment sessions and workspace coding sessions.
 */
function showTerminalForSession(terminalKey, tmuxSessionName, projectName, agentName, workspaceId, canvasId) {
  const emptyEl = document.getElementById("session-empty");
  const xtermEl = document.getElementById("xterm-container");
  const titleEl = document.getElementById("session-title");

  if (emptyEl) emptyEl.classList.add("hidden");
  if (xtermEl) xtermEl.classList.remove("hidden");

  // Update session context for title bar actions
  // Parse workspace session info from terminalKey: "ws_<wsId>_<sessionId>" or "agent-<id>"
  const isWsSession = terminalKey.startsWith("ws_");
  const isAgentSession = terminalKey.startsWith("agent-");
  if (isWsSession) {
    const parts = terminalKey.split("_");
    _currentSessionContext = {
      workspaceId: parts[1],
      sessionId: parts.slice(2).join("_"),
      tmuxName: tmuxSessionName,
      terminalKey,
      type: "workspace",
    };
  } else if (isAgentSession) {
    _currentSessionContext = {
      agentId: terminalKey.replace("agent-", ""),
      tmuxName: tmuxSessionName,
      terminalKey,
      type: "agent",
    };
  } else {
    // Experiment session
    _currentSessionContext = {
      projectId: terminalKey,
      tmuxName: tmuxSessionName,
      terminalKey,
      type: "experiment",
    };
  }

  // Show/hide action buttons based on session type
  const actionsEl = document.getElementById("session-title-actions");
  if (actionsEl) {
    // Show actions for workspace coding sessions and agents (not experiment sessions)
    actionsEl.classList.toggle("hidden", _currentSessionContext.type === "experiment");
  }
  // Agents are persistent — they don't "complete" like workspace coding
  // sessions do. Hide the Complete button for agent sessions.
  const completeBtn = document.getElementById("session-btn-complete");
  if (completeBtn) {
    completeBtn.classList.toggle("hidden", _currentSessionContext.type === "agent");
  }
  // Show edit button only for agent sessions
  const editBtn = document.getElementById("session-btn-edit-agent");
  if (editBtn) {
    editBtn.classList.toggle("hidden", _currentSessionContext.type !== "agent");
  }

  // Show session title bar if we have project context
  if (titleEl && projectName) {
    const projEl = document.getElementById("session-title-project");
    projEl.textContent = projectName;
    projEl.style.cursor = workspaceId ? "pointer" : "";
    projEl.onclick = workspaceId ? () => selectWorkspace(workspaceId) : null;
    document.getElementById("session-title-agent").textContent = agentName || "";
    titleEl.classList.remove("hidden");
  } else if (titleEl) {
    titleEl.classList.add("hidden");
  }

  // Document viewer: show for writing sessions, hide for coding sessions
  if (canvasId && isWsSession && typeof showDocumentViewer === "function") {
    showDocumentViewer(_currentSessionContext.workspaceId, canvasId);
  } else if (typeof hideDocumentViewer === "function") {
    hideDocumentViewer();
  }

  switchEditorTab("session", { skipSessionAttach: true });

  // Electron path: use the preload bridge
  if (window.nicolas && window.xtermBridge) {
    attachToTerminalSession(terminalKey, tmuxSessionName);
    // Focus the terminal so keystrokes go straight to it
    setTimeout(() => { if (window.xtermBridge) window.xtermBridge.focus(); }, 100);
    return;
  }

  // Browser path: WebSocket terminal proxy
  _attachViaBrowserWs(tmuxSessionName);
}

// --- Session title bar actions ---

async function _stopCurrentSession() {
  const ctx = _currentSessionContext;
  if (!ctx) return;

  if (ctx.type === "workspace") {
    try {
      const resp = await fetch(
        `http://127.0.0.1:${serverPort}/workspaces/${ctx.workspaceId}/sessions/${ctx.sessionId}/stop`,
        { method: "POST" });
      const data = await resp.json();
      if (data.success) {
        detachTerminal();
        showSessionEmpty();
        if (typeof showToast === "function") showToast("Session stopped", "info");
        if (typeof fetchWorkspaces === "function") fetchWorkspaces();
      } else {
        if (typeof showToast === "function") showToast(data.error || "Failed to stop", "error");
      }
    } catch (e) {
      if (typeof showToast === "function") showToast("Failed to stop session", "error");
    }
  } else if (ctx.type === "agent") {
    if (typeof stopAgent === "function") stopAgent(ctx.agentId);
  }
}

async function _restartCurrentSession() {
  const ctx = _currentSessionContext;
  if (!ctx) return;

  if (ctx.type === "workspace") {
    try {
      detachTerminal();
      if (window.xtermBridge) window.xtermBridge.clear();
      if (window.xtermBridge) window.xtermBridge.write("\r\n\x1b[33mRestarting session...\x1b[0m\r\n");
      const resp = await fetch(
        `http://127.0.0.1:${serverPort}/workspaces/${ctx.workspaceId}/sessions/${ctx.sessionId}/restart`,
        { method: "POST" });
      const data = await resp.json();
      if (data.success) {
        if (typeof showToast === "function")
          showToast(data.message || "Session restarted", "success");
        // Re-attach to the restarted tmux
        const newTmux = data.tmux_name || ctx.tmuxName;
        setTimeout(() => {
          attachToTerminalSession(ctx.terminalKey, newTmux);
          if (typeof fetchWorkspaces === "function") fetchWorkspaces();
        }, 500);
      } else {
        if (typeof showToast === "function") showToast(data.error || "Failed to restart", "error");
      }
    } catch (e) {
      if (typeof showToast === "function") showToast("Failed to restart session", "error");
    }
  } else if (ctx.type === "agent") {
    // For agents: stop then re-select to start fresh
    if (typeof stopAgent === "function") {
      await stopAgent(ctx.agentId);
      setTimeout(() => {
        if (typeof selectAgent === "function") selectAgent(ctx.agentId);
      }, 500);
    }
  }
}

async function _completeCurrentSession() {
  const ctx = _currentSessionContext;
  if (!ctx || ctx.type !== "workspace") return;

  const btn = document.getElementById("session-btn-complete");
  const stopSpinner = typeof _startWrapupSpinner === "function"
    ? _startWrapupSpinner(btn)
    : (() => { if (btn) { btn.disabled = true; btn.title = "Wrapping up..."; }
               return () => { if (btn) { btn.disabled = false; btn.title = "Complete session"; } }; })();
  if (typeof showToast === "function") {
    showToast("Asking Claude to summarise the session...", "info");
  }

  try {
    const resp = await fetch(
      `http://127.0.0.1:${serverPort}/workspaces/${ctx.workspaceId}/sessions/${ctx.sessionId}/complete`,
      { method: "POST" });
    const data = await resp.json();
    if (data.success) {
      // Session stays running — don't detach the terminal here. The dock's
      // Save button persists + ends the session; its X calls the
      // wrapup/discard endpoint so the session continues untouched.
      if (typeof window.addDraftToDock === "function") {
        window.addDraftToDock(ctx.workspaceId, ctx.sessionId, data.session_name, data.summary);
      }
    } else {
      if (typeof showToast === "function") showToast(data.error || "Failed to wrap up", "error");
    }
  } catch (e) {
    if (typeof showToast === "function") showToast("Failed to wrap up session", "error");
  } finally {
    stopSpinner();
  }
}

// Wire up title bar buttons
document.getElementById("session-btn-stop")?.addEventListener("click", (e) => {
  e.stopPropagation();
  _stopCurrentSession();
});
document.getElementById("session-btn-complete")?.addEventListener("click", (e) => {
  e.stopPropagation();
  _completeCurrentSession();
});
document.getElementById("session-btn-restart")?.addEventListener("click", (e) => {
  e.stopPropagation();
  _restartCurrentSession();
});
document.getElementById("session-btn-transcript")?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (typeof window.openTranscriptOverlay === "function") {
    window.openTranscriptOverlay();
  }
});
document.getElementById("session-btn-edit-agent")?.addEventListener("click", (e) => {
  e.stopPropagation();
  const ctx = _currentSessionContext;
  if (ctx && ctx.type === "agent" && typeof editAgent === "function") {
    editAgent(ctx.agentId);
  }
});

let _browserTermWs = null;
let _browserTerm = null;
let _browserTermFit = null;
let _browserTermResizeObs = null;

async function _attachViaBrowserWs(tmuxName) {
  // Load xterm.js from CDN if not already present
  if (!window.Terminal) {
    await _loadXtermScript("https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js");
    await _loadXtermScript("https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js");
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css";
    document.head.appendChild(link);
  }

  const container = document.getElementById("xterm-container");
  if (!container) return;

  // Clean up previous terminal
  if (_browserTermWs) { try { _browserTermWs.close(); } catch (e) {} _browserTermWs = null; }
  if (_browserTermResizeObs) { _browserTermResizeObs.disconnect(); _browserTermResizeObs = null; }
  if (_browserTerm) { _browserTerm.dispose(); _browserTerm = null; }
  container.innerHTML = "";

  // Create terminal
  const _isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const term = new window.Terminal({
    cursorBlink: true,
    scrollback: 5000,
    fontSize: 12.5,
    lineHeight: 1.2,
    fontFamily: "'MesloLGS Nerd Font Mono', 'Andale Mono', Menlo, monospace",
    // In light mode, CLI tools emit truecolor Monokai sequences (#f8f8f2 etc.)
    // that are invisible on a light background. minimumContrastRatio forces
    // xterm.js to darken any foreground that doesn't meet the ratio.
    minimumContrastRatio: _isDark ? 1 : 4.5,
    theme: _isDark
      ? { background: "rgba(12,10,20,0.40)", foreground: "#e0dce8", cursor: "#8b7cf6",
          selectionBackground: "rgba(139,124,246,0.15)",
          black: "#0c0a14", red: "#6b2424", green: "#2e5c3f", yellow: "#6b5010",
          blue: "#7da0d4", magenta: "#b89cf0", cyan: "#60b8b0", white: "#e0dce8",
          brightBlack: "#6a6280", brightRed: "#e05555", brightGreen: "#5eae76",
          brightYellow: "#e8c06a", brightBlue: "#90b8e8", brightMagenta: "#d0b0f8",
          brightCyan: "#70d0c8", brightWhite: "#f0ecf8" }
      // Light-mode palette is pastel so colors work as BACKGROUND (CLI diff
       // tools emit `bright red` bg for `-` lines, `bright green` for `+`,
       // Claude's input bar uses a colored bg band). minimumContrastRatio
       // below auto-darkens the same colors when they're used as FOREGROUND.
      : { background: "#f8f9fb", foreground: "#0a0a14", cursor: "#6356d4",
          selectionBackground: "rgba(99,86,212,0.12)",
          black: "#0a0a14", red: "#fca5a5", green: "#86efac", yellow: "#fde68a",
          blue: "#93c5fd", magenta: "#c4b5fd", cyan: "#67e8f9", white: "#f8f9fb",
          brightBlack: "#6b7280", brightRed: "#fecaca", brightGreen: "#bbf7d0",
          brightYellow: "#fef3c7", brightBlue: "#dbeafe", brightMagenta: "#e9d5ff",
          brightCyan: "#cffafe", brightWhite: "#ffffff" },
    padding: 32,
  });

  const FitAddonClass = (window.FitAddon && window.FitAddon.FitAddon) || window.FitAddon;
  const fitAddon = new FitAddonClass();
  term.loadAddon(fitAddon);
  term.open(container);
  _browserTerm = term;
  _browserTermFit = fitAddon;

  // Fit after layout settles (double rAF to let CSS/flex finalize)
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

  // Fit with right margin: reduce columns to account for 24px padding
  const fitWithMargin = () => {
    fitAddon.fit();
    // Reduce columns by ~15 to account for 24px right padding and prevent clipping
    term.resize(Math.max(term.cols - 15, 80), term.rows);
  };

  fitWithMargin();

  // Connect WebSocket
  const wsUrl = `ws://127.0.0.1:${serverPort}/ws/terminal/${encodeURIComponent(tmuxName)}`;
  const tws = new WebSocket(wsUrl);
  tws.binaryType = "arraybuffer";
  _browserTermWs = tws;

  tws.onopen = () => {
    // Re-fit and send final dimensions — layout is fully settled now
    fitWithMargin();
    tws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    setTimeout(() => term.focus(), 50);
  };

    tws.onmessage = (evt) => {
      if (evt.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(evt.data));
      } else {
        term.write(evt.data);
      }
    };

    tws.onclose = () => {
      term.write("\r\n\x1b[90m[session disconnected]\x1b[0m\r\n");
    };

    // Forward keystrokes to PTY
    term.onData((data) => {
      if (tws.readyState === WebSocket.OPEN) tws.send(data);
    });

    // Forward resize
    term.onResize(({ cols, rows }) => {
      if (tws.readyState === WebSocket.OPEN) {
        tws.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    // Re-fit on container resize (debounced to avoid resize storms)
    let _bResizeTimer = null;
    _browserTermResizeObs = new ResizeObserver(() => {
      try { fitWithMargin(); } catch (e) {}
      clearTimeout(_bResizeTimer);
      _bResizeTimer = setTimeout(() => {
        try {
          fitWithMargin();
          if (tws.readyState === WebSocket.OPEN) {
            tws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
          }
        } catch (e) {}
      }, 150);
    });
    _browserTermResizeObs.observe(container);
}

function _loadXtermScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

function showSessionConnecting() {
  const emptyEl = document.getElementById("session-empty");
  const xtermEl = document.getElementById("xterm-container");
  if (emptyEl) {
    emptyEl.innerHTML = '<div class="empty-icon spinner-icon"></div><h2>Connecting\u2026</h2><p>Starting Claude session.</p>';
    emptyEl.classList.remove("hidden");
  }
  if (xtermEl) xtermEl.classList.add("hidden");
}

function showSessionEmpty() {
  const xtermEl = document.getElementById("xterm-container");
  const emptyEl = document.getElementById("session-empty");
  if (emptyEl) {
    emptyEl.innerHTML = '<div class="empty-icon">&#x1F4BB;</div><h2>No active session</h2><p>Launch an experiment to see the live Claude session here.</p>';
  }
  if (emptyEl) emptyEl.classList.remove("hidden");
  if (xtermEl) xtermEl.classList.add("hidden");
  detachTerminal();
}

// Receive PTY data and pipe to xterm
if (window.nicolas) {
  window.nicolas.onTerminalData(({ projectId, data }) => {
    if (projectId === currentTerminalProject && window.xtermBridge) {
      window.xtermBridge.write(data);
    }
  });

  window.nicolas.onTerminalExit(({ projectId }) => {
    if (projectId === currentTerminalProject && window.xtermBridge) {
      window.xtermBridge.write("\r\n\x1b[2m--- Session ended ---\x1b[0m\r\n");
      currentTerminalProject = null;
      currentTerminalSession = null;
    }
  });
}

// Centralized terminal resize — single gatekeeper for all resize triggers.
// Fits xterm immediately (cheap), then debounces the PTY resize IPC (150ms)
// to avoid tmux redraw storms from overlapping callers.
let _resizeTimer = null;
let _lastSentCols = 0;
let _lastSentRows = 0;
function resizeTerminal() {
  if (!window.xtermBridge || !terminalInitialized || _termTransitioning) return;
  if (window._attachSettlingUntil && Date.now() < window._attachSettlingUntil) return;
  window.xtermBridge.fit();
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => {
    if (!currentTerminalProject || !window.nicolas) return;
    if (window._attachSettlingUntil && Date.now() < window._attachSettlingUntil) return;
    window.xtermBridge.fit();
    const dims = window.xtermBridge.getDimensions();
    if (dims.cols < 10 || dims.rows < 3) return;
    if (dims.cols === _lastSentCols && dims.rows === _lastSentRows) return;
    _lastSentCols = dims.cols;
    _lastSentRows = dims.rows;
    window.nicolas.terminalResize(currentTerminalProject, dims.cols, dims.rows);
  }, 150);
}

const xtermContainerEl = document.getElementById("xterm-container");
if (xtermContainerEl) {
  new ResizeObserver(() => resizeTerminal()).observe(xtermContainerEl);
}

// Keyboard shortcuts
document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;

  if (mod && e.key === "k") {
    e.preventDefault();
    const overlay = document.getElementById("resource-search-overlay");
    if (overlay && !overlay.classList.contains("hidden")) closeResourceSearch();
    else openResourceSearch();
  }
  // Cmd+Shift+T: open terminal transcript overlay for selecting long text
  if (mod && e.shiftKey && (e.key === "T" || e.key === "t")) {
    e.preventDefault();
    if (typeof window.isTranscriptOverlayOpen === "function"
        && window.isTranscriptOverlayOpen()) {
      window.closeTranscriptOverlay();
    } else if (typeof window.openTranscriptOverlay === "function") {
      window.openTranscriptOverlay();
    }
  }
  if (mod && !e.shiftKey && e.key === "b") {
    e.preventDefault();
    togglePane("sidebar-left");
  }
  if (mod && !e.shiftKey && e.key === ",") {
    e.preventDefault();
    if (typeof openPreferences === "function") openPreferences();
  }
  if (mod && !e.shiftKey && e.key === "r") {
    e.preventDefault();
    reloadCurrentProject();
    fetchPapersData();
  }
  // Cmd+1-9 to switch sidebar views (positional — matches activity bar order)
  // Press again on the active view to collapse the sidebar.
  if (mod && !e.shiftKey && e.key >= "1" && e.key <= "9") {
    const sidebarBtns = [...document.querySelectorAll('.activity-btn[data-pane="sidebar-left"][data-sidebar-view]')];
    const idx = parseInt(e.key) - 1;
    if (idx < sidebarBtns.length) {
      e.preventDefault();
      const view = sidebarBtns[idx].dataset.sidebarView;
      const sidebar = document.getElementById("sidebar-left");
      const isOpen = sidebar && !sidebar.classList.contains("collapsed");
      const activeBtn = document.querySelector('.activity-btn[data-pane="sidebar-left"].active');
      const alreadyActive = isOpen && activeBtn?.dataset.sidebarView === view;
      if (alreadyActive) {
        togglePane("sidebar-left");
      } else {
        if (!isOpen) togglePane("sidebar-left");
        if (typeof switchSidebarView === "function") switchSidebarView(view);
        document.querySelectorAll('.activity-btn[data-pane="sidebar-left"]').forEach((b) => {
          b.classList.toggle("active", b.dataset.sidebarView === view);
        });
      }
    }
  }
  // Cmd+Shift+1/2/3/4 to switch experiment detail tabs
  if (mod && e.shiftKey && e.key >= "1" && e.key <= "4") {
    e.preventDefault();
    const tabs = ["control-panel", "session", "results", "prompt-editor"];
    switchEditorTab(tabs[parseInt(e.key) - 1]);
  }
  // Escape: close search, stop generation, or deselect
  if (e.key === "Escape" && !e.metaKey && !e.ctrlKey) {
    if (typeof window.isTranscriptOverlayOpen === "function"
        && window.isTranscriptOverlayOpen()) {
      window.closeTranscriptOverlay(); return;
    }
    const searchOverlay = document.getElementById("resource-search-overlay");
    if (searchOverlay && !searchOverlay.classList.contains("hidden")) {
      closeResourceSearch(); return;
    }
    if (!(document.getElementById("settings-overlay")?.hidden)) return;
    // Close shortcuts overlay first if open
    const shortcutsOverlay = document.getElementById("shortcuts-overlay");
    if (shortcutsOverlay && !shortcutsOverlay.classList.contains("hidden")) {
      closeShortcutsOverlay(); return;
    }
    if (isStreaming) {
      e.preventDefault();
      stopGeneration();
    } else if (currentProjectId || currentPaperKey) {
      e.preventDefault();
      deselectAll();
    }
  }
  // Cmd+/ to toggle keyboard shortcuts
  if (mod && e.key === "/") {
    e.preventDefault();
    const sOverlay = document.getElementById("shortcuts-overlay");
    if (sOverlay && !sOverlay.classList.contains("hidden")) closeShortcutsOverlay();
    else openShortcutsOverlay();
  }
});

/* ───── Keyboard shortcuts overlay (Cmd+/) ───── */

function openShortcutsOverlay() {
  let overlay = document.getElementById("shortcuts-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "shortcuts-overlay";
    overlay.className = "shortcuts-overlay";
    // Build sidebar view shortcuts dynamically from activity bar
    const sidebarBtns = [...document.querySelectorAll('.activity-btn[data-pane="sidebar-left"][data-sidebar-view]')];
    const viewRows = sidebarBtns.map((btn, i) =>
      `<tr><td><kbd>\u2318${i + 1}</kbd></td><td>${btn.getAttribute("aria-label") || btn.dataset.sidebarView}</td></tr>`
    ).join("");
    overlay.innerHTML = `
      <div class="shortcuts-box">
        <div class="shortcuts-header">
          <span class="shortcuts-title">Keyboard Shortcuts</span>
        </div>
        <div class="shortcuts-body">
          <div class="shortcuts-col">
            <h4>Navigation</h4>
            <table>${viewRows}
              <tr><td><kbd>\u2318B</kbd></td><td>Toggle sidebar</td></tr>
            </table>
          </div>
          <div class="shortcuts-col">
            <h4>Actions</h4>
            <table>
              <tr><td><kbd>\u2318K</kbd></td><td>Command palette</td></tr>
              <tr><td><kbd>\u2318N</kbd></td><td>New thread</td></tr>
              <tr><td><kbd>\u2318R</kbd></td><td>Refresh data</td></tr>
              <tr><td><kbd>\u2318\u21E7T</kbd></td><td>Copy terminal transcript</td></tr>
              <tr><td><kbd>\u2318,</kbd></td><td>Settings</td></tr>
              <tr><td><kbd>Esc</kbd></td><td>Dismiss / cancel</td></tr>
            </table>
            <h4>Experiment Detail</h4>
            <table>
              <tr><td><kbd>\u2318\u21E71</kbd> \u2013 <kbd>\u2318\u21E74</kbd></td><td>Switch tabs</td></tr>
            </table>
          </div>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeShortcutsOverlay();
    });
  }
  overlay.classList.remove("hidden");
}

function closeShortcutsOverlay() {
  const overlay = document.getElementById("shortcuts-overlay");
  if (overlay) overlay.classList.add("hidden");
}

/* ───── Resource search (Cmd+K) ───── */

// When set, clicking a paper/experiment/project fires this callback instead
// of navigating. Cleared on close.
let _onAttach = null;

function openResourceSearch(opts) {
  _onAttach = (opts && opts.onAttach) || null;
  let overlay = document.getElementById("resource-search-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "resource-search-overlay";
    overlay.className = "resource-search-overlay";
    overlay.innerHTML = `
      <div class="resource-search-box">
        <input id="resource-search-input" type="text" placeholder="Ask Nicolas, or jump to anything\u2026  \u2318/ for shortcuts"
               autocomplete="off" spellcheck="false">
        <div id="resource-search-results" class="resource-search-results"></div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeResourceSearch();
    });
    document.getElementById("resource-search-input").addEventListener("input", (e) => {
      renderResourceSearchResults(e.target.value.trim());
    });
    document.getElementById("resource-search-input").addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeResourceSearch(); e.stopPropagation(); return; }
      if (e.key === "Enter") {
        e.preventDefault();
        const active = document.querySelector(".resource-search-item.active")
          || document.querySelector(".resource-search-item");
        if (active) { active.click(); return; }
        // No item rendered but text exists — fall through to Ask Nicolas
        const q = document.getElementById("resource-search-input").value.trim();
        if (q) { askNicolasFromPalette(q); }
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const items = [...document.querySelectorAll(".resource-search-item")];
        if (!items.length) return;
        const active = document.querySelector(".resource-search-item.active");
        let idx = active ? items.indexOf(active) : -1;
        if (active) active.classList.remove("active");
        idx = e.key === "ArrowDown" ? Math.min(idx + 1, items.length - 1) : Math.max(idx - 1, 0);
        items[idx].classList.add("active");
        items[idx].scrollIntoView({ block: "nearest" });
      }
    });
  }
  overlay.classList.remove("hidden");
  const input = document.getElementById("resource-search-input");
  input.value = "";
  input.placeholder = _onAttach
    ? "Add context \u2014 search papers, experiments, projects\u2026"
    : "Ask Nicolas, or jump to anything\u2026  \u2318/ for shortcuts";
  input.focus();
  renderResourceSearchResults("");
}

// Send a prompt to Nicolas from anywhere in the app: surfaces the main-
// window chat, seeds the input, and triggers sendMessage(). Used by the
// command palette "Ask Nicolas" row and by in-context "Ask Nicolas"
// affordances (new experiment button, etc.).
function askNicolasFromPalette(query) {
  closeResourceSearch();
  if (typeof showNicolasMain === "function") {
    showNicolasMain();
  }
  if (typeof inputEl !== "undefined" && inputEl && typeof sendMessage === "function") {
    inputEl.value = query;
    sendMessage();
  }
}

function closeResourceSearch() {
  const overlay = document.getElementById("resource-search-overlay");
  if (overlay) overlay.classList.add("hidden");
  _onAttach = null;
}

function renderResourceSearchResults(query) {
  const resultsEl = document.getElementById("resource-search-results");
  if (!resultsEl) return;

  const q = query.toLowerCase();
  const sections = [];

  // Word-based fuzzy match: every word in the query must appear in the text.
  function paletteMatch(text, q) {
    if (!q) return true;
    const t = (text || "").toLowerCase();
    return q.split(/\s+/).filter(Boolean).every((w) => t.includes(w));
  }

  // Ask Nicolas — always first when the user has typed something.
  // Whole query preserved (not just the .toLowerCase() version used for
  // resource matching) so Nicolas sees exactly what the user typed.
  if (query) {
    const previewRaw = query.length > 140 ? query.slice(0, 140) + "\u2026" : query;
    const preview = escapeHtml(previewRaw);
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">Ask Nicolas</div>
      <div class="resource-search-item resource-search-nicolas active" data-type="nicolas">
        <span class="resource-search-icon">\u2697\uFE0F</span>
        <span class="resource-search-name">${preview}</span>
        <span class="resource-search-meta">\u21B5 send</span>
      </div>
    </div>`);
  } else {
    // Zero state: offer the most useful Nicolas-driven entry points so
    // first-time users discover what he can do without typing anything.
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">Ask Nicolas</div>
      <div class="resource-search-item resource-search-nicolas active" data-type="nicolas-preset"
           data-query="Let's start a new experiment. Walk me through it step by step \u2014 ask me what I want to try, then propose a clear name, goal, primary metric with direction, a baseline, and any constraints. When ready, scaffold it with init_experiment and pause for me to review PROMPT.md before launching.">
        <span class="resource-search-icon">\uD83E\uDDEA</span>
        <span class="resource-search-name">Start a new experiment</span>
        <span class="resource-search-meta">step by step</span>
      </div>
      <div class="resource-search-item resource-search-nicolas" data-type="nicolas-preset"
           data-query="What should I work on right now? Look at my active experiments, running sessions, and recent papers, then give me one concrete next step I can take in under 15 minutes.">
        <span class="resource-search-icon">\u2728</span>
        <span class="resource-search-name">What should I work on?</span>
        <span class="resource-search-meta">today's briefing</span>
      </div>
      <div class="resource-search-item resource-search-nicolas" data-type="nicolas-preset"
           data-query="What's in my reading queue right now? Show me the top 5 and pick the one I should read next based on my active experiments.">
        <span class="resource-search-icon">\uD83D\uDCDA</span>
        <span class="resource-search-name">Review my reading queue</span>
        <span class="resource-search-meta">next to read</span>
      </div>
      <div class="resource-search-item resource-search-nicolas" data-type="nicolas-preset"
           data-query="Summarize the status of every running experiment session in one line each. Call out anything that looks stuck or off-track.">
        <span class="resource-search-icon">\uD83D\uDD2C</span>
        <span class="resource-search-name">Status of running experiments</span>
        <span class="resource-search-meta">live check</span>
      </div>
    </div>`);
  }

  // Papers — search by title/authors, or show 4 most-recent in empty state.
  const allPapers = (typeof cachedPapers !== "undefined" ? cachedPapers : []);
  const paperResults = q
    ? allPapers.filter((p) =>
        paletteMatch(p.title, q) ||
        (p.authors || []).some((a) => paletteMatch(a, q))
      ).slice(0, 6)
    : allPapers.slice(0, 4);
  if (paperResults.length) {
    const label = q ? "Papers" : "Recent Papers";
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">${label}</div>
      ${paperResults.map((p) => {
        const metaParts = [];
        if (Array.isArray(p.authors) && p.authors.length) {
          const first = String(p.authors[0] || "").trim();
          const lastName = first.includes(" ") ? first.split(" ").pop() : first;
          if (lastName) metaParts.push(p.authors.length > 1 ? `${lastName} et al.` : lastName);
        }
        if (p.publication_date) {
          const yr = String(p.publication_date).match(/(19|20)\d{2}/);
          if (yr) metaParts.push(yr[0]);
        }
        const meta = metaParts.join(" \u00B7 ");
        return `<div class="resource-search-item" data-type="paper" data-id="${escapeHtml(p.key)}">
          <span class="resource-search-icon">\uD83D\uDCC4</span>
          <span class="resource-search-name">${escapeHtml(p.title || p.key)}</span>
          ${meta ? `<span class="resource-search-meta">${escapeHtml(meta)}</span>` : ""}
        </div>`;
      }).join("")}
    </div>`);
  }

  // Agents
  const agents = (typeof _agents !== "undefined" ? _agents : [])
    .filter((a) => !q || paletteMatch(a.name, q))
    .slice(0, 8);
  if (agents.length) {
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">Agents</div>
      ${agents.map((a) => `<div class="resource-search-item" data-type="agent" data-id="${a.id}">
        <span class="resource-search-icon">${a.session_status === "running" ? "&#x25CF;" : "&#x25CB;"}</span>
        <span class="resource-search-name">${escapeHtml(a.name)}</span>
        ${a.session_status === "running" ? '<span class="resource-search-badge">live</span>' : ""}
      </div>`).join("")}
    </div>`);
  }

  // Experiments
  const experiments = (typeof cachedProjects !== "undefined" ? cachedProjects : [])
    .filter((p) => !q || paletteMatch(p.name, q) || paletteMatch(p.goal, q))
    .slice(0, 8);
  if (experiments.length) {
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">Experiments</div>
      ${experiments.map((p) => `<div class="resource-search-item" data-type="experiment" data-id="${p.id}">
        <span class="resource-search-icon">${p.active_sessions > 0 ? "&#x25CF;" : "&#x25CB;"}</span>
        <span class="resource-search-name">${escapeHtml(p.name)}</span>
        ${p.total_runs ? `<span class="resource-search-meta">${p.total_runs} runs</span>` : ""}
      </div>`).join("")}
    </div>`);
  }

  // Projects (workspaces)
  const workspaces = (typeof _workspaces !== "undefined" ? _workspaces : [])
    .filter((w) => !q || paletteMatch(w.name, q) || paletteMatch(w.description, q))
    .slice(0, 8);
  if (workspaces.length) {
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">Workspaces</div>
      ${workspaces.map((w) => `<div class="resource-search-item" data-type="project" data-id="${w.id}">
        <span class="resource-search-icon">&#x25CB;</span>
        <span class="resource-search-name">${escapeHtml(w.name)}</span>
        ${w.repos?.length ? `<span class="resource-search-meta">${w.repos.length} repos</span>` : ""}
      </div>`).join("")}
    </div>`);
  }

  // Actions — always present, filtered when a query is active.
  const PALETTE_ACTIONS = [
    { id: "new-experiment", icon: "\uD83E\uDDEA", name: "New experiment", meta: "start fresh" },
    { id: "add-paper",      icon: "\uD83D\uDCE5", name: "Add paper",      meta: "URL or title" },
    { id: "shortcuts",      icon: "\u2328\uFE0F",  name: "Keyboard shortcuts", meta: "\u2318/" },
    { id: "settings",       icon: "\u2699\uFE0F",  name: "Settings",      meta: "" },
  ];
  const actionResults = q
    ? PALETTE_ACTIONS.filter((a) => paletteMatch(a.name, q))
    : PALETTE_ACTIONS;
  if (actionResults.length) {
    sections.push(`<div class="resource-search-section">
      <div class="resource-search-label">Actions</div>
      ${actionResults.map((a) => `<div class="resource-search-item" data-type="action" data-id="${a.id}">
        <span class="resource-search-icon">${a.icon}</span>
        <span class="resource-search-name">${escapeHtml(a.name)}</span>
        ${a.meta ? `<span class="resource-search-meta">${escapeHtml(a.meta)}</span>` : ""}
      </div>`).join("")}
    </div>`);
  }

  if (!sections.length) {
    resultsEl.innerHTML = q
      ? '<div class="resource-search-empty">No results</div>'
      : '<div class="resource-search-empty">Type to search</div>';
  } else {
    resultsEl.innerHTML = sections.join("");
  }

  // Click handlers
  resultsEl.querySelectorAll(".resource-search-item").forEach((item) => {
    item.addEventListener("click", () => {
      const type = item.dataset.type;
      const id = item.dataset.id;
      if (type === "nicolas") {
        const inputVal = document.getElementById("resource-search-input")?.value?.trim();
        if (inputVal) askNicolasFromPalette(inputVal);
        return;
      }
      if (type === "nicolas-preset") {
        const preset = item.dataset.query || "";
        if (preset) askNicolasFromPalette(preset);
        return;
      }
      if (type === "action") {
        closeResourceSearch();
        if (id === "new-experiment" && typeof showNewExperimentWizard === "function") {
          showNewExperimentWizard();
        } else if (id === "add-paper") {
          askNicolasFromPalette("Add a paper to my queue");
        } else if (id === "shortcuts" && typeof openShortcutsOverlay === "function") {
          openShortcutsOverlay();
        } else if (id === "settings" && typeof openSettings === "function") {
          openSettings();
        }
        return;
      }
      // Attach mode: fire callback for attachable types instead of navigating
      if (_onAttach && (type === "paper" || type === "experiment" || type === "project")) {
        const label = item.querySelector(".resource-search-name")?.textContent || id;
        _onAttach({ type, id, label });
        closeResourceSearch();
        return;
      }
      closeResourceSearch();
      if (type === "paper" && typeof selectPaper === "function") {
        const sidebar = document.getElementById("sidebar-left");
        if (sidebar?.classList.contains("collapsed")) togglePane("sidebar-left");
        switchSidebarView("papers");
        selectPaper(id);
      } else if (type === "agent" && typeof selectAgent === "function") {
        const sidebar = document.getElementById("sidebar-left");
        if (sidebar?.classList.contains("collapsed")) togglePane("sidebar-left");
        switchSidebarView("agents");
        selectAgent(id);
      } else if (type === "experiment" && typeof selectProject === "function") {
        const sidebar = document.getElementById("sidebar-left");
        if (sidebar?.classList.contains("collapsed")) togglePane("sidebar-left");
        switchSidebarView("experiments");
        selectProject(id);
      } else if (type === "project" && typeof selectWorkspace === "function") {
        const sidebar = document.getElementById("sidebar-left");
        if (sidebar?.classList.contains("collapsed")) togglePane("sidebar-left");
        switchSidebarView("workspaces");
        selectWorkspace(id);
      }
    });
  });
}

/* ───── Resize handles ───── */

function initResize(handleId, target, prop, direction) {
  const handle = document.getElementById(handleId);
  if (!handle || !target) return;

  let startPos = 0;
  let startSize = 0;

  function onMouseDown(e) {
    e.preventDefault();
    startPos = direction === "horizontal" ? e.clientY : e.clientX;
    startSize = direction === "horizontal" ? target.offsetHeight : target.offsetWidth;
    handle.classList.add("dragging");
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    document.body.style.cursor = direction === "horizontal" ? "row-resize" : "col-resize";
    document.body.style.userSelect = "none";
  }

  function onMouseMove(e) {
    const delta = direction === "horizontal"
      ? startPos - e.clientY // inverted: drag up = bigger
      : (prop === "right" ? startPos - e.clientX : e.clientX - startPos);
    const maxSize = direction === "horizontal" ? window.innerHeight * 0.6 : window.innerWidth * 0.4;
    const newSize = Math.max(120, Math.min(startSize + delta, maxSize));
    target.style[direction === "horizontal" ? "height" : "width"] = newSize + "px";
  }

  function onMouseUp() {
    handle.classList.remove("dragging");
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    saveLayoutState();
  }

  handle.addEventListener("mousedown", onMouseDown);

  // Double-click to collapse
  handle.addEventListener("dblclick", () => {
    const paneMap = { "resize-left": "sidebar-left", "resize-right": "sidebar-right" };
    const pane = paneMap[handleId];
    if (pane) togglePane(pane);
  });
}

initResize("resize-left", sidebarLeft, "left", "vertical");
initResize("resize-right", sidebarRight, "right", "vertical");

/* ───── Layout state persistence ───── */

function saveLayoutState() {
  try {
    const rightHasContent = !!(sidebarRight && sidebarRight.children.length > 0);
    const state = {
      leftCollapsed: sidebarLeft?.classList.contains("collapsed") || false,
      leftWidth: sidebarLeft?.offsetWidth,
    };
    if (rightHasContent) {
      state.rightCollapsed = sidebarRight.classList.contains("collapsed") || false;
      state.rightWidth = sidebarRight.offsetWidth;
    }
    localStorage.setItem("distillate-layout", JSON.stringify(state));
  } catch {}
}

function restoreLayoutState() {
  try {
    // Left sidebar starts collapsed in HTML so the Nicolas home is centered
    // with zero FOUC. We leave it collapsed on launch — the saved width is
    // restored so it's the right size when the user opens it.
    const state = JSON.parse(localStorage.getItem("distillate-layout"));
    if (!state) return;
    if (sidebarRight && sidebarRight.children.length > 0) {
      if (state.rightCollapsed) { sidebarRight.classList.add("collapsed"); document.querySelector('.activity-btn[data-pane="sidebar-right"]')?.classList.remove("active"); }
      else { sidebarRight.classList.remove("collapsed"); document.querySelector('.activity-btn[data-pane="sidebar-right"]')?.classList.add("active"); }
      if (state.rightWidth) sidebarRight.style.width = state.rightWidth + "px";
    }
    if (state.leftWidth && sidebarLeft) sidebarLeft.style.width = state.leftWidth + "px";
    // One-shot migration: drop retired bottom-panel keys from existing state.
    if ("bottomCollapsed" in state || "bottomHeight" in state) {
      delete state.bottomCollapsed;
      delete state.bottomHeight;
      localStorage.setItem("distillate-layout", JSON.stringify(state));
    }
  } catch {}
}

// Restore layout from previous session (sizes and collapsed state)
restoreLayoutState();

/* ───── Cleanup ───── */

window.addEventListener("beforeunload", () => {
  stopExperimentSSE();
  detachTerminal();
});
