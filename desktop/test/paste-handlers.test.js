/**
 * Tests for clipboard paste + drag-drop handlers.
 *
 * Structure mirrors paste-handlers.js:
 *
 *   1. handlePasteEvent — the primary paste path (DOM ClipboardEvent).
 *      Covers uri-list, file blobs (screenshots), permissive MIME,
 *      multi-file pastes, text fallback, materialization failures,
 *      and the never-throws contract.
 *
 *   2. handlePaste — synthetic text-only fallback via clipboard.readText().
 *      Covers basic paste, chunking, empty clipboard.
 *
 *   3. handleDrop — the document drop path.
 *      Covers Finder paths, webUtils fallback, screenshot-thumbnail
 *      blobs, multi-file drops, extension detection.
 *
 *   4. pastePathsIntoTerm — path quoting utility.
 *
 * Run: node --test test/paste-handlers.test.js
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { createPasteHandlers } = require("../electron/paste-handlers");

// ── Mock factories ─────────────────────────────────────────────────────────

// A clipboard mock that exposes ONLY readText. Accessing any other
// member throws, which is how we assert "paste handlers never touch
// readImage / has / availableFormats / read".
function makeStrictClipboard(text = "") {
  const calls = [];
  return {
    calls,
    readText: () => { calls.push("readText"); return text; },
    get readImage() { throw new Error("FORBIDDEN: clipboard.readImage"); },
    get has() { throw new Error("FORBIDDEN: clipboard.has"); },
    get availableFormats() { throw new Error("FORBIDDEN: clipboard.availableFormats"); },
    get read() { throw new Error("FORBIDDEN: clipboard.read"); },
  };
}

function makeMockFs(diskFiles = {}) {
  const writes = [];
  return {
    writes,
    writeFileSync: (p, data) => writes.push({ path: p, size: data && data.length }),
    // readFileSync returns pre-seeded content or throws ENOENT.
    readFileSync: (p) => {
      if (diskFiles[p]) return diskFiles[p];
      const err = new Error(`ENOENT: no such file or directory, open '${p}'`);
      err.code = "ENOENT";
      throw err;
    },
  };
}

function makeMockOs(tmpdir = "/tmp") {
  return { tmpdir: () => tmpdir };
}

function makeMockTerm() {
  const pastes = [];
  return { pastes, paste: (s) => pastes.push(s) };
}

function makeNow(start = 1_700_000_000_000) {
  return () => start;
}

function makeHandlers(extra = {}) {
  return createPasteHandlers({
    clipboard: extra.clipboard || makeStrictClipboard(extra.text || ""),
    fs: extra.fs || makeMockFs(),
    os: makeMockOs(),
    path,
    now: makeNow(),
  });
}

// Build a fake DOM ClipboardEvent. Everything is optional so tests can
// simulate the exact shape Chromium surfaces for each scenario.
function makeClipboardEvent({ items = [], files = [], strings = {} } = {}) {
  return {
    clipboardData: {
      items,
      files,
      getData: (type) => (type in strings ? strings[type] : ""),
    },
  };
}

// A File-like object matching what DataTransferItem.getAsFile() returns.
function fakeFile({ name, type = "", size = 100, lastModified = 0 }) {
  return { name, type, size, lastModified };
}

function fileItem(file) {
  return { kind: "file", type: file.type, getAsFile: () => file };
}

function stringItem(type, data) {
  return { kind: "string", type, getAsString: (cb) => cb(data) };
}

// ═══════════════════════════════════════════════════════════════════════════
// handlePasteEvent — the primary paste path
// ═══════════════════════════════════════════════════════════════════════════

describe("handlePasteEvent — file:// URIs in text/uri-list", () => {
  it("pastes a single file:// URI from Finder copy", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      strings: { "text/uri-list": "file:///tmp/screenshot.png" },
    });

    const result = await handlers.handlePasteEvent(term, event, {});
    assert.equal(result.type, "uris");
    assert.deepEqual(result.paths, ["/tmp/screenshot.png"]);
    assert.deepEqual(term.pastes, ["/tmp/screenshot.png"]);
  });

  it("decodes percent-encoded spaces and unicode in filenames", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      strings: { "text/uri-list": "file:///Users/me/My%20Photos/caf%C3%A9.jpg" },
    });

    const result = await handlers.handlePasteEvent(term, event, {});
    assert.equal(result.type, "uris");
    assert.deepEqual(result.paths, ["/Users/me/My Photos/café.jpg"]);
    // Space-containing path must be quoted before it hits the terminal.
    assert.deepEqual(term.pastes, ['"/Users/me/My Photos/café.jpg"']);
  });

  it("supports multiple URIs separated by CRLF (RFC 2483)", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      strings: {
        "text/uri-list":
          "# comment\r\nfile:///tmp/a.png\r\nfile:///tmp/b.png\r\n",
      },
    });

    const result = await handlers.handlePasteEvent(term, event, {});
    assert.deepEqual(result.paths, ["/tmp/a.png", "/tmp/b.png"]);
    assert.deepEqual(term.pastes, ["/tmp/a.png /tmp/b.png"]);
  });

  it("ignores non-file:// URIs in the list", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      strings: { "text/uri-list": "http://example.com/x.png\nfile:///tmp/y.png" },
    });

    const result = await handlers.handlePasteEvent(term, event, {});
    assert.deepEqual(result.paths, ["/tmp/y.png"]);
  });

  it("prefers uri-list over file bytes when both are present (Finder path is cheaper)", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const file = fakeFile({ name: "y.png", type: "image/png", size: 10 });
    const event = makeClipboardEvent({
      items: [fileItem(file)],
      strings: { "text/uri-list": "file:///real/path/y.png" },
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(10),
    });
    assert.equal(result.type, "uris");
    assert.deepEqual(result.paths, ["/real/path/y.png"]);
  });
});

describe("handlePasteEvent — file blobs (screenshots, image viewers)", () => {
  it("materializes a single image/png item to a temp file and pastes the path", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const file = fakeFile({ name: "screenshot.png", type: "image/png", size: 4 });
    const event = makeClipboardEvent({ items: [fileItem(file)] });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1);
    assert.match(result.paths[0], /^\/tmp\/distillate-paste-\d+-[a-z0-9]+\.png$/);
    assert.equal(fs.writes.length, 1);
    assert.equal(fs.writes[0].size, 4);
    assert.equal(term.pastes.length, 1);
  });

  it("is permissive about MIME type: empty type still materializes", async () => {
    // macOS / Chromium sometimes surface screenshots with an empty
    // item.type even though kind === "file". The old strict
    // `type.startsWith("image/")` check dropped these on the floor.
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const file = fakeFile({ name: "unknown.png", type: "", size: 8 });
    const event = makeClipboardEvent({ items: [fileItem(file)] });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(8),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1);
    assert.ok(result.paths[0].endsWith(".png"));
  });

  it("is permissive about MIME type: non-image file still materializes", async () => {
    // A PDF on the clipboard (Preview.app Cmd+C) should also become
    // a temp path — Claude Code can read any file, not just images.
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const file = fakeFile({ name: "paper.pdf", type: "application/pdf", size: 16 });
    const event = makeClipboardEvent({ items: [fileItem(file)] });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(16),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1);
    assert.ok(result.paths[0].endsWith(".pdf"));
  });

  it("extracts extension from MIME when filename has none", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const file = fakeFile({ name: "", type: "image/jpeg", size: 16 });
    const event = makeClipboardEvent({ items: [fileItem(file)] });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(16),
    });

    assert.equal(result.type, "files");
    assert.ok(result.paths[0].endsWith(".jpg"), `got ${result.paths[0]}`);
  });

  it("falls back to .bin when name and type both lack an extension", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const file = fakeFile({ name: "", type: "", size: 8 });
    const event = makeClipboardEvent({ items: [fileItem(file)] });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(8),
    });

    assert.equal(result.type, "files");
    assert.ok(result.paths[0].endsWith(".bin"));
  });

  it("materializes every file in a multi-file paste", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      items: [
        fileItem(fakeFile({ name: "a.png", type: "image/png", size: 4 })),
        fileItem(fakeFile({ name: "b.jpg", type: "image/jpeg", size: 8 })),
        fileItem(fakeFile({ name: "c.pdf", type: "application/pdf", size: 16 })),
      ],
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async (f) => Buffer.alloc(f.size),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 3);
    assert.equal(fs.writes.length, 3);
    // Single paste call with all three paths joined by spaces.
    assert.equal(term.pastes.length, 1);
    const parts = term.pastes[0].split(" ");
    assert.equal(parts.length, 3);
  });

  it("dedupes files that appear in both .items and .files (Chromium quirk)", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const file = fakeFile({ name: "dup.png", type: "image/png", size: 4, lastModified: 42 });
    const event = makeClipboardEvent({
      items: [fileItem(file)],
      files: [file],
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(4),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1, "must not double-materialize the same file");
    assert.equal(fs.writes.length, 1);
  });

  it("reads from legacy .files when .items is empty", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      files: [fakeFile({ name: "legacy.png", type: "image/png", size: 4 })],
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(4),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1);
  });

  it("skips string-kind items when looking for files", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      items: [stringItem("text/plain", "ignored")],
      strings: { "text/plain": "ignored" },
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(0),
    });

    // No files → falls through to text/plain.
    assert.equal(result.type, "text");
    assert.equal(result.text, "ignored");
  });

  it("falls through to text when materialization throws for every file", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      items: [fileItem(fakeFile({ name: "a.png", type: "image/png", size: 4 }))],
      strings: { "text/plain": "textual fallback" },
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => { throw new Error("disk full"); },
    });

    assert.equal(result.type, "text");
    assert.equal(result.text, "textual fallback");
  });

  it("partial multi-file failure: successful files still get pasted", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      items: [
        fileItem(fakeFile({ name: "ok.png", type: "image/png", size: 4 })),
        fileItem(fakeFile({ name: "bad.png", type: "image/png", size: 4 })),
      ],
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async (f) => {
        if (f.name === "bad.png") throw new Error("nope");
        return Buffer.alloc(4);
      },
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1);
    assert.ok(result.paths[0].includes("distillate-paste-"));
  });
});

describe("handlePasteEvent — text fallback", () => {
  it("pastes plain text when no files / URIs are present", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({
      strings: { "text/plain": "hello world" },
    });

    const result = await handlers.handlePasteEvent(term, event, {});
    assert.equal(result.type, "text");
    assert.equal(result.text, "hello world");
    assert.deepEqual(term.pastes, ["hello world"]);
  });

  it("chunks large text pastes", (t, done) => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const big = "a".repeat(1500);
    const event = makeClipboardEvent({ strings: { "text/plain": big } });

    handlers.handlePasteEvent(term, event, {}).then(() => {
      assert.equal(term.pastes.length, 1);
      assert.equal(term.pastes[0].length, 512);
      // Chunker uses setTimeout(sendChunk, 5). Under CI load, timer drift
      // can push 3-chunk completion past a tight 50ms wait — bumped to 500ms
      // for a 10× margin while still keeping the test fast.
      setTimeout(() => {
        const total = term.pastes.reduce((s, c) => s + c.length, 0);
        assert.equal(total, 1500);
        assert.equal(term.pastes.join(""), big);
        done();
      }, 500);
    });
  });
});

describe("handlePasteEvent — none / edge cases", () => {
  it("returns none when clipboardData is missing", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const result = await handlers.handlePasteEvent(term, {}, {});
    assert.equal(result.type, "none");
    assert.equal(term.pastes.length, 0);
  });

  it("returns none when event is null", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const result = await handlers.handlePasteEvent(term, null, {});
    assert.equal(result.type, "none");
  });

  it("returns none when clipboardData has nothing usable", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({});
    const result = await handlers.handlePasteEvent(term, event, {});
    assert.equal(result.type, "none");
    assert.equal(term.pastes.length, 0);
  });

  it("never throws — swallows unexpected errors into { type: 'none' }", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const badEvent = {
      clipboardData: {
        get items() { throw new Error("boom"); },
        get files() { throw new Error("boom"); },
        getData() { throw new Error("boom"); },
      },
    };
    const result = await handlers.handlePasteEvent(term, badEvent, {});
    assert.equal(result.type, "none");
    assert.match(String(result.reason), /error/);
  });

  it("works without an opts object", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({ strings: { "text/plain": "x" } });
    const result = await handlers.handlePasteEvent(term, event);
    assert.equal(result.type, "text");
  });
});

describe("handlePasteEvent — privacy guarantee", () => {
  it("NEVER touches clipboard.readImage / has / availableFormats / read", async () => {
    // The strict clipboard throws on any dangerous access. Running all
    // three priority paths (uri, file, text) should not read the clipboard
    // module at all — everything comes from the DOM event.
    const clipboard = makeStrictClipboard();
    const handlers = createPasteHandlers({
      clipboard,
      fs: makeMockFs(),
      os: makeMockOs(),
      path,
      now: makeNow(),
    });

    await handlers.handlePasteEvent(makeMockTerm(),
      makeClipboardEvent({ strings: { "text/uri-list": "file:///x" } }), {});
    await handlers.handlePasteEvent(makeMockTerm(),
      makeClipboardEvent({ items: [fileItem(fakeFile({ name: "a.png", type: "image/png" }))] }),
      { readBlob: async () => Buffer.alloc(0) });
    await handlers.handlePasteEvent(makeMockTerm(),
      makeClipboardEvent({ strings: { "text/plain": "y" } }), {});

    assert.deepEqual(clipboard.calls, [],
      "handlePasteEvent must not touch clipboard.readText either — everything is in the DOM event");
  });
});

describe("handlePasteEvent — debug logger", () => {
  it("invokes opts.log with path taken when a logger is supplied", async () => {
    const logs = [];
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({ strings: { "text/plain": "ping" } });

    await handlers.handlePasteEvent(term, event, {
      log: (...a) => logs.push(a.join(" ")),
    });
    assert.ok(logs.some((l) => l.startsWith("paste: text")),
      `expected a 'paste: text' log, got: ${logs.join(" | ")}`);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// handlePaste — synthetic text-only fallback
// ═══════════════════════════════════════════════════════════════════════════

describe("handlePaste — synthetic (no event)", () => {
  it("pastes text from clipboard.readText", () => {
    const clipboard = makeStrictClipboard("hello");
    const handlers = createPasteHandlers({
      clipboard, fs: makeMockFs(), os: makeMockOs(), path, now: makeNow(),
    });
    const term = makeMockTerm();
    const result = handlers.handlePaste(term);
    assert.equal(result.type, "text");
    assert.equal(result.text, "hello");
    assert.deepEqual(term.pastes, ["hello"]);
    assert.deepEqual(clipboard.calls, ["readText"]);
  });

  it("chunks large text", (t, done) => {
    const big = "a".repeat(1500);
    const handlers = createPasteHandlers({
      clipboard: makeStrictClipboard(big),
      fs: makeMockFs(), os: makeMockOs(), path, now: makeNow(),
    });
    const term = makeMockTerm();
    handlers.handlePaste(term);
    assert.equal(term.pastes.length, 1);
    assert.equal(term.pastes[0].length, 512);
    // See note on the parallel handlePasteEvent test above — 500ms accommodates
    // timer drift on loaded CI runners.
    setTimeout(() => {
      assert.equal(term.pastes.join(""), big);
      done();
    }, 500);
  });

  it("returns none on empty clipboard without touching image APIs", () => {
    const clipboard = makeStrictClipboard("");
    const handlers = createPasteHandlers({
      clipboard, fs: makeMockFs(), os: makeMockOs(), path, now: makeNow(),
    });
    const result = handlers.handlePaste(makeMockTerm());
    assert.equal(result.type, "none");
    assert.deepEqual(clipboard.calls, ["readText"]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// handleDrop — document drop path
// ═══════════════════════════════════════════════════════════════════════════

describe("handleDrop", () => {
  // New contract: always materialize first (copy bytes to our tempfile),
  // fall back to the real on-disk path only if materialization fails.
  // Rationale: macOS file-promise drags (screenshot thumbnails, Preview
  // drags) resolve to ephemeral NSIRD_screencaptureui_* paths that get
  // cleaned up the moment the drag source releases. Our tempfiles are
  // persistent and have no-space names, which avoids both the
  // file-disappears-before-read bug and the quoting dance.

  it("always materializes — even when a real path is available (regression: ephemeral NSIRD paths)", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const webUtils = {
      getPathForFile: () =>
        "/var/folders/gg/xx/T/TemporaryItems/NSIRD_screencaptureui_ABC/Screenshot 2026-04-10 at 6.10.23 PM.png",
    };
    const files = [{ name: "Screen Shot.png", type: "image/png", size: 100 }];

    const paths = await handlers.handleDrop(files, {
      webUtils,
      readBlob: async () => Buffer.alloc(100),
    });

    assert.equal(paths.length, 1);
    assert.match(paths[0], /^\/tmp\/distillate-drop-\d+-[a-z0-9]+\.png$/,
      "must materialize to our own no-space tempfile, NOT use the ephemeral NSIRD path");
    assert.equal(fs.writes.length, 1);
  });

  it("materializes path-less blobs to temp file (screenshot thumbnail, pure blob case)", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const files = [{ name: "Screen Shot.png", type: "image/png", size: 4 }];

    const paths = await handlers.handleDrop(files, {
      readBlob: async () => Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    });

    assert.equal(paths.length, 1);
    assert.match(paths[0], /^\/tmp\/distillate-drop-\d+-[a-z0-9]+\.png$/);
    assert.equal(fs.writes.length, 1);
    assert.equal(fs.writes[0].size, 4);
  });

  it("uses original extension when materializing", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const files = [{ name: "photo.jpg", type: "image/jpeg", size: 10 }];
    const paths = await handlers.handleDrop(files, { readBlob: async () => Buffer.alloc(10) });
    assert.ok(paths[0].endsWith(".jpg"), `expected .jpg, got ${paths[0]}`);
  });

  it("falls back to copying from disk when materialization throws (guards against NSIRD cleanup)", async () => {
    const diskContent = Buffer.from([0x89, 0x50, 0x4e, 0x47]); // fake PNG header
    const diskPath = "/Users/romain/Desktop/disk.png";
    const fs = makeMockFs({ [diskPath]: diskContent });
    const handlers = makeHandlers({ fs });
    const webUtils = { getPathForFile: () => diskPath };
    const files = [{ name: "disk.png", type: "image/png", size: 100 }];

    const paths = await handlers.handleDrop(files, {
      webUtils,
      readBlob: async () => { throw new Error("arrayBuffer not available"); },
    });

    assert.equal(paths.length, 1);
    assert.match(paths[0], /^\/tmp\/distillate-drop-\d+-[a-z0-9]+\.png$/,
      "must copy to persistent tempfile, NOT use the ephemeral original path");
    assert.equal(fs.writes.length, 1);
    assert.equal(fs.writes[0].size, 4);
  });

  it("uses original path as last resort when disk copy also fails", async () => {
    // fs.readFileSync throws ENOENT — file already deleted by macOS.
    const handlers = makeHandlers();
    const webUtils = { getPathForFile: () => "/var/folders/xx/NSIRD/Screenshot.png" };
    const files = [{ name: "Screenshot.png", type: "image/png", size: 100 }];

    const paths = await handlers.handleDrop(files, {
      webUtils,
      readBlob: async () => { throw new Error("arrayBuffer not available"); },
    });
    // File is gone — last resort is the original path (will ENOENT downstream).
    assert.deepEqual(paths, ["/var/folders/xx/NSIRD/Screenshot.png"]);
  });

  it("falls back to f.path when webUtils is unavailable AND materialization throws", async () => {
    const handlers = makeHandlers();
    const files = [{ name: "a.png", path: "/tmp/a.png", type: "image/png" }];
    const paths = await handlers.handleDrop(files, {
      readBlob: async () => { throw new Error("no arrayBuffer"); },
    });
    assert.deepEqual(paths, ["/tmp/a.png"]);
  });

  it("handles multiple files in a single drop (all materialized)", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const files = [
      { name: "a.png", type: "image/png", size: 4 },
      { name: "b.png", type: "image/png", size: 4 },
    ];
    const paths = await handlers.handleDrop(files, {
      readBlob: async () => Buffer.alloc(4),
    });
    assert.equal(paths.length, 2);
    for (const p of paths) assert.match(p, /^\/tmp\/distillate-drop-/);
    assert.equal(fs.writes.length, 2);
  });

  it("mixes materialized successes and path fallbacks when some readBlob calls fail", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const files = [
      { name: "ok.png", type: "image/png", size: 4 },
      { name: "bad.png", type: "image/png", size: 4, path: "/tmp/bad.png" },
    ];
    const paths = await handlers.handleDrop(files, {
      readBlob: async (f) => {
        if (f.name === "bad.png") throw new Error("nope");
        return Buffer.alloc(4);
      },
    });
    assert.equal(paths.length, 2);
    assert.match(paths[0], /^\/tmp\/distillate-drop-/);
    assert.equal(paths[1], "/tmp/bad.png");
  });

  it("skips files that cannot be resolved at all", async () => {
    const handlers = makeHandlers();
    const files = [{ name: "ghost.png", type: "image/png", size: 10 }];
    const paths = await handlers.handleDrop(files, {
      readBlob: async () => { throw new Error("no can do"); },
    });
    assert.deepEqual(paths, [],
      "no readBlob success and no real path → skipped silently");
  });

  it("continues after a webUtils throw during fallback", async () => {
    const handlers = makeHandlers();
    const webUtils = { getPathForFile: () => { throw new Error("boom"); } };
    const files = [{ name: "fallback.png", path: "/tmp/fallback.png" }];
    const paths = await handlers.handleDrop(files, {
      webUtils,
      readBlob: async () => { throw new Error("force fallback"); },
    });
    assert.deepEqual(paths, ["/tmp/fallback.png"]);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// pastePathsIntoTerm
// ═══════════════════════════════════════════════════════════════════════════

describe("pastePathsIntoTerm", () => {
  const makeH = () => makeHandlers();

  it("pastes single path unquoted when no spaces", () => {
    const term = makeMockTerm();
    makeH().pastePathsIntoTerm(term, ["/tmp/foo.png"]);
    assert.deepEqual(term.pastes, ["/tmp/foo.png"]);
  });

  it("quotes paths containing spaces", () => {
    const term = makeMockTerm();
    makeH().pastePathsIntoTerm(term, ["/tmp/my file.png"]);
    assert.deepEqual(term.pastes, ['"/tmp/my file.png"']);
  });

  it("joins multiple paths with space, quoting individually", () => {
    const term = makeMockTerm();
    makeH().pastePathsIntoTerm(term, ["/a.png", "/b with space.png"]);
    assert.deepEqual(term.pastes, ['/a.png "/b with space.png"']);
  });

  it("returns false for empty / null path lists", () => {
    const term = makeMockTerm();
    assert.equal(makeH().pastePathsIntoTerm(term, []), false);
    assert.equal(makeH().pastePathsIntoTerm(term, null), false);
    assert.equal(term.pastes.length, 0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Smoke test — simulates preload.js wiring end-to-end
// ═══════════════════════════════════════════════════════════════════════════

describe("smoke: preload.js-style wiring with a fake DOM event", () => {
  // Reproduces the exact shape of what preload.js passes into
  // handlePasteEvent from its textarea "paste" listener. This is the
  // last line of defense: if the fake event goes through our pipeline
  // and lands a valid tempfile path in the terminal, the wiring works.
  it("screenshot paste: PNG bytes → tempfile → quoted path in terminal", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();

    // Simulate Cmd+Shift+Ctrl+4 screenshot on the clipboard.
    const file = fakeFile({
      name: "Screen Shot.png", type: "image/png", size: 12, lastModified: 1,
    });
    const event = makeClipboardEvent({
      items: [
        // Screenshot image file
        fileItem(file),
        // Chromium also adds a string-kind item for text/html sometimes;
        // our collector must ignore it.
        stringItem("text/html", "<img src='...'/>"),
      ],
      files: [file], // duplicated in legacy .files list
    });

    // readBlob matches what preload wires: Buffer.from(await file.arrayBuffer())
    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async (f) => Buffer.alloc(f.size),
    });

    assert.equal(result.type, "files");
    assert.equal(result.paths.length, 1, "single screenshot must not double-materialize");
    assert.equal(fs.writes.length, 1);
    assert.equal(term.pastes.length, 1);
    assert.match(term.pastes[0], /distillate-paste-\d+-[a-z0-9]+\.png/);
  });

  it("Finder file copy: uri-list wins, no fs.writeFileSync", async () => {
    const fs = makeMockFs();
    const handlers = makeHandlers({ fs });
    const term = makeMockTerm();

    const event = makeClipboardEvent({
      items: [stringItem("text/uri-list", "file:///Users/romain/paper.pdf")],
      strings: { "text/uri-list": "file:///Users/romain/paper.pdf" },
    });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(0),
    });

    assert.equal(result.type, "uris");
    assert.deepEqual(result.paths, ["/Users/romain/paper.pdf"]);
    assert.deepEqual(term.pastes, ["/Users/romain/paper.pdf"]);
    assert.equal(fs.writes.length, 0, "uri-list path must not materialize");
  });

  it("plain text copy: no files, no uri-list → text/plain is pasted", async () => {
    const handlers = makeHandlers();
    const term = makeMockTerm();
    const event = makeClipboardEvent({ strings: { "text/plain": "ls -la" } });

    const result = await handlers.handlePasteEvent(term, event, {
      readBlob: async () => Buffer.alloc(0),
    });

    assert.equal(result.type, "text");
    assert.deepEqual(term.pastes, ["ls -la"]);
  });
});
