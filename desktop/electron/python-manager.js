const { spawn, execFile } = require("child_process");
const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const https = require("https");
const http = require("http");
const net = require("net");
const { app } = require("electron");

const DEFAULT_PORT = 8742;
const MAX_PORT_ATTEMPTS = 10;
const PYPI_PACKAGE = "distillate";
const PYPI_JSON_URL = `https://pypi.org/pypi/${PYPI_PACKAGE}/json`;
const VENV_DIR_NAME = "python-env";
const VERSION_FILE = "distillate-version.txt";

class PythonManager {
  constructor() {
    this.process = null;
    this.port = DEFAULT_PORT;
    this._stderr = "";
    this._exited = false;
    this._exitCode = null;
  }

  /* ───── Venv paths ───── */

  /** User-writable venv (survives app updates, pip-upgradeable). */
  _externalVenvDir() {
    return path.join(app.getPath("userData"), VENV_DIR_NAME);
  }

  /** Read-only bundled venv inside the signed .app (bootstrap seed). */
  _bundledVenvDir() {
    const isDev =
      !process.resourcesPath ||
      process.resourcesPath.includes("node_modules");
    if (isDev) return null;
    const dir = path.join(process.resourcesPath, VENV_DIR_NAME);
    return fs.existsSync(dir) ? dir : null;
  }

  _isDev() {
    return (
      !process.resourcesPath ||
      process.resourcesPath.includes("node_modules")
    );
  }

  /* ───── Version tracking ───── */

  _versionFilePath() {
    return path.join(app.getPath("userData"), VERSION_FILE);
  }

  async _installedVersion() {
    try {
      const ver = await fsp.readFile(this._versionFilePath(), "utf-8");
      return ver.trim();
    } catch {
      return null;
    }
  }

  async _writeInstalledVersion(ver) {
    await fsp.mkdir(app.getPath("userData"), { recursive: true });
    await fsp.writeFile(this._versionFilePath(), ver, "utf-8");
  }

  /** Compare X.Y.Z versions. Returns true if latest > current. */
  _isNewer(latest, current) {
    if (!latest || !current) return false;
    const a = latest.split(".").map(Number);
    const b = current.split(".").map(Number);
    for (let i = 0; i < 3; i++) {
      if ((a[i] || 0) > (b[i] || 0)) return true;
      if ((a[i] || 0) < (b[i] || 0)) return false;
    }
    return false;
  }

  /* ───── PyPI check ───── */

