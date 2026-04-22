/**
 * Permission-prompt regression tests.
 *
 * Distillate must run with ZERO macOS permission prompts. The rule is
 * absolute: no paste-related code path may ever touch a clipboard API
 * that can trigger a privacy prompt. That includes:
 *
 *   - clipboard.readImage()   → can prompt for Apple Music / Photos
 *   - clipboard.read(type)    → can prompt on protected formats
 *   - clipboard.has(type)     → on some Electron/macOS combinations,
 *                               silently probes protected data
 *   - clipboard.availableFormats() → safe as a pure enumerator but we
 *                               avoid it anyway to keep the boundary
 *                               dead-simple: only readText() is allowed
 *
 * The rule: Cmd+V inside the terminal is TEXT-ONLY. Images are handled
 * by the drag-and-drop path, which uses File.arrayBuffer() / on-disk
 * paths and requires no permissions.
 *
 * These tests are the regression guard. They run two kinds of checks:
 *
 *   1. Behavior tests against createPasteHandlers with an auditing
 *      mock clipboard — the mock throws (or fatally records) if any
 *      protected API is called.
 *
 *   2. Static-source tests that grep the shipped electron code for
 *      dangerous identifiers. This catches future regressions even if
 *      nobody adds a behavior test for the new path.
 *
 * Run: node --test test/no-permissions.test.js
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");
const { createPasteHandlers } = require("../electron/paste-handlers");

// ── Behavior: paste handler only touches readText ─────────────────────────

// A clipboard proxy whose dangerous members throw on access. Any attempt
// to touch readImage / has / availableFormats / read explodes so the test
// can hard-assert "this must never be called."
function makeStrictClipboard(text) {
  const calls = [];
  return {
    calls,
    readText: () => {
      calls.push("readText");
      return text;
    },
    get readImage() {
      calls.push("readImage-ACCESS");
      throw new Error("FORBIDDEN: paste handler accessed clipboard.readImage");
    },
    get has() {
      calls.push("has-ACCESS");
      throw new Error("FORBIDDEN: paste handler accessed clipboard.has");
    },
    get availableFormats() {
      calls.push("availableFormats-ACCESS");
      throw new Error("FORBIDDEN: paste handler accessed clipboard.availableFormats");
    },
    get read() {
      calls.push("read-ACCESS");
      throw new Error("FORBIDDEN: paste handler accessed clipboard.read");
    },
  };
}

function mockFs() { return { writeFileSync: () => {} }; }
function mockOs() { return { tmpdir: () => "/tmp" }; }
function mockTerm() {
  const pastes = [];
  return { pastes, paste: (s) => pastes.push(s) };
}

describe("handlePaste is text-only", () => {
  it("pastes text without touching any image/has/availableFormats API", () => {
    const clipboard = makeStrictClipboard("hello world");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    const term = mockTerm();
    const result = handlers.handlePaste(term);
    assert.equal(result.type, "text");
    assert.equal(result.text, "hello world");
    assert.deepEqual(term.pastes, ["hello world"]);
    assert.deepEqual(clipboard.calls, ["readText"]);
  });

  it("returns 'none' on empty clipboard without probing image APIs", () => {
    const clipboard = makeStrictClipboard("");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    const result = handlers.handlePaste(mockTerm());
    assert.equal(result.type, "none");
    assert.deepEqual(clipboard.calls, ["readText"]);
  });

  it("does not access image APIs even when the clipboard has protected-looking content", () => {
    // The strict clipboard doesn't need to simulate formats — if any
    // protected API gets touched, it throws.  This covers the Apple Music
    // regression from issue c027d46: we don't even peek.
    const clipboard = makeStrictClipboard("https://music.apple.com/us/album/…");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    handlers.handlePaste(mockTerm());
    assert.deepEqual(clipboard.calls, ["readText"]);
  });
});

describe("handlePasteEvent never touches the Electron clipboard module", () => {
  // handlePasteEvent is the PRIMARY paste path (Cmd+V, menu Paste,
  // screenshots). It reads everything from the DOM ClipboardEvent and
  // must never fall back to Electron's clipboard module. This is the
  // regression guard: if a future change re-introduces a readImage /
  // has / availableFormats / read call, the strict clipboard throws.

  const mkClipboardEvent = ({ items = [], files = [], strings = {} } = {}) => ({
    clipboardData: {
      items,
      files,
      getData: (type) => (type in strings ? strings[type] : ""),
    },
  });
  const fakeFile = (name, type, size = 4) => ({ name, type, size, lastModified: 0 });
  const fileItem = (f) => ({ kind: "file", type: f.type, getAsFile: () => f });

  it("uri-list path: does not read from clipboard module", async () => {
    const clipboard = makeStrictClipboard("should-not-be-read");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    await handlers.handlePasteEvent(
      mockTerm(),
      mkClipboardEvent({ strings: { "text/uri-list": "file:///tmp/x.png" } }),
      {},
    );
    assert.deepEqual(clipboard.calls, []);
  });

  it("file-bytes path: does not read from clipboard module", async () => {
    const clipboard = makeStrictClipboard("should-not-be-read");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    await handlers.handlePasteEvent(
      mockTerm(),
      mkClipboardEvent({ items: [fileItem(fakeFile("a.png", "image/png"))] }),
      { readBlob: async () => Buffer.alloc(4) },
    );
    assert.deepEqual(clipboard.calls, []);
  });

  it("text path: does not read from clipboard module", async () => {
    const clipboard = makeStrictClipboard("should-not-be-read");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    await handlers.handlePasteEvent(
      mockTerm(),
      mkClipboardEvent({ strings: { "text/plain": "hi" } }),
      {},
    );
    assert.deepEqual(clipboard.calls, []);
  });

  it("none path: does not read from clipboard module", async () => {
    const clipboard = makeStrictClipboard("should-not-be-read");
    const handlers = createPasteHandlers({
      clipboard, fs: mockFs(), os: mockOs(), path, now: () => 1_700_000_000_000,
    });
    await handlers.handlePasteEvent(mockTerm(), mkClipboardEvent({}), {});
    assert.deepEqual(clipboard.calls, []);
  });
});

// ── Static source audit: dangerous identifiers must not appear ────────────

const ELECTRON_SRC_DIR = path.resolve(__dirname, "../electron");
const RENDERER_SRC_DIR = path.resolve(__dirname, "../renderer");

function readSourceFrom(dir, file) {
  return fs.readFileSync(path.join(dir, file), "utf-8");
}

function readSource(file) {
  return readSourceFrom(ELECTRON_SRC_DIR, file);
}

function stripComments(src) {
  // Remove line comments and block comments so prose in docstrings
  // (which may legitimately mention readImage etc.) doesn't trip the
  // source audit. Simple state machine, no regex.
  let out = "";
  let i = 0;
  const n = src.length;
  while (i < n) {
    // Block comment
    if (src[i] === "/" && src[i + 1] === "*") {
      const end = src.indexOf("*/", i + 2);
      if (end === -1) break;
      i = end + 2;
      continue;
    }
    // Line comment
    if (src[i] === "/" && src[i + 1] === "/") {
      const nl = src.indexOf("\n", i + 2);
      if (nl === -1) break;
      i = nl + 1;
      continue;
    }
    // String literals — skip so grep doesn't match inside error messages
    if (src[i] === '"' || src[i] === "'" || src[i] === "`") {
      const quote = src[i];
      i++;
      while (i < n && src[i] !== quote) {
        if (src[i] === "\\") i += 2;
        else i++;
      }
      i++;
      continue;
    }
    out += src[i];
    i++;
  }
  return out;
}

