/**
 * Paste + drop handlers for the xterm terminal.
 *
 * SINGLE SOURCE OF TRUTH for getting clipboard / drag-drop content into
 * the terminal as either pasted text or file paths. Preload.js wires the
 * DOM events; this module does all the real work. Pure and unit-tested
 * — tests inject fakes for clipboard, fs, os, path.
 *
 * ── The two entry points ────────────────────────────────────────────────
 *
 *   1. handlePasteEvent(term, event, opts)
 *      Driven by the native "paste" DOM event on xterm's hidden textarea.
 *      Reads everything from event.clipboardData — NEVER from Electron's
 *      clipboard module — so it can't trigger any macOS permission prompt.
 *      This is the only path that handles images / file paths / uri-lists.
 *
 *   2. handleDrop(files, opts)
 *      Driven by the document-level drop listener. Resolves File objects
 *      to on-disk paths via webUtils.getPathForFile(), or materializes
 *      path-less blobs (screenshot thumbnails, webview drags) to tempfiles.
 *
 * Both routes share _materialize() so the temp-file naming + extension
 * detection have one implementation.
 *
 * The keydown handler in terminal-controller.js is intentionally a no-op
 * for Cmd+V. It just suppresses xterm.js's own `v` insertion and returns
 * false; Chromium then dispatches the native paste event on the focused
 * textarea, which runs handlePasteEvent. One user gesture → one paste.
 *
 * ── Source priority inside handlePasteEvent ─────────────────────────────
 *
 *   1. text/uri-list  → file:// URIs (Finder Cmd+C, other file managers).
 *                       Paste the real on-disk paths directly. Cheapest
 *                       path — no fs.writeFileSync, correct filename.
 *
 *   2. Files in clipboardData.items / .files
 *                     → Screenshots (Cmd+Shift+Ctrl+4 → Cmd+V), images
 *                       from viewers, drag-promise sources. Materialized
 *                       to /tmp/distillate-paste-<ts>-<rand><ext>.
 *                       PERMISSIVE: every kind="file" qualifies, regardless
 *                       of MIME type, because macOS/Chromium sometimes
 *                       surface files with empty or non-standard types.
 *
 *   3. text/plain     → Plain text. Chunked into 512-byte writes so large
 *                       pastes don't overwhelm the PTY input buffer.
 *
 * If none of the above yields data, returns { type: "none" } and the
 * terminal input stays empty.
 *
 * ── macOS privacy: NEVER call clipboard.readImage() ─────────────────────
 *
 * clipboard.readImage() / .has() / .read() / .availableFormats() can
 * trigger a per-app permission prompt on macOS 14+ if the clipboard
 * contains protected content (Apple Music, Photos, etc.). The DOM
 * ClipboardEvent path reads everything via event.clipboardData, which
 * Chromium populates from a user-activated paste gesture and does NOT
 * touch protected sources. The static-source audit in
 * test/no-permissions.test.js lints this rule; don't weaken it.
 *
 * ── Bulletproof contract ────────────────────────────────────────────────
 *
 * handlePasteEvent NEVER throws. It always returns a descriptor
 * { type: "uris"|"files"|"text"|"none", ... }. preload.js depends on
 * this — there is no try/catch in the wiring. If a file fails to
 * materialize, we fall through to the next priority level silently
 * (logged via opts.log when diagnostics are enabled).
 */

"use strict";

const TEXT_CHUNK = 512;

