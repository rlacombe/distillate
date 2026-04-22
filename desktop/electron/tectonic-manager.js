/**
 * Tectonic LaTeX engine manager.
 *
 * Downloads the Tectonic binary on first use, caches it in Electron userData,
 * and shells out to compile .tex -> .pdf for the write-up editor.
 *
 * Mirrors the pattern in python-manager.js: one subprocess spawn per compile,
 * stdout/stderr piped, exit code checked. Log parsing turns the TeX log into
 * structured error objects for the editor's error panel.
 */
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const https = require("https");
const { app } = require("electron");

const TECTONIC_VERSION = "0.15.0";

/** Pinned release asset names (checked against GitHub release downloads). */
function _assetFor(platform, arch) {
  if (platform === "darwin" && arch === "arm64") {
    return `tectonic-${TECTONIC_VERSION}-aarch64-apple-darwin.tar.gz`;
  }
  if (platform === "darwin" && arch === "x64") {
    return `tectonic-${TECTONIC_VERSION}-x86_64-apple-darwin.tar.gz`;
  }
  if (platform === "linux" && arch === "x64") {
    return `tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz`;
  }
  if (platform === "win32" && arch === "x64") {
    return `tectonic-${TECTONIC_VERSION}-x86_64-pc-windows-msvc.zip`;
  }
  return null;
}

function _downloadUrl(asset) {
  const tag = encodeURIComponent(`tectonic@${TECTONIC_VERSION}`);
  return `https://github.com/tectonic-typesetting/tectonic/releases/download/${tag}/${asset}`;
}

class TectonicManager {
  constructor() {
    this._installPromise = null;
    this._compiles = new Map(); // `${wsId}::${wuId}` -> { proc }
  }

  _compileKey(wsId, wuId) {
    return `${wsId}::${wuId}`;
  }

  _binDir() {
    return path.join(app.getPath("userData"), "tectonic");
  }

  _binPath() {
    const name = process.platform === "win32" ? "tectonic.exe" : "tectonic";
    return path.join(this._binDir(), name);
  }

  /** Return the resolved path — respects TECTONIC_PATH override env. */
  resolvePath() {
    if (process.env.TECTONIC_PATH && fs.existsSync(process.env.TECTONIC_PATH)) {
      return process.env.TECTONIC_PATH;
    }
    return this._binPath();
  }

  status() {
    const p = this.resolvePath();
    return {
      installed: fs.existsSync(p),
      path: p,
      version: TECTONIC_VERSION,
    };
  }

  /**
   * Download and extract Tectonic. Idempotent: coalesces concurrent calls.
   * @param {(p: {phase, pct, msg}) => void} onProgress
   */
  async install(onProgress) {
    if (this.status().installed) return { ok: true, path: this.resolvePath() };
    if (this._installPromise) return this._installPromise;

    this._installPromise = (async () => {
      try {
        const asset = _assetFor(process.platform, process.arch);
        if (!asset) {
          return {
            ok: false,
            error: `Unsupported platform ${process.platform}/${process.arch}. Set TECTONIC_PATH to an existing tectonic binary.`,
          };
        }

        fs.mkdirSync(this._binDir(), { recursive: true });
        const archivePath = path.join(this._binDir(), asset);

        onProgress?.({ phase: "downloading", pct: 0, msg: "Downloading Tectonic…" });
        await _download(_downloadUrl(asset), archivePath, (pct) => {
          onProgress?.({ phase: "downloading", pct, msg: `Downloading Tectonic… ${pct}%` });
        });

        onProgress?.({ phase: "extracting", pct: 100, msg: "Extracting…" });
        await _extract(archivePath, this._binDir());

        const bin = this._binPath();
        if (!fs.existsSync(bin)) {
          return { ok: false, error: `Archive extracted but binary not found at ${bin}` };
        }
        if (process.platform !== "win32") {
          fs.chmodSync(bin, 0o755);
        }
        try { fs.unlinkSync(archivePath); } catch {}

        onProgress?.({ phase: "done", pct: 100, msg: "Tectonic ready" });
        return { ok: true, path: bin };
      } catch (err) {
        return { ok: false, error: err.message || String(err) };
      } finally {
        this._installPromise = null;
      }
    })();
    return this._installPromise;
  }

