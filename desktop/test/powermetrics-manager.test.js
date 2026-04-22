// Covers: electron/powermetrics-manager.js
//
// Parser tests only — we do not exercise the real `sudo powermetrics` call
// (would need root and would be flaky in CI). The class's start/stop/stream
// wiring goes through a Mac-only privileged binary, so behavior is verified
// manually in the running app and through the pure functions here.

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const {
  parsePowermetricsSample,
  splitSamples,
} = require("../electron/powermetrics-manager.js");

// Minimal but realistic sample block — the real output is longer but these
// are the lines our parser pulls from.
const SAMPLE_BLOCK = `*** Sampled system activity (Sun Jan  7 12:00:00 2024 +0000) (2010.27ms elapsed)
**** Processor usage ****

CPU Power: 1234 mW
GPU Power: 567 mW
ANE Power: 89 mW
Combined Power (CPU + GPU + ANE): 1890 mW

**** GPU usage ****

GPU HW active frequency: 700 MHz
GPU HW active residency:  45.23% (444 MHz:   0% 612 MHz:  50%)
GPU idle residency:  54.77%
GPU Power: 567 mW
`;

describe("parsePowermetricsSample", () => {
  it("extracts GPU active residency as a percent", () => {
    const r = parsePowermetricsSample(SAMPLE_BLOCK);
    assert.equal(r.gpuActive, 45.23);
  });

  it("converts GPU / CPU / ANE power from mW to W", () => {
    const r = parsePowermetricsSample(SAMPLE_BLOCK);
    assert.equal(r.gpuWatts, 0.567);
    assert.equal(r.cpuWatts, 1.234);
    assert.equal(r.aneWatts, 0.089);
  });

  it("does not confuse the Combined Power line for GPU/CPU/ANE", () => {
    // Combined would be the largest number — if our regex matched it, the
    // individual totals would be wrong.
    const r = parsePowermetricsSample(SAMPLE_BLOCK);
    assert.notEqual(r.gpuWatts, 1.890);
    assert.notEqual(r.cpuWatts, 1.890);
  });

  it("returns null for missing fields rather than zero", () => {
    const r = parsePowermetricsSample("some preamble without metrics");
    assert.equal(r.gpuActive, null);
    assert.equal(r.gpuWatts, null);
    assert.equal(r.cpuWatts, null);
    assert.equal(r.aneWatts, null);
  });

  it("handles integer percents without a decimal point", () => {
    const r = parsePowermetricsSample("GPU HW active residency:  7% (...)\nGPU Power: 100 mW");
    assert.equal(r.gpuActive, 7);
    assert.equal(r.gpuWatts, 0.1);
  });

  it("tolerates non-string input", () => {
    assert.doesNotThrow(() => parsePowermetricsSample(null));
    assert.doesNotThrow(() => parsePowermetricsSample(undefined));
    const r = parsePowermetricsSample(null);
    assert.equal(r.gpuActive, null);
  });
});

describe("splitSamples", () => {
  it("emits one block per completed sample and keeps the tail as remainder", () => {
    const stream = SAMPLE_BLOCK + SAMPLE_BLOCK + "*** Sampled system activity (partial";
    const { samples, remainder } = splitSamples(stream);
    assert.equal(samples.length, 2);
    for (const s of samples) {
      assert.match(s, /^\*\*\* Sampled system activity/);
      assert.match(s, /GPU HW active residency/);
    }
    assert.match(remainder, /^\*\*\* Sampled system activity \(partial$/);
  });

  it("returns no samples when the buffer has no marker yet", () => {
    const { samples, remainder } = splitSamples("warming up...");
    assert.equal(samples.length, 0);
    assert.equal(remainder, "warming up...");
  });

  it("keeps partial output as remainder so the next chunk can complete it", () => {
    const partial = "*** Sampled system activity (Sun) CPU Power: 1";
    const { samples, remainder } = splitSamples(partial);
    assert.equal(samples.length, 0);
    assert.equal(remainder, partial);
  });
});