const FORBIDDEN_PATTERNS = [
  // Clipboard APIs that may trigger macOS privacy prompts
  { re: /\bclipboard\.readImage\s*\(/, reason: "clipboard.readImage() prompts for Apple Music/Photos access" },
  { re: /\bclipboard\.has\s*\(/,       reason: "clipboard.has() may silently probe protected data" },
  { re: /\bclipboard\.availableFormats\s*\(/, reason: "clipboard.availableFormats() is safe in isolation but we keep the boundary simple" },
  { re: /\bclipboard\.read\s*\(/,      reason: "clipboard.read(type) may prompt on protected formats" },
  // Web Clipboard API — navigator.clipboard.readText() / .read() trigger
  // the macOS "access data from other apps" prompt in Electron renderers.
  { re: /navigator\.clipboard\.readText\s*\(/, reason: "navigator.clipboard.readText() triggers macOS cross-app data access prompt" },
  { re: /navigator\.clipboard\.read\s*\(/,     reason: "navigator.clipboard.read() triggers macOS cross-app data access prompt" },
  // AppleScript / automation APIs that trigger the Apps permission prompt
  { re: /\bosascript\b/,               reason: "osascript triggers the macOS Automation permission prompt" },
  { re: /NSAppleScript/,               reason: "NSAppleScript triggers the macOS Automation permission prompt" },
  // Media / device APIs that trigger per-category prompts
  { re: /navigator\.mediaDevices/,     reason: "mediaDevices triggers camera/microphone prompts" },
  { re: /getUserMedia/,                reason: "getUserMedia triggers camera/microphone prompts" },
  { re: /systemPreferences\.askForMediaAccess/, reason: "askForMediaAccess triggers device permission prompts" },
];

const AUDITED_FILES = [
  "preload.js",
  "paste-handlers.js",
  "terminal-controller.js",
  "main.js",
  "menu.js",
  "python-manager.js",
  "pty-manager.js",
  "tectonic-manager.js",
  "writeup-fs.js",
];

const AUDITED_RENDERER_FILES = [
  "browser-shim.js",
];

describe("static source audit — no permission-triggering APIs", () => {
  for (const file of AUDITED_FILES) {
    const fullPath = path.join(ELECTRON_SRC_DIR, file);
    if (!fs.existsSync(fullPath)) continue;
    const raw = readSource(file);
    const code = stripComments(raw);
    for (const { re, reason } of FORBIDDEN_PATTERNS) {
      it(`electron/${file} does not use ${re.source}`, () => {
        assert.equal(
          re.test(code),
          false,
          `electron/${file}: ${reason}\nPattern: ${re}`,
        );
      });
    }
  }
  for (const file of AUDITED_RENDERER_FILES) {
    const fullPath = path.join(RENDERER_SRC_DIR, file);
    if (!fs.existsSync(fullPath)) continue;
    const raw = readSourceFrom(RENDERER_SRC_DIR, file);
    const code = stripComments(raw);
    for (const { re, reason } of FORBIDDEN_PATTERNS) {
      it(`renderer/${file} does not use ${re.source}`, () => {
        assert.equal(
          re.test(code),
          false,
          `renderer/${file}: ${reason}\nPattern: ${re}`,
        );
      });
    }
  }
});