  /** Fetch latest stable version from PyPI. Returns null on any failure. */
  _fetchLatestPyPIVersion(timeoutMs = 5000) {
    return new Promise((resolve) => {
      const req = https.get(PYPI_JSON_URL, { timeout: timeoutMs }, (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          resolve(null);
          return;
        }
        let body = "";
        res.on("data", (chunk) => {
          body += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(body).info.version);
          } catch {
            resolve(null);
          }
        });
      });
      req.on("error", () => resolve(null));
      req.on("timeout", () => {
        req.destroy();
        resolve(null);
      });
    });
  }

  /* ───── Bootstrap & upgrade ───── */

  /** First launch: copy bundled venv to external user-writable location. */
  async _ensureExternalVenv(onProgress) {
    const extDir = this._externalVenvDir();
    const pythonBin =
      process.platform === "win32"
        ? path.join(extDir, "Scripts", "python.exe")
        : path.join(extDir, "bin", "python3");

    if (fs.existsSync(pythonBin)) return; // Already bootstrapped

    const bundled = this._bundledVenvDir();
    if (bundled) {
      onProgress({ phase: "bootstrap", message: "Setting up Python environment\u2026" });
      console.log(`[update] Copying bundled venv to ${extDir}`);
      await fsp.cp(bundled, extDir, { recursive: true });
      onProgress({ phase: "bootstrap", message: "Python environment ready." });
    } else {
      // No bundled venv — create fresh + install from PyPI
      onProgress({ phase: "bootstrap", message: "Installing Python environment\u2026" });
      await this._createFreshVenv(extDir, onProgress);
    }
  }

  /** Fallback: create a venv from system Python and pip install. */
  async _createFreshVenv(venvDir, onProgress) {
    const systemPython =
      process.platform === "win32" ? "python" : "python3";

    await new Promise((resolve, reject) => {
      execFile(
        systemPython,
        ["-m", "venv", venvDir],
        { timeout: 60000 },
        (err) => (err ? reject(new Error(`Failed to create venv: ${err.message}`)) : resolve()),
      );
    });

    const python =
      process.platform === "win32"
        ? path.join(venvDir, "Scripts", "python.exe")
        : path.join(venvDir, "bin", "python3");

    onProgress({ phase: "bootstrap", message: "Installing dependencies\u2026" });

    await new Promise((resolve, reject) => {
      const proc = spawn(
        python,
        ["-m", "pip", "install", "distillate[desktop]", "--quiet"],
        { stdio: ["ignore", "pipe", "pipe"], timeout: 300000 },
      );
      proc.on("exit", (code) =>
        code === 0
          ? resolve()
          : reject(new Error(`pip install failed (exit ${code})`)),
      );
      proc.on("error", reject);
    });
  }

  /** Run pip upgrade in the external venv. Returns true on success. */
  async _pipUpgrade(targetVersion, onProgress) {
    const extDir = this._externalVenvDir();
    const python =
      process.platform === "win32"
        ? path.join(extDir, "Scripts", "python.exe")
        : path.join(extDir, "bin", "python3");

    const spec = targetVersion
      ? `distillate[desktop]==${targetVersion}`
      : "distillate[desktop]";

    return new Promise((resolve) => {
      onProgress({
        phase: "update",
        message: `Updating to v${targetVersion}\u2026`,
      });
      console.log(`[update] pip install --upgrade ${spec}`);

      const proc = spawn(
        python,
        ["-m", "pip", "install", "--upgrade", spec, "--quiet"],
        { stdio: ["ignore", "pipe", "pipe"] },
      );

      let stderr = "";
      proc.stderr.on("data", (data) => {
        stderr += data.toString();
      });

      const timer = setTimeout(() => {
        proc.kill();
        console.error("[update] pip upgrade timed out");
        onProgress({ phase: "update", message: "Update timed out, using current version." });
        resolve(false);
      }, 120000);

      proc.on("exit", (code) => {
        clearTimeout(timer);
        if (code === 0) {
          console.log(`[update] Successfully upgraded to ${targetVersion}`);
          onProgress({ phase: "update", message: `Updated to v${targetVersion}.` });
          resolve(true);
        } else {
          console.error("[update] pip upgrade failed:", stderr);
          onProgress({ phase: "update", message: "Update failed, using current version." });
          resolve(false);
        }
      });

      proc.on("error", (err) => {
        clearTimeout(timer);
        console.error("[update] pip spawn error:", err);
        onProgress({ phase: "update", message: "Update failed, using current version." });
        resolve(false);
      });
    });
  }

  /* ───── Find Python ───── */

  _findPython() {
    if (this._isDev()) {
      // Development: project .venv
      const projectRoot = path.resolve(__dirname, "..", "..");
      if (process.platform === "win32") {
        const p = path.join(projectRoot, ".venv", "Scripts", "python.exe");
        if (fs.existsSync(p)) return p;
      } else {
        const p = path.join(projectRoot, ".venv", "bin", "python3");
        if (fs.existsSync(p)) return p;
      }
      return process.platform === "win32" ? "python" : "python3";
    }

    // Production: external user-writable venv
    const extDir = this._externalVenvDir();
    if (process.platform === "win32") {
      const p = path.join(extDir, "Scripts", "python.exe");
      if (fs.existsSync(p)) return p;
    } else {
      const p = path.join(extDir, "bin", "python3");
      if (fs.existsSync(p)) return p;
    }

    // Fallback: system Python
    return process.platform === "win32" ? "python" : "python3";
  }

  /* ───── Port discovery ───── */

  _isPortFree(port) {
    return new Promise((resolve) => {
      const server = net.createServer();
      server.once("error", () => resolve(false));
      server.once("listening", () => {
        server.close(() => resolve(true));
      });
      server.listen(port, "127.0.0.1");
    });
  }

  async _findFreePort() {
    for (let i = 0; i < MAX_PORT_ATTEMPTS; i++) {
      const port = DEFAULT_PORT + i;
      if (await this._isPortFree(port)) {
        return port;
      }
      console.log(`[python] port ${port} in use, trying ${port + 1}`);
    }
    throw new Error(
      `No free port found (tried ${DEFAULT_PORT}\u2013${DEFAULT_PORT + MAX_PORT_ATTEMPTS - 1})`,
    );
  }

  /* ───── Start server ───── */

  /**
   * Start the Python server.
   * In production, bootstraps the external venv and checks for updates first.
   * @param {Function} onProgress - callback({phase, message}) for UI updates
   * @returns {number} port
   */
  async start(onProgress = () => {}) {
    if (!this._isDev()) {
      // Step 1: Bootstrap external venv (first launch only)
      await this._ensureExternalVenv(onProgress);

      // Step 2: Check for updates
      onProgress({ phase: "update", message: "Checking for updates\u2026" });
      const [latestVersion, installedVersion] = await Promise.all([
        this._fetchLatestPyPIVersion(),
        this._installedVersion(),
      ]);

      if (latestVersion && this._isNewer(latestVersion, installedVersion)) {
        const upgraded = await this._pipUpgrade(latestVersion, onProgress);
        if (upgraded) {
          await this._writeInstalledVersion(latestVersion);
        }
      } else if (latestVersion && !installedVersion) {
        // First run after bootstrap — record the current version
        await this._writeInstalledVersion(latestVersion);
      }
    }

    // Step 3: Spawn the server
    const python = this._findPython();
    this.port = await this._findFreePort();

    this._stderr = "";
    this._exited = false;
    this._exitCode = null;

    onProgress({ phase: "starting", message: "Starting server\u2026" });

    this.process = spawn(python, ["-m", "distillate.server", String(this.port)], {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env },
    });

    this.process.stdout.on("data", (data) => {
      console.log("[python]", data.toString().trim());
    });

    this.process.stderr.on("data", (data) => {
      const text = data.toString().trim();
      console.error("[python]", text);
      this._stderr += text + "\n";
      if (this._stderr.length > 4096) {
        this._stderr = this._stderr.slice(-4096);
      }
    });

    this.process.on("exit", (code) => {
      console.log(`[python] exited with code ${code}`);
      this._exited = true;
      this._exitCode = code;
      this.process = null;
    });

    await this._waitForServer();
    return this.port;
  }

  /* ───── Wait for server ───── */

  _waitForServer(maxAttempts = 30, interval = 500) {
    return new Promise((resolve, reject) => {
      let attempts = 0;

      const check = () => {
        if (this._exited) {
          const detail = this._stderr.trim();
          const msg = detail
            ? `Python server crashed (exit code ${this._exitCode}):\n${detail}`
            : `Python server crashed (exit code ${this._exitCode})`;
          reject(new Error(msg));
          return;
        }

        attempts++;
        const req = http.get(
          `http://127.0.0.1:${this.port}/status`,
          (res) => {
            if (res.statusCode === 200) {
              resolve();
            } else if (attempts < maxAttempts) {
              setTimeout(check, interval);
            } else {
              reject(new Error("Server failed to start (bad status)"));
            }
          },
        );

        req.on("error", () => {
          if (attempts < maxAttempts) {
            setTimeout(check, interval);
          } else {
            const detail = this._stderr.trim();
            const msg = detail
              ? `Server failed to start (timeout). stderr:\n${detail}`
              : "Server failed to start (timeout)";
            reject(new Error(msg));
          }
        });

        req.end();
      };

      setTimeout(check, 1000);
    });
  }

  /* ───── Stop server ───── */

  stop() {
    return new Promise((resolve) => {
      if (!this.process) {
        resolve();
        return;
      }

      const proc = this.process;
      const killTimer = setTimeout(() => {
        try {
          proc.kill("SIGKILL");
        } catch (_) {
          // already dead
        }
        resolve();
      }, 3000);

      proc.once("exit", () => {
        clearTimeout(killTimer);
        this.process = null;
        resolve();
      });

      proc.kill("SIGTERM");
    });
  }
}

module.exports = { PythonManager };