function createPasteHandlers(deps) {
  const { clipboard, fs, os, path, now } = deps;
  const nowFn = now || (() => Date.now());

  // ── helpers ──────────────────────────────────────────────────────────────

  function _quote(p) {
    return p.includes(" ") ? `"${p}"` : p;
  }

  function pastePathsIntoTerm(term, paths) {
    if (!paths || !paths.length) return false;
    term.paste(paths.map(_quote).join(" "));
    return true;
  }

  function _pasteText(term, text) {
    if (text.length <= TEXT_CHUNK) {
      term.paste(text);
      return;
    }
    let i = 0;
    const sendChunk = () => {
      if (i < text.length) {
        term.paste(text.slice(i, i + TEXT_CHUNK));
        i += TEXT_CHUNK;
        setTimeout(sendChunk, 5);
      }
    };
    sendChunk();
  }

  // Parse a text/uri-list blob (RFC 2483: \r\n separated, # comments,
  // file:// URIs). Returns an array of decoded local file paths.
  function _parseFileUris(uriListText) {
    if (!uriListText) return [];
    const out = [];
    for (const line of uriListText.split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      if (!t.startsWith("file://")) continue;
      try {
        const u = new URL(t);
        const decoded = decodeURIComponent(u.pathname);
        if (decoded) out.push(decoded);
      } catch {
        // bad URL — skip
      }
    }
    return out;
  }

  // Collect every File object the browser gives us in this ClipboardEvent.
  // Checks both the modern items API and the legacy files API; dedupes
  // the two because they often surface the same file via both paths.
  function _collectFiles(clipboardData) {
    const out = [];
    const seen = new Set();
    const addFile = (f) => {
      if (!f) return;
      const key = `${f.name || ""}|${f.size || 0}|${f.type || ""}|${f.lastModified || 0}`;
      if (seen.has(key)) return;
      seen.add(key);
      out.push(f);
    };
    const items = clipboardData && clipboardData.items;
    if (items && items.length) {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (!item || item.kind !== "file") continue;
        if (typeof item.getAsFile === "function") addFile(item.getAsFile());
      }
    }
    const files = clipboardData && clipboardData.files;
    if (files && files.length) {
      for (let i = 0; i < files.length; i++) addFile(files[i]);
    }
    return out;
  }

  // Extension resolver: prefer the original filename, fall back to MIME
  // subtype, fall back to .bin so the path is still a valid file name.
  function _extOf(file) {
    if (file && file.name) {
      const e = path.extname(file.name);
      if (e) return e;
    }
    if (file && file.type && typeof file.type === "string") {
      const m = /^[\w-]+\/([\w.+-]+)/.exec(file.type);
      if (m) return "." + m[1].replace("jpeg", "jpg");
    }
    return ".bin";
  }

  async function _materialize(file, readBlob, label) {
    const buf = await readBlob(file);
    if (!buf || buf.length === 0) {
      throw new Error("readBlob returned empty buffer");
    }
    const ext = _extOf(file);
    // Random suffix avoids same-millisecond collisions on multi-file pastes.
    const rnd = Math.random().toString(36).slice(2, 8);
    const tmp = path.join(
      os.tmpdir(),
      `distillate-${label}-${nowFn()}-${rnd}${ext}`,
    );
    fs.writeFileSync(tmp, buf);
    // Best-effort validation: check that the written file is non-empty.
    // fs.statSync may not exist in test mocks — don't gate on it.
    try {
      if (typeof fs.statSync === "function") {
        const stat = fs.statSync(tmp);
        if (stat.size === 0) {
          throw new Error("writeFileSync produced an empty file");
        }
      }
    } catch (e) {
      if (e.message === "writeFileSync produced an empty file") throw e;
      // statSync not available or path issue — file was written, proceed.
    }
    return tmp;
  }

  // ── public: paste from DOM ClipboardEvent ────────────────────────────────

  async function handlePasteEvent(term, event, opts) {
    const log = (opts && opts.log) || null;
    const emit = (...a) => { if (log) log(...a); };

    try {
      const data = event && event.clipboardData;
      if (!data) {
        emit("paste: no clipboardData");
        return { type: "none", reason: "no clipboardData" };
      }

      // 1. file:// URIs — Finder copy, file managers, uri-list sources.
      const uriList = typeof data.getData === "function"
        ? data.getData("text/uri-list")
        : "";
      const fileUris = _parseFileUris(uriList);
      if (fileUris.length) {
        emit("paste: uris", fileUris);
        pastePathsIntoTerm(term, fileUris);
        return { type: "uris", paths: fileUris };
      }

      // 2. File blobs — screenshots, image viewers, drag-promise sources.
      const files = _collectFiles(data);
      const readBlob = opts && opts.readBlob;
      if (files.length && readBlob) {
        const paths = [];
        for (const f of files) {
          try {
            paths.push(await _materialize(f, readBlob, "paste"));
          } catch (e) {
            emit("paste: materialize failed", f && f.name, e && e.message);
          }
        }
        if (paths.length) {
          emit("paste: files", paths);
          pastePathsIntoTerm(term, paths);
          return { type: "files", paths };
        }
      }

      // 3. Plain text fallback.
      const text = typeof data.getData === "function"
        ? data.getData("text/plain")
        : "";
      if (text) {
        emit("paste: text", text.length, "chars");
        _pasteText(term, text);
        return { type: "text", text };
      }

      emit("paste: nothing usable");
      return { type: "none", reason: "no usable data in clipboardData" };
    } catch (e) {
      // Last-resort catch so the DOM listener never sees a thrown exception.
      emit("paste: handler threw", e && e.message);
      return { type: "none", reason: `error: ${e && e.message}` };
    }
  }

  // ── public: synthetic text-only paste (no DOM event available) ───────────
  //
  // Used by callers that don't have a DOM ClipboardEvent. Reads only
  // clipboard.readText() — the single Electron-clipboard method that is
  // safe on macOS (it doesn't access protected clipboard content).
  // Never calls readImage / has / availableFormats / read.
  //
  // Today nothing in the app actually routes through this: the terminal's
  // keydown handler is a no-op, and the textarea paste listener always
  // has an event. Kept so ad-hoc callers (devtools experiments, future
  // menu hooks) have a permissions-safe fallback.
  function handlePaste(term) {
    if (!clipboard || typeof clipboard.readText !== "function") {
      return { type: "none", reason: "no clipboard" };
    }
    const text = clipboard.readText();
    if (text) {
      _pasteText(term, text);
      return { type: "text", text };
    }
    return { type: "none" };
  }

  // ── public: drop ─────────────────────────────────────────────────────────

  async function handleDrop(files, opts) {
    const { webUtils, readBlob, log } = opts || {};
    const emit = (...a) => { if (log) log(...a); };
    const paths = [];

    for (const f of files) {
      // ALWAYS materialize first, even if webUtils could give us a
      // real on-disk path. Two reasons:
      //
      //   1. macOS file-promise drags (screenshot thumbnail from the
      //      bottom-right preview, "drag from Preview/Photos/etc.")
      //      resolve to paths like /var/folders/.../TemporaryItems/
      //      NSIRD_screencaptureui_*/Screenshot*.png. macOS deletes
      //      that NSIRD directory the moment the drag source releases,
      //      so by the time tmux forwards the path to the downstream
      //      app the file may already be gone.
      //
      //   2. Original filenames often contain spaces ("Screenshot
      //      2026-04-10 at 6.10.23 PM.png") which forces us to wrap
      //      the path in double quotes. Not every downstream path
      //      auto-detector unquotes, so the space-quoting dance breaks
      //      file reference parsers.
      //
      // Our tempfiles live under os.tmpdir() and have deterministic
      // no-space names (`distillate-drop-<ts>-<rand><ext>`), matching
      // the paste path. One code path, one naming convention, foolproof.
      if (readBlob) {
        try {
          paths.push(await _materialize(f, readBlob, "drop"));
          emit("drop: materialized", paths[paths.length - 1]);
          continue;
        } catch (e) {
          emit("drop: materialize failed, trying path fallback", e && e.message);
        }
      }

      // Fallback: use the real on-disk path if materialization failed
      // (e.g. File.arrayBuffer() is unavailable for the source type).
      // Finder drags of regular files always land here successfully.
      //
      // IMPORTANT: for macOS file-promise drags (screenshot thumbnails,
      // Preview, Photos), the on-disk path lives under /var/folders/.../
      // TemporaryItems/NSIRD_screencaptureui_*/ and macOS deletes that
      // directory the moment the drag source releases. We MUST copy the
      // file to a persistent temp path before returning it, otherwise
      // the downstream reader (Claude Code, etc.) will get ENOENT.
      let p = "";
      try {
        if (webUtils && typeof webUtils.getPathForFile === "function") {
          p = webUtils.getPathForFile(f) || "";
        }
      } catch (e) {
        emit("drop: webUtils.getPathForFile threw", e && e.message);
      }
      if (!p && f.path) p = f.path;
      if (p) {
        // Copy to persistent temp file — guards against NSIRD cleanup.
        try {
          const buf = fs.readFileSync(p);
          const ext = path.extname(p) || ".bin";
          const rnd = Math.random().toString(36).slice(2, 8);
          const tmp = path.join(
            os.tmpdir(),
            `distillate-drop-${nowFn()}-${rnd}${ext}`,
          );
          fs.writeFileSync(tmp, buf);
          emit("drop: copied ephemeral path to persistent", tmp);
          paths.push(tmp);
        } catch (copyErr) {
          // Last resort: use the original path and hope it still exists.
          emit("drop: copy failed, using original", p, copyErr && copyErr.message);
          paths.push(p);
        }
      }
    }

    return paths;
  }

  return {
    handlePaste,
    handlePasteEvent,
    handleDrop,
    pastePathsIntoTerm,
    _pasteText,
    // Exposed for unit tests.
    _parseFileUris,
    _collectFiles,
    _extOf,
  };
}

module.exports = { createPasteHandlers };
