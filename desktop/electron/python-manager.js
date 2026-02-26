const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

const DEFAULT_PORT = 8742;

class PythonManager {
  constructor() {
    this.process = null;
    this.port = DEFAULT_PORT;
  }

  /**
   * Find the Python executable.
   * In development: use the system `python3` or `python`.
   * In production: use the bundled venv.
   */
  _findPython() {
    const isDev = !process.resourcesPath || process.env.NODE_ENV === "development";

    if (isDev) {
      // Development: look for python3 in PATH (should have distillate installed)
      return "python3";
    }

    // Production: bundled venv
    const venvDir = path.join(process.resourcesPath, "python-env");
    if (process.platform === "win32") {
      return path.join(venvDir, "Scripts", "python.exe");
    }
    return path.join(venvDir, "bin", "python3");
  }

  /**
   * Start the Python WebSocket server. Returns the port number.
   */
  async start() {
    const python = this._findPython();

    this.process = spawn(python, ["-m", "distillate.server", String(this.port)], {
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env },
    });

    this.process.stdout.on("data", (data) => {
      console.log("[python]", data.toString().trim());
    });

    this.process.stderr.on("data", (data) => {
      console.error("[python]", data.toString().trim());
    });

    this.process.on("exit", (code) => {
      console.log(`[python] exited with code ${code}`);
      this.process = null;
    });

    // Wait for the server to be ready
    await this._waitForServer();
    return this.port;
  }

  /**
   * Poll the /status endpoint until the server responds.
   */
  _waitForServer(maxAttempts = 30, interval = 500) {
    return new Promise((resolve, reject) => {
      let attempts = 0;

      const check = () => {
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
            reject(new Error("Server failed to start (timeout)"));
          }
        });

        req.end();
      };

      // Give the server a moment to start before first check
      setTimeout(check, 1000);
    });
  }

  /**
   * Stop the Python server.
   */
  stop() {
    if (this.process) {
      this.process.kill("SIGTERM");
      this.process = null;
    }
  }
}

module.exports = { PythonManager };
