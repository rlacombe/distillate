/**
 * Playwright config for Distillate desktop E2E tests.
 *
 * Architecture: the tests DO NOT launch Electron. Instead they launch
 * the Python server directly (the same one Electron spawns) against an
 * isolated tmp config dir, then drive the UI in a real Chromium page.
 * This gives us 95% of real-Electron fidelity without the fragility of
 * cross-process IPC + electron-specific Playwright integrations.
 *
 * Run: cd desktop && npx playwright test
 */

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./test-e2e",
  testMatch: /.*\.spec\.js$/,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  workers: 1,  // serialise — we share a single Python server
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8788",
    viewport: { width: 1280, height: 900 },
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  // The Python server is spawned by a fixture in test-e2e/fixtures.js
  // rather than a global webServer — that lets each spec control its
  // tmp config dir, env vars, and state fixtures.
});
