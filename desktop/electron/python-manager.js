const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");
const net = require("net");
const { app } = require("electron");

const DEFAULT_PORT = 8742;
const MAX_PORT_ATTEMPTS = 10;
const MAX_LOG_SIZE = 1024 * 1024; // 1 MB

class PythonManager {
  constructor() {
    this.process = null;
    this.port = DEFAULT_PORT;
    this._stderr = "";
    this._exited = false;
    this._exitCode = null;
    this._logStream = null;
    this._initLogFile();
  }

  /**
   * Initialize persistent log file in Electron's logs directory.
   */
  _initLogFile() {
    try {
      const logPath = this._logFilePath();
      const dir = path.dirname(logPath);
      fs.mkdirSync(dir, { recursive: true });

      // Auto-rotate: truncate if > 1 MB
      try {
        const stat = fs.statSync(logPath);
        if (stat.size > MAX_LOG_SIZE) {
          fs.writeFileSync(logPath, "");
        }
      } catch {
        // File doesn't exist yet, fine
      }

      this._logStream = fs.createWriteStream(logPath, { flags: "a" });
    } catch {
      // Non-critical — fall back to console only
    }
  }

  _logFilePath() {
    return path.join(app.getPath("logs"), "distillate.log");
  }

  /**
   * Log to both console and persistent log file.
   */
  _log(level, tag, message) {
    const ts = new Date().toISOString();
    const line = `${ts} [${level}] [${tag}] ${message}`;

    try {
      if (level === "error") {
        console.error(`[${tag}]`, message);
      } else {
        console.log(`[${tag}]`, message);
      }
    } catch {
      // EPIPE — stdout/stderr closed during shutdown
    }

    if (this._logStream) {
      try {
        this._logStream.write(line + "\n");
      } catch {
        // Log stream closed
      }
    }
  }

  /**
   * Whether we're running in a packaged Electron app (vs dev).
   */
  _isProd() {
    return (
      process.resourcesPath &&
      !process.resourcesPath.includes("node_modules")
    );
  }

  /**
   * External (writable) venv directory in userData.
   * Used in production so we can pip-upgrade without touching the read-only app bundle.
   */
  _externalVenvDir() {
    return path.join(app.getPath("userData"), "python-env");
  }

  /**
   * Bundled (read-only) venv shipped inside the app resources.
   */
  _bundledVenvDir() {
    return path.join(process.resourcesPath, "python-env");
  }

  /**
   * Read the installed distillate version from userData.
   * Returns null if the file doesn't exist.
   */
  _installedVersion() {
    const versionFile = path.join(
      app.getPath("userData"),
      "distillate-version.txt"
    );
    try {
      return fs.readFileSync(versionFile, "utf-8").trim();
    } catch (_) {
      return null;
    }
  }

  /**
   * Write the installed version after a successful upgrade.
   */
  _writeInstalledVersion(version) {
    const versionFile = path.join(
      app.getPath("userData"),
      "distillate-version.txt"
    );
    fs.writeFileSync(versionFile, version, "utf-8");
  }

  /**
   * Fetch the latest distillate version from PyPI.
   * Returns the version string, or null on failure.
   */
  _fetchLatestPyPIVersion() {
    return new Promise((resolve) => {
      const req = https.get(
        "https://pypi.org/pypi/distillate/json",
        { timeout: 5000 },
        (res) => {
          let data = "";
          res.on("data", (chunk) => (data += chunk));
          res.on("end", () => {
            try {
              const json = JSON.parse(data);
              resolve(json.info.version);
            } catch (_) {
              resolve(null);
            }
          });
        }
      );
      req.on("error", () => resolve(null));
      req.on("timeout", () => {
        req.destroy();
        resolve(null);
      });
    });
  }

  /**
   * Compare two semver strings numerically.
   * Returns true if `latest` is newer than `current`.
   */
  _isNewer(latest, current) {
    if (!latest || !current) return false;
    const parse = (v) => v.split(".").map(Number);
    const [lMaj, lMin, lPatch] = parse(latest);
    const [cMaj, cMin, cPatch] = parse(current);
    if (lMaj !== cMaj) return lMaj > cMaj;
    if (lMin !== cMin) return lMin > cMin;
    return lPatch > cPatch;
  }

