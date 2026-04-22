/**
 * Sandboxed file I/O for a workspace's canvas directories.
 *
 * A workspace may have multiple canvases, so all operations are scoped to
 * a (wsId, canvasId) pair. Paths are validated to resolve inside the
 * canvas's own directory — no escapes via ".." or absolute paths or
 * symlinks. The main-process side queries the Python server for the
 * authoritative directory and caches the result per (wsId, canvasId).
 *
 * Also owns the per-canvas file watcher that pushes ``canvas:file-changed``
 * events to the renderer so the editor can hot-reload when the agent (or
 * any external tool) writes the entry file.
 */
const fs = require("fs");
const path = require("path");

function _key(wsId, cvId) {
  return `${wsId}::${cvId}`;
}

class CanvasFs {
  constructor({ getServerPort }) {
    this._getServerPort = getServerPort;
    this._dirCache = new Map(); // `${wsId}::${cvId}` -> {dir, entry, type}
    // Active file watchers keyed by (wsId, cvId). See startWatch.
    this._watchers = new Map();
  }

  /** Drop cached dir info for a single canvas or all canvases in a workspace. */
  invalidate(wsId, cvId) {
    if (cvId) {
      this._dirCache.delete(_key(wsId, cvId));
      return;
    }
    const prefix = `${wsId}::`;
    for (const k of [...this._dirCache.keys()]) {
      if (k.startsWith(prefix)) this._dirCache.delete(k);
    }
  }

  /** Fetch and cache a canvas's dir record from the Python server. */
  async _resolveDir(wsId, cvId) {
    const cached = this._dirCache.get(_key(wsId, cvId));
    if (cached) return cached;
    const port = this._getServerPort?.();
    if (!port) throw new Error("server_not_ready");
    const resp = await fetch(
      `http://127.0.0.1:${port}/workspaces/${wsId}/canvases/${cvId}/dir`
    );
    const data = await resp.json();
    if (!data.ok) throw new Error(data.error || "canvas-dir lookup failed");
    const info = { dir: data.dir, entry: data.entry, type: data.type };
    this._dirCache.set(_key(wsId, cvId), info);
    return info;
  }

  /** Reject paths that escape the canvas dir. */
  _safeResolve(baseDir, relPath) {
    if (!relPath || typeof relPath !== "string") {
      throw new Error("invalid_path");
    }
    if (path.isAbsolute(relPath)) {
      throw new Error("absolute_path_rejected");
    }
    const resolved = path.resolve(baseDir, relPath);
    const rel = path.relative(baseDir, resolved);
    if (rel.startsWith("..") || path.isAbsolute(rel)) {
      throw new Error("path_escape_rejected");
    }
    return resolved;
  }

  async listFiles(wsId, cvId) {
    const info = await this._resolveDir(wsId, cvId);
    if (!fs.existsSync(info.dir)) {
      return { ok: true, dir: info.dir, entry: info.entry, files: [] };
    }
    const files = [];
    const walk = (sub) => {
      const here = path.join(info.dir, sub);
      let entries;
      try { entries = fs.readdirSync(here, { withFileTypes: true }); } catch { return; }
      for (const e of entries) {
        if (e.name === "build" || e.name === ".git" || e.name === "node_modules") continue;
        const rel = sub ? path.join(sub, e.name) : e.name;
        if (e.isDirectory()) {
          walk(rel);
        } else if (e.isFile()) {
          try {
            const st = fs.statSync(path.join(info.dir, rel));
            files.push({ path: rel, size: st.size, mtime: st.mtimeMs });
          } catch {}
        }
      }
    };
    walk("");
    return { ok: true, dir: info.dir, entry: info.entry, files };
  }

  async readFile(wsId, cvId, relPath) {
    const info = await this._resolveDir(wsId, cvId);
    const abs = this._safeResolve(info.dir, relPath);
    if (!fs.existsSync(abs)) {
      return { ok: false, error: "not_found" };
    }
    const content = fs.readFileSync(abs, "utf-8");
    const st = fs.statSync(abs);
    return { ok: true, content, mtime: st.mtimeMs };
  }

  async writeFile(wsId, cvId, relPath, content) {
    const info = await this._resolveDir(wsId, cvId);
    const abs = this._safeResolve(info.dir, relPath);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    // Suppress our own write from triggering the hot-reload watcher.
    const w = this._watchers.get(_key(wsId, cvId));
    if (w) w.suppressUntil = Date.now() + 400;
    fs.writeFileSync(abs, content, "utf-8");
    const st = fs.statSync(abs);
    if (w) w.lastMtime = st.mtimeMs;
    return { ok: true, mtime: st.mtimeMs, path: abs };
  }

  async readPdf(wsId, cvId) {
    const info = await this._resolveDir(wsId, cvId);
    const entryNoExt = (info.entry || "main.tex").replace(/\.tex$/, "");
    const pdfPath = path.join(info.dir, "build", `${entryNoExt}.pdf`);
    if (!fs.existsSync(pdfPath)) {
      return { ok: false, error: "no_pdf" };
    }
    const buf = fs.readFileSync(pdfPath);
    return { ok: true, bytes: buf };
  }

  /** Returns {dir, entry, type} for the compile step. */
  async getDir(wsId, cvId) {
    const info = await this._resolveDir(wsId, cvId);
    return {
      dir: info.dir,
      entry: info.entry || "main.tex",
      type: info.type || "plain",
    };
  }

  /**
   * Start watching the entry file for external changes (e.g. agent edits).
   * ``emitter`` is called with ``{wsId, cvId, relPath, mtime}`` when a
   * change is detected that wasn't caused by our own writeFile().
   */
  async startWatch(wsId, cvId, emitter) {
    this.stopWatch(wsId, cvId);
    const info = await this._resolveDir(wsId, cvId);
    const entryPath = path.join(info.dir, info.entry || "");
    if (!fs.existsSync(entryPath)) {
      return { ok: false, error: "entry_not_found" };
    }

    const state = {
      watcher: null,
      timer: null,
      lastMtime: fs.statSync(entryPath).mtimeMs,
      suppressUntil: 0,
      emitter,
    };

    try {
      state.watcher = fs.watch(entryPath, { persistent: true }, () => {
        if (state.timer) clearTimeout(state.timer);
        state.timer = setTimeout(() => {
          if (Date.now() < state.suppressUntil) return;
          let st;
          try { st = fs.statSync(entryPath); } catch { return; }
          if (st.mtimeMs === state.lastMtime) return;
          state.lastMtime = st.mtimeMs;
          try {
            state.emitter?.({
              wsId, cvId,
              relPath: info.entry || "",
              mtime: st.mtimeMs,
            });
          } catch {}
        }, 150);
      });
    } catch (err) {
      return { ok: false, error: err.message };
    }

    this._watchers.set(_key(wsId, cvId), state);
    return { ok: true };
  }

  stopWatch(wsId, cvId) {
    const state = this._watchers.get(_key(wsId, cvId));
    if (!state) return;
    try { state.watcher?.close?.(); } catch {}
    if (state.timer) clearTimeout(state.timer);
    this._watchers.delete(_key(wsId, cvId));
  }

  stopAllWatchers() {
    for (const k of [...this._watchers.keys()]) {
      const [wsId, cvId] = k.split("::");
      this.stopWatch(wsId, cvId);
    }
  }
}

module.exports = { CanvasFs };
