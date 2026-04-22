/**
 * Playwright fixtures: spin up a Distillate Python server against an
 * isolated tmp config dir, seed a minimal paper + PDF on disk, and
 * expose the base URL + paper key to each spec. Tears down on exit.
 */

const { test: base } = require("@playwright/test");
const { spawn } = require("node:child_process");
const { mkdtempSync, rmSync, writeFileSync, mkdirSync, existsSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");


function waitForServer(url, timeoutMs = 15_000) {
  const start = Date.now();
  return new Promise((accept, reject) => {
    const tick = async () => {
      try {
        const r = await fetch(url);
        if (r.ok) return accept();
      } catch { /* not ready yet */ }
      if (Date.now() - start > timeoutMs) {
        return reject(new Error(`server at ${url} didn't come up in ${timeoutMs}ms`));
      }
      setTimeout(tick, 200);
    };
    tick();
  });
}


/**
 * Stub for the Electron preload API that the renderer assumes.
 * Injected BEFORE any page script runs via addInitScript.
 *
 * The stub returns a resolved Promise for every method call — that
 * way the renderer's `.then(...)` chains on nicolas/distillate APIs
 * don't crash. Methods that return sync values get a Promise that
 * resolves to undefined; callers treating them sync will still see
 * undefined but won't throw on `.then`.
 */
const NICOLAS_STUB = `
  const _mkFn = () => {
    const fn = (...args) => Promise.resolve(undefined);
    // Allow both fn() and fn.then() style callers.
    fn.then = (...args) => Promise.resolve(undefined).then(...args);
    return fn;
  };
  const _makeProxy = () => new Proxy({}, {
    get(target, prop) {
      if (typeof prop === 'symbol') return undefined;
      if (prop === 'then') return undefined;  // stop a stray "await proxy"
      if (!(prop in target)) target[prop] = _mkFn();
      return target[prop];
    },
  });
  window.nicolas = _makeProxy();
  window.distillate = window.distillate || new Proxy({}, {
    get(target, prop) {
      if (typeof prop === 'symbol') return undefined;
      if (prop === 'then') return undefined;
      if (!(prop in target)) target[prop] = _makeProxy();
      return target[prop];
    },
  });
`;

const test = base.extend({
  // Auto-inject the Electron API shim before every page script.
  page: async ({ page }, use) => {
    await page.addInitScript(NICOLAS_STUB);
    await use(page);
  },
  serverContext: [async ({}, use) => {
    // Tmp config dir for full isolation.
    const cfg = mkdtempSync(join(tmpdir(), "distillate-e2e-"));
    const stateFile = join(cfg, "state.json");

    // Vault root with the minimal folder structure the resolver walks.
    const vault = join(cfg, "vault");
    const papersDir = join(vault, "Distillate", "Papers");
    const inbox = join(papersDir, "To Read");
    mkdirSync(inbox, { recursive: true });
    mkdirSync(join(papersDir, "Notes"), { recursive: true });
    mkdirSync(join(papersDir, "pdf"), { recursive: true });
    // Obsidian vault marker — auto-discovery looks for .obsidian.
    mkdirSync(join(vault, ".obsidian"), { recursive: true });

    // Stage a minimal PDF on disk. We use the test helper to generate
    // it via PyMuPDF since the Python venv is already set up.
    const pdfPath = join(inbox, "heuler_2026.pdf");
    const repoRoot = resolve(__dirname, "../..");
    const venvPython = join(repoRoot, ".venv", "bin", "python3");
    const mkPdfScript = `
import sys, pymupdf
doc = pymupdf.open()
page = doc.new_page(width=612, height=792)
page.insert_text((72, 100), "Sample paper body text for E2E.", fontsize=12)
page.insert_text((72, 130), "A second line to select across.", fontsize=12)
doc.save(sys.argv[1])
doc.close()
    `;
    const pdfGen = spawn(venvPython, ["-c", mkPdfScript, pdfPath], { stdio: "inherit" });
    await new Promise((a, r) => {
      pdfGen.on("exit", (code) => code === 0 ? a() : r(new Error("PDF gen failed")));
    });

    // Seed state.json with a single paper that resolves to the PDF above.
    const paperKey = "E2EPAPER";
    const state = {
      schema_version: 2,
      zotero_library_version: 0,
      last_poll_timestamp: null,
      documents: {
        [paperKey]: {
          zotero_item_key: paperKey,
          zotero_attachment_key: "",
          title: "Sample paper body text for E2E.",
          status: "on_remarkable",
          authors: ["Heuler"],
          metadata: {
            citekey: "heuler_2026",
            publication_date: "2026-01-01",
            url: "",
          },
          uploaded_at: "2026-01-01T00:00:00Z",
        },
      },
      promoted_papers: [],
      projects: {},
    };
    writeFileSync(stateFile, JSON.stringify(state, null, 2));

    // Spawn the Python server on a fixed E2E port with DISTILLATE_STATE_BACKEND=json
    // so it reads our seeded state.json directly (no SQLite migration).
    const port = 8788;
    const env = {
      ...process.env,
      DISTILLATE_CONFIG_DIR: cfg,
      OBSIDIAN_VAULT_PATH: vault,
      OBSIDIAN_PAPERS_FOLDER: "Distillate/Papers",
      PDF_SUBFOLDER: "pdf",
      DISTILLATE_STATE_BACKEND: "json",
      ZOTERO_API_KEY: "",  // avoid Zotero calls
      ZOTERO_USER_ID: "",
      ANTHROPIC_API_KEY: "",
    };
    const proc = spawn(
      venvPython,
      ["-m", "distillate.server", String(port), "--no-open"],
      { env, cwd: repoRoot, stdio: "pipe" },
    );

    let serverErr = "";
    proc.stderr.on("data", (chunk) => { serverErr += chunk; });
    proc.stdout.on("data", () => {});  // drain

    try {
      await waitForServer(`http://127.0.0.1:${port}/status`);
    } catch (err) {
      proc.kill();
      throw new Error(
        `Python server didn't come up on port ${port}. stderr:\n${serverErr}`
      );
    }

    await use({ port, paperKey, vault, pdfPath, cfg });

    // Cleanup.
    proc.kill("SIGTERM");
    await new Promise((resolveP) => proc.on("exit", resolveP));
    try { rmSync(cfg, { recursive: true, force: true }); } catch {}
  }, { scope: "worker" }],
});

module.exports = { test, expect: require("@playwright/test").expect };