  /**
   * Copy the bundled venv to the external (writable) location on first launch.
   */
  _ensureExternalVenv(onProgress) {
    const ext = this._externalVenvDir();
    if (fs.existsSync(ext)) return;

    const bundled = this._bundledVenvDir();
    if (!fs.existsSync(bundled)) {
      throw new Error("Bundled Python environment not found");
    }

    if (onProgress) onProgress("Setting up Python environment...");
    this._log("info", "python", `copying bundled venv to ${ext}`);
    fs.cpSync(bundled, ext, { recursive: true });

    // Record the bundled version
    this._writeInstalledVersion(app.getVersion());
    this._log("info", "python", "external venv ready");
  }

  /**
   * Upgrade distillate via pip in the external venv.
   */
  _pipUpgrade(version, onProgress) {
    return new Promise((resolve, reject) => {
      const python = this._pythonInVenv(this._externalVenvDir());
      if (onProgress) onProgress(`Updating to v${version}...`);
      this._log("info", "python", `upgrading distillate to ${version}`);

      const proc = spawn(
        python,
        [
          "-m",
          "pip",
          "install",
          "--upgrade",
          `distillate[desktop]==${version}`,
          "--quiet",
        ],
        {
          stdio: ["ignore", "pipe", "pipe"],
          timeout: 120000,
        }
      );

      let stderr = "";
      proc.stderr.on("data", (d) => (stderr += d.toString()));
      proc.stdout.on("data", (d) =>
        this._log("info", "pip", d.toString().trim())
      );

      proc.on("exit", (code) => {
        if (code === 0) {
          this._log("info", "python", "upgrade complete");
          resolve();
        } else {
          this._log("error", "python", `upgrade failed: ${stderr}`);
          reject(new Error(`pip install failed (exit ${code}): ${stderr}`));
        }
      });

      proc.on("error", (err) => reject(err));
    });
  }

  /**
   * Check the actually installed distillate version via pip show.
   * Returns the version string, or null on failure.
   */
  _checkInstalledVersion() {
    return new Promise((resolve) => {
      const python = this._pythonInVenv(this._externalVenvDir());
      const proc = spawn(python, ["-m", "pip", "show", "distillate"], {
        stdio: ["ignore", "pipe", "pipe"],
        timeout: 15000,
      });

      let stdout = "";
      proc.stdout.on("data", (d) => (stdout += d.toString()));
      proc.on("exit", (code) => {
        if (code !== 0) { resolve(null); return; }
        const match = stdout.match(/^Version:\s*(.+)$/m);
        resolve(match ? match[1].trim() : null);
      });
      proc.on("error", () => resolve(null));
    });
  }

  /**
   * Get the python binary path inside a given venv directory.
   */
  _pythonInVenv(venvDir) {
    if (process.platform === "win32") {
      return path.join(venvDir, "Scripts", "python.exe");
    }
    return path.join(venvDir, "bin", "python3");
  }

  /**
   * Find the Python executable.
   * Dev: project .venv
   * Prod: external (writable) venv in userData
   * Fallback: system python3
   */
  _findPython() {
    if (this._isProd()) {
      // Production: use the external writable venv
      return this._pythonInVenv(this._externalVenvDir());
    }

    // Development: find the project .venv relative to this file
    // desktop/electron/python-manager.js -> ../../.venv/bin/python3
    const projectRoot = path.resolve(__dirname, "..", "..");
    const venvPython = this._pythonInVenv(
      path.join(projectRoot, ".venv")
    );
    if (fs.existsSync(venvPython)) {
      return venvPython;
    }

    // Fallback: hope python3 in PATH has distillate installed
    return process.platform === "win32" ? "python" : "python3";
  }

  /**
   * Check if a port is available.
   */
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

  /**
   * Find a free port starting from DEFAULT_PORT.
   */
  async _findFreePort() {
    for (let i = 0; i < MAX_PORT_ATTEMPTS; i++) {
      const port = DEFAULT_PORT + i;
      if (await this._isPortFree(port)) {
        return port;
      }
      this._log("info", "python", `port ${port} in use, trying ${port + 1}`);
    }
    throw new Error(
      `No free port found (tried ${DEFAULT_PORT}\u2013${DEFAULT_PORT + MAX_PORT_ATTEMPTS - 1})`
    );
  }