  /**
   * Compile <entry>.tex -> build/<entry>.pdf inside dir.
   * One compile per (wsId, wuId) at a time; a new compile while one is
   * running kills the current one (user kept typing — latest wins).
   */
  async compile(wsId, wuId, dir, entry = "main.tex") {
    const key = this._compileKey(wsId, wuId);
    const prev = this._compiles.get(key);
    if (prev?.proc && !prev.proc.killed) {
      try { prev.proc.kill("SIGTERM"); } catch {}
    }

    const bin = this.resolvePath();
    if (!fs.existsSync(bin)) {
      return {
        ok: false,
        exitCode: -1,
        errors: [{ file: entry, line: 0, message: "Tectonic not installed", severity: "error" }],
        stdout: "",
        stderr: "",
        durationMs: 0,
      };
    }

    const buildDir = path.join(dir, "build");
    fs.mkdirSync(buildDir, { recursive: true });

    const args = [
      "-X",
      "compile",
      "--synctex",
      "--keep-intermediates",
      "--outdir",
      "build",
      entry,
    ];

    const startedAt = Date.now();
    return await new Promise((resolve) => {
      let stdout = "";
      let stderr = "";
      const proc = spawn(bin, args, {
        cwd: dir,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env },
      });
      this._compiles.set(key, { proc });

      proc.stdout.on("data", (d) => { stdout += d.toString(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); });

      proc.on("error", (err) => {
        this._compiles.delete(key);
        resolve({
          ok: false,
          exitCode: -1,
          errors: [{ file: entry, line: 0, message: err.message, severity: "error" }],
          stdout, stderr,
          durationMs: Date.now() - startedAt,
        });
      });

      proc.on("exit", (code, signal) => {
        this._compiles.delete(key);
        const ok = code === 0;
        let logText = stderr;
        try {
          const logPath = path.join(buildDir, entry.replace(/\.tex$/, ".log"));
          if (fs.existsSync(logPath)) {
            logText = fs.readFileSync(logPath, "utf-8") + "\n" + stderr;
          }
        } catch {}
        const errors = parseTectonicLog(logText, entry);
        const pdfPath = path.join(buildDir, entry.replace(/\.tex$/, ".pdf"));
        resolve({
          ok: ok && fs.existsSync(pdfPath),
          exitCode: code ?? -1,
          signal: signal || null,
          pdfPath: fs.existsSync(pdfPath) ? pdfPath : null,
          errors,
          stdout, stderr,
          durationMs: Date.now() - startedAt,
        });
      });
    });
  }

  abort(wsId, wuId) {
    const key = this._compileKey(wsId, wuId);
    const cur = this._compiles.get(key);
    if (cur?.proc && !cur.proc.killed) {
      try { cur.proc.kill("SIGTERM"); } catch {}
    }
    this._compiles.delete(key);
  }
}

/**
 * Parse a Tectonic/XeTeX log into structured errors.
 *
 * The TeX log format is historical and weird. Errors look like:
 *   ! Undefined control sequence.
 *   l.42 \foo
 *          {hello}
 *
 * We match lines starting with "!" as error banners, then look ahead for the
 * nearest "l.<n>" marker. Warnings are "LaTeX Warning:" etc.
 */
function parseTectonicLog(logText, defaultFile) {
  const errors = [];
  if (!logText) return errors;
  const lines = logText.split("\n");

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Error banner
    if (line.startsWith("!")) {
      const message = line.replace(/^!\s*/, "").trim();
      let lineNum = 0;
      // Scan forward up to 10 lines for "l.<number>"
      for (let j = i + 1; j < Math.min(i + 10, lines.length); j++) {
        const m = /^l\.(\d+)/.exec(lines[j]);
        if (m) { lineNum = parseInt(m[1], 10); break; }
      }
      errors.push({
        file: defaultFile,
        line: lineNum,
        message,
        severity: "error",
      });
      continue;
    }

    // LaTeX Warning / Package Warning — only surface the useful ones
    const warn = /^(LaTeX|Package \w+|Class \w+) (Warning|Error):\s*(.+)$/.exec(line);
    if (warn) {
      const severity = warn[2].toLowerCase() === "error" ? "error" : "warning";
      // Warnings often have "on input line 42." suffix
      const lineMatch = /on input line (\d+)/.exec(line);
      errors.push({
        file: defaultFile,
        line: lineMatch ? parseInt(lineMatch[1], 10) : 0,
        message: warn[3].trim(),
        severity,
      });
    }
  }

  return errors;
}

/** Download to a file with simple redirect handling and progress callbacks. */
function _download(url, dest, onProgress) {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const req = https.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        file.close();
        fs.unlink(dest, () => {});
        _download(res.headers.location, dest, onProgress).then(resolve, reject);
        return;
      }
      if (res.statusCode !== 200) {
        file.close();
        fs.unlink(dest, () => {});
        reject(new Error(`HTTP ${res.statusCode} downloading ${url}`));
        return;
      }
      const total = parseInt(res.headers["content-length"] || "0", 10);
      let received = 0;
      let lastPct = -1;
      res.on("data", (chunk) => {
        received += chunk.length;
        if (total > 0) {
          const pct = Math.floor((received / total) * 100);
          if (pct !== lastPct) { lastPct = pct; onProgress?.(pct); }
        }
      });
      res.pipe(file);
      file.on("finish", () => file.close(() => resolve()));
    });
    req.on("error", (err) => {
      file.close();
      fs.unlink(dest, () => {});
      reject(err);
    });
  });
}

/** Extract a .tar.gz or .zip archive using the system tar/unzip. */
function _extract(archivePath, destDir) {
  return new Promise((resolve, reject) => {
    let cmd, args;
    if (archivePath.endsWith(".tar.gz") || archivePath.endsWith(".tgz")) {
      cmd = "tar";
      args = ["-xzf", archivePath, "-C", destDir];
    } else if (archivePath.endsWith(".zip")) {
      if (process.platform === "win32") {
        cmd = "powershell.exe";
        args = ["-NoProfile", "-Command", `Expand-Archive -Force -Path '${archivePath}' -DestinationPath '${destDir}'`];
      } else {
        cmd = "unzip";
        args = ["-o", archivePath, "-d", destDir];
      }
    } else {
      reject(new Error(`Unknown archive format: ${archivePath}`));
      return;
    }
    const proc = spawn(cmd, args, { stdio: "inherit" });
    proc.on("exit", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Extract failed (exit ${code})`));
    });
    proc.on("error", reject);
  });
}

module.exports = { TectonicManager, parseTectonicLog, TECTONIC_VERSION };
