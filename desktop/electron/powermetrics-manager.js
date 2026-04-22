// Polls Apple Silicon hardware metrics via one-shot `powermetrics` calls.
// Each tick spawns a fresh `sudo -n powermetrics -n 1 ...` process that
// exits after a single sample, avoiding long-running privileged processes
// and the buffering quirks of streaming mode. Falls silent (emits
// `onUnavailable`) on non-Mac, denied sudo, or any spawn error.

const { spawn } = require("child_process");

// Parse one powermetrics text sample block and return the numbers the strip
// displays: GPU active residency %, GPU/CPU/ANE power in watts. Missing keys
// come back as null rather than 0 so callers can distinguish "not reported"
// from "zero power". Exported for unit tests.
function parsePowermetricsSample(text) {
  const result = { gpuActive: null, gpuWatts: null, cpuWatts: null, aneWatts: null };
  if (!text || typeof text !== "string") return result;

  const gpuActive = text.match(/GPU HW active residency:\s+([\d.]+)\s*%/);
  if (gpuActive) result.gpuActive = parseFloat(gpuActive[1]);

  // Power lines repeat as both a per-section header and the Combined line.
  // Match the first standalone occurrence of each (GPU Power / CPU Power /
  // ANE Power) — never the "Combined Power" line.
  const firstMw = (label) => {
    const re = new RegExp(`^${label} Power:\\s+(\\d+)\\s*mW`, "m");
    const m = text.match(re);
    return m ? parseInt(m[1], 10) / 1000 : null;
  };
  result.gpuWatts = firstMw("GPU");
  result.cpuWatts = firstMw("CPU");
  result.aneWatts = firstMw("ANE");
  return result;
}

// Legacy export retained for tests — the one-shot poller no longer needs
// to split a continuous stream, but the parser unit tests still exercise
// it to guard against regression.
function splitSamples(buffer) {
  const marker = "*** Sampled system activity";
  const parts = buffer.split(marker);
  if (parts.length <= 1) return { samples: [], remainder: buffer };
  const samples = [];
  for (let i = 1; i < parts.length - 1; i++) samples.push(marker + parts[i]);
  const remainder = marker + parts[parts.length - 1];
  return { samples, remainder };
}

const POLL_INTERVAL_MS = 2000;
// Time powermetrics is allowed to collect before we read its output.
// Matches typical asitop cadence; shorter = snappier UI, more sudo churn.
const SAMPLE_WINDOW_MS = 500;

class PowermetricsManager {
  constructor({ onSample, onUnavailable } = {}) {
    this._onSample = onSample || (() => {});
    this._onUnavailable = onUnavailable || (() => {});
    this._timer = null;
    this._proc = null;
    this._unavailable = false;
  }

  get running() { return !!this._timer; }

  start() {
    if (this._timer) return;
    if (this._unavailable) return;
    if (process.platform !== "darwin") {
      this._markUnavailable("not-darwin");
      return;
    }
    // Tick immediately, then on the interval — no 2s delay before first sample.
    this._tick();
    this._timer = setInterval(() => this._tick(), POLL_INTERVAL_MS);
  }

  stop() {
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
    if (this._proc) {
      try { this._proc.kill("SIGTERM"); } catch {}
      this._proc = null;
    }
  }

  _markUnavailable(reason) {
    if (this._unavailable) return;
    this._unavailable = true;
    // Keep this one warn — surfaces when someone configures sudo wrong.
    console.warn("[powermetrics] unavailable:", reason);
    this.stop();
    this._onUnavailable(reason);
  }

  _tick() {
    // Skip if the previous tick is still running — 500ms window means this
    // is rare, but a stuck process shouldn't pile up.
    if (this._proc) return;

    const args = [
      "-n", "/usr/bin/powermetrics",
      "--samplers", "gpu_power,cpu_power",
      "-i", String(SAMPLE_WINDOW_MS),
      "-n", "1",
      "-f", "text",
    ];

    let proc;
    try {
      proc = spawn("sudo", args, { stdio: ["ignore", "pipe", "pipe"] });
    } catch (err) {
      this._markUnavailable(err.message || "spawn-failed");
      return;
    }
    this._proc = proc;

    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (c) => { stdout += c.toString("utf8"); });
    proc.stderr.on("data", (c) => { stderr += c.toString("utf8"); });

    proc.on("error", (err) => {
      this._proc = null;
      this._markUnavailable(err.message || "proc-error");
    });

    proc.on("exit", (code) => {
      this._proc = null;
      if (code !== 0) {
        if (/password is required|sudo:|not permitted/i.test(stderr)) {
          this._markUnavailable("sudo-denied");
        } else {
          this._markUnavailable(`exit-${code}`);
        }
        return;
      }
      const parsed = parsePowermetricsSample(stdout);
      if (parsed.gpuActive !== null || parsed.gpuWatts !== null
          || parsed.cpuWatts !== null) {
        this._onSample(parsed);
      }
    });
  }
}

module.exports = { PowermetricsManager, parsePowermetricsSample, splitSamples };