  /**
   * Start the Python WebSocket server. Returns the port number.
   * In production: ensures external venv exists, checks for updates, then spawns.
   * @param {Function} [onProgress] - callback for progress messages
   */
  async start(onProgress) {
    // Production: set up writable venv and check for updates
    if (this._isProd()) {
      // 1. Copy bundled venv on first launch
      this._ensureExternalVenv(onProgress);

      // 2. Check PyPI for updates (non-blocking on failure)
      try {
        if (onProgress) onProgress("Checking for updates...");
        const latest = await this._fetchLatestPyPIVersion();
        const current = this._installedVersion();
        this._log("info", "python", `installed: ${current}, latest: ${latest}`);

        if (this._isNewer(latest, current)) {
          try {
            await this._pipUpgrade(latest, onProgress);
            // Validate the upgrade
            const actual = await this._checkInstalledVersion();
            if (actual && actual !== latest) {
              this._log("error", "python", `upgrade verification failed: expected ${latest}, got ${actual}`);
            }
            this._writeInstalledVersion(latest);
          } catch (err) {
            // Upgrade failed — continue with current version
            this._log("error", "python", `upgrade failed, continuing: ${err.message}`);
          }
        }
      } catch (err) {
        // Network/parse error — skip update, launch current version
        this._log("error", "python", `update check failed: ${err.message}`);
      }
    }

    if (onProgress) onProgress("Starting server...");

    const python = this._findPython();
    this.port = await this._findFreePort();

    // Reset state
    this._stderr = "";
    this._exited = false;
    this._exitCode = null;

    // Electron apps launched from Finder get a minimal PATH that misses
    // /usr/local/bin, Homebrew, etc.  Ensure common tool locations are present
    // so rmapi, git, and other CLIs are found by the Python server.
    const extraPaths = [
      "/usr/local/bin",
      "/opt/homebrew/bin",
      path.join(process.env.HOME || "", ".local", "bin"),
    ];
    const currentPath = process.env.PATH || "/usr/bin:/bin";
    const fullPath = [...new Set([...extraPaths, ...currentPath.split(":")])].join(":");

    this.process = spawn(python, ["-m", "distillate.server", String(this.port)], {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, PATH: fullPath },
    });

    this.process.stdout.on("data", (data) => {
      this._log("info", "python", data.toString().trim());
    });

    this.process.stderr.on("data", (data) => {
      const text = data.toString().trim();
      this._log("error", "python", text);
      // Keep last 4KB of stderr for error reporting
      this._stderr += text + "\n";
      if (this._stderr.length > 4096) {
        this._stderr = this._stderr.slice(-4096);
      }
    });

    this.process.on("exit", (code) => {
      this._log("info", "python", `exited with code ${code}`);
      this._exited = true;
      this._exitCode = code;
      this.process = null;
    });

    // Wait for the server to be ready
    await this._waitForServer();
    return this.port;
  }

  /**
   * Poll the /status endpoint until the server responds.
   * Detects early crashes and surfaces stderr in the error.
   */
  _waitForServer(maxAttempts = 30, interval = 500) {
    return new Promise((resolve, reject) => {
      let attempts = 0;

      const check = () => {
        // Detect early crash: process exited before server was ready
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

      // Give the server a moment to start before first check
      setTimeout(check, 1000);
    });
  }

  /**
   * Stop the Python server and wait for exit.
   * Returns a promise that resolves when the process is fully stopped.
   */
  stop() {
    return new Promise((resolve) => {
      if (!this.process) {
        resolve();
        return;
      }

      const proc = this.process;
      const killTimer = setTimeout(() => {
        // Force kill if SIGTERM didn't work after 3s
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
        if (this._logStream) {
          this._logStream.end();
          this._logStream = null;
        }
        resolve();
      });

      proc.kill("SIGTERM");
    });
  }
}

module.exports = { PythonManager };
