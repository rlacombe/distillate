/**
 * Tests for the Tectonic log parser and CanvasFs path sandboxing.
 *
 * Run: node --test test/tectonic-manager.test.js
 *
 * We avoid importing tectonic-manager.js directly because it pulls in the
 * Electron `app` module which isn't available in a plain node test runner.
 * Instead we stub `require("electron")` before loading it.
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const Module = require("node:module");

// Stub 'electron' so tectonic-manager can be required outside Electron.
const _origResolve = Module._resolveFilename;
const _origLoad = Module._load;
Module._load = function (request, parent, ...rest) {
  if (request === "electron") {
    return { app: { getPath: (_k) => "/tmp" } };
  }
  return _origLoad.call(this, request, parent, ...rest);
};

const { parseTectonicLog } = require("../electron/tectonic-manager.js");
const { CanvasFs } = require("../electron/canvas-fs.js");

// Restore the real loader
Module._load = _origLoad;
Module._resolveFilename = _origResolve;

describe("parseTectonicLog", () => {
  it("parses a TeX error with line number", () => {
    const log = `
This is XeTeX, Version 3.14
(./main.tex
! Undefined control sequence.
l.42 \\foo
         {hello}
`;
    const errors = parseTectonicLog(log, "main.tex");
    assert.equal(errors.length, 1);
    assert.equal(errors[0].severity, "error");
    assert.equal(errors[0].line, 42);
    assert.match(errors[0].message, /Undefined control sequence/);
  });

  it("parses a LaTeX warning with input line", () => {
    const log = `LaTeX Warning: Reference \`foo' on page 1 undefined on input line 17.`;
    const errors = parseTectonicLog(log, "main.tex");
    assert.equal(errors.length, 1);
    assert.equal(errors[0].severity, "warning");
    assert.equal(errors[0].line, 17);
  });

  it("distinguishes errors from warnings in Package lines", () => {
    const log = `
Package hyperref Warning: Option 'draft' has been used on input line 5.
Package biblatex Error: 'natbib' not loaded.
`;
    const errors = parseTectonicLog(log, "main.tex");
    const warnings = errors.filter((e) => e.severity === "warning");
    const errs = errors.filter((e) => e.severity === "error");
    assert.ok(warnings.length >= 1);
    assert.ok(errs.length >= 1);
  });

  it("returns empty array for empty log", () => {
    assert.deepEqual(parseTectonicLog("", "main.tex"), []);
    assert.deepEqual(parseTectonicLog(null, "main.tex"), []);
  });

  it("handles multiple errors in one log", () => {
    const log = `
! Missing $ inserted.
l.10 $x_y
! Undefined control sequence.
l.20 \\qux
`;
    const errors = parseTectonicLog(log, "main.tex");
    assert.equal(errors.length, 2);
    assert.equal(errors[0].line, 10);
    assert.equal(errors[1].line, 20);
  });
});

describe("CanvasFs path sandbox", () => {
  // CanvasFs reaches out to fetch() for the dir lookup. We bypass that by
  // priming the internal cache directly, then test the safe-resolve logic.
  // The cache is now keyed by (wsId, wuId) since a workspace can hold
  // multiple write-ups.

  const fs = require("node:fs");
  const os = require("node:os");
  const tmpBase = fs.mkdtempSync(path.join(os.tmpdir(), "canvas-fs-"));
  const tmpOther = fs.mkdtempSync(path.join(os.tmpdir(), "canvas-fs-b-"));

  const wfs = new CanvasFs({ getServerPort: () => null });
  wfs._dirCache.set("ws1::cv_001", { dir: tmpBase, entry: "main.tex", exists: true });
  wfs._dirCache.set("ws1::cv_002", { dir: tmpOther, entry: "main.tex", exists: true });

  it("rejects absolute paths", async () => {
    const r = await wfs.writeFile("ws1", "cv_001", "/etc/passwd", "pwned")
      .catch((e) => ({ ok: false, error: e.message }));
    assert.equal(r.ok, false);
  });

  it("rejects ../ escapes", async () => {
    const r = await wfs.writeFile("ws1", "cv_001", "../../escape.tex", "pwned")
      .catch((e) => ({ ok: false, error: e.message }));
    assert.equal(r.ok, false);
  });

  it("accepts nested relative paths inside the sandbox", async () => {
    const r = await wfs.writeFile("ws1", "cv_001", "sections/intro.tex", "hello");
    assert.equal(r.ok, true);
    const read = await wfs.readFile("ws1", "cv_001", "sections/intro.tex");
    assert.equal(read.ok, true);
    assert.equal(read.content, "hello");
  });

  it("two canvases in the same workspace have independent sandboxes", async () => {
    await wfs.writeFile("ws1", "cv_001", "paper.tex", "paper content");
    await wfs.writeFile("ws1", "cv_002", "paper.tex", "other content");

    const a = await wfs.readFile("ws1", "cv_001", "paper.tex");
    const b = await wfs.readFile("ws1", "cv_002", "paper.tex");
    assert.equal(a.content, "paper content");
    assert.equal(b.content, "other content");
  });

  it("listFiles returns only files inside the sandbox, skipping build/", async () => {
    fs.mkdirSync(path.join(tmpBase, "build"), { recursive: true });
    fs.writeFileSync(path.join(tmpBase, "build", "main.pdf"), "pdfbytes");
    fs.writeFileSync(path.join(tmpBase, "main.tex"), "\\documentclass{article}");
    const list = await wfs.listFiles("ws1", "cv_001");
    assert.equal(list.ok, true);
    const paths = list.files.map((f) => f.path);
    assert.ok(paths.includes("main.tex"));
    assert.ok(!paths.some((p) => p.startsWith("build")));
  });

  it("invalidate(wsId) drops all write-ups in that workspace", () => {
    // Fresh cache for this test
    const wfs2 = new CanvasFs({ getServerPort: () => null });
    wfs2._dirCache.set("ws1::cv_001", { dir: tmpBase, entry: "main.tex" });
    wfs2._dirCache.set("ws1::cv_002", { dir: tmpOther, entry: "main.tex" });
    wfs2._dirCache.set("ws2::cv_001", { dir: tmpBase, entry: "main.tex" });
    wfs2.invalidate("ws1");
    assert.equal(wfs2._dirCache.has("ws1::cv_001"), false);
    assert.equal(wfs2._dirCache.has("ws1::cv_002"), false);
    assert.equal(wfs2._dirCache.has("ws2::cv_001"), true);
  });
});
