const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const http = require("http");
const net = require("net");

const DEFAULT_PORT = 8742;
const MAX_PORT_ATTEMPTS = 10;

class PythonManager {
  constructor() {
    this.process = null;
    this.port = DEFAULT_PORT;
    this._stderr = "";
    this._exited = false;
    this._exitCode = null;
  }

  /**
   * Find the Python executable.
   * In development: use the project .venv (uv-managed).
   * In production: use the bundled venv.
   */
  _findPython() {
    // Production: bundled venv inside Electron resources
    const isDev =
      !process.resourcesPath ||
      process.resourcesPath.includes("node_modules");

    if (!isDev) {
      const venvDir = path.join(process.resourcesPath, "python-env");
      if (process.platform === "win32") {
        return path.join(venvDir, "Scripts", "python.exe");
      }
      return path.join(venvDir, "bin", "python3");
    }

    // Development: find the project .venv relative to this file
    // desktop/electron/python-manager.js → ../../.venv/bin/python3
    const projectRoot = path.resolve(__dirname, "..", "..");
    if (process.platform === "win32") {
      const winPython = path.join(projectRoot, ".venv", "Scripts", "python.exe");
      if (fs.existsSync(winPython)) {
        return winPython;
      }
    } else {
      const unixPython = path.join(projectRoot, ".venv", "bin", "python3");
      if (fs.existsSync(unixPython)) {
        return unixPython;
      }
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
      console.log(`[python] port ${port} in use, trying ${port + 1}`);
    }
    throw new Error(
      `No free port found (tried ${DEFAULT_PORT}–${DEFAULT_PORT + MAX_PORT_ATTEMPTS - 1})`
    );
  }

  /**
   * Start the Python WebSocket server. Returns the port number.
   */
  async start() {
    const python = this._findPython();
    this.port = await this._findFreePort();

    // Reset state
    this._stderr = "";
    this._exited = false;
    this._exitCode = null;

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
      // Keep last 4KB of stderr for error reporting
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
        resolve();
      });

      proc.kill("SIGTERM");
    });
  }
}

module.exports = { PythonManager };
