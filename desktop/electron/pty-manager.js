const path = require("path");
const pty = require("node-pty");

// Electron apps launched from Finder get a minimal PATH — augment it
// so tmux (typically in /usr/local/bin or /opt/homebrew/bin) is found.
function _augmentedEnv() {
  const extraPaths = [
    "/usr/local/bin",
    "/opt/homebrew/bin",
    path.join(process.env.HOME || "", ".local", "bin"),
  ];
  const currentPath = process.env.PATH || "/usr/bin:/bin";
  const fullPath = [...new Set([...extraPaths, ...currentPath.split(":")])].join(":");
  return { ...process.env, TERM: "xterm-256color", PATH: fullPath };
}

class PtyManager {
  /** @type {Map<string, {process: any, sessionName: string}>} */
  #terminals = new Map();

  /**
   * Attach to a tmux session via a PTY.
   * @returns {import("node-pty").IPty} The spawned process.
   */
  attach(projectId, sessionName, cols, rows) {
    if (this.#terminals.has(projectId)) this.detach(projectId);

    const finalCols = Math.max(cols || 120, 80);
    const finalRows = Math.max(rows || 30, 24);
    const env = _augmentedEnv();
    console.log(`[pty] spawning: tmux attach-session -t ${sessionName} (${finalCols}x${finalRows})`);
    console.log(`[pty] PATH includes /usr/local/bin: ${env.PATH.includes("/usr/local/bin")}`);

    const proc = pty.spawn("tmux", ["attach-session", "-t", sessionName], {
      name: "xterm-256color",
      cols: finalCols,
      rows: finalRows,
      cwd: process.env.HOME,
      env,
    });

    console.log(`[pty] spawned PID: ${proc.pid}`);
    this.#terminals.set(projectId, { process: proc, sessionName });
    return proc;
  }

  /**
   * Detach from a project's PTY (kills the PTY process).
   */
  detach(projectId) {
    const entry = this.#terminals.get(projectId);
    if (!entry) return;
    try {
      entry.process.kill();
    } catch {
      // Already dead
    }
    this.#terminals.delete(projectId);
  }

  /**
   * Forward keystrokes to the PTY stdin.
   */
  write(projectId, data) {
    const entry = this.#terminals.get(projectId);
    if (!entry) {
      console.warn(`[pty] write DROPPED — no terminal for project=${projectId} (known: ${[...this.#terminals.keys()].join(", ")})`);
      return;
    }
    entry.process.write(data);
  }

  /**
   * Resize the PTY.
   */
  resize(projectId, cols, rows) {
    const entry = this.#terminals.get(projectId);
    if (entry) {
      try {
        entry.process.resize(cols, rows);
      } catch {
        // Ignore resize errors on dead processes
      }
    }
  }

  /**
   * Check if a project is currently attached.
   */
  isAttached(projectId) {
    return this.#terminals.has(projectId);
  }

  /**
   * Clean up all PTY processes (call on app quit).
   */
  cleanup() {
    for (const [id] of this.#terminals) {
      this.detach(id);
    }
  }
}

module.exports = { PtyManager };
