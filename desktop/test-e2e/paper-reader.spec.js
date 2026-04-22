/**
 * End-to-end smoke tests for the in-app PDF reader.
 *
 * Scope: boots the real Python server against a tmp config + a single
 * seeded paper with a minimal PDF, drives the UI in Chromium, and
 * asserts on the DOM + network. Catches the class of bug the static /
 * jsdom suites can't — actual viewport + PDF.js + text layer rendering.
 */

const { test, expect } = require("./fixtures");


test("app loads and shows Papers sidebar", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  // Allow a moment for JS bootstrap.
  await page.waitForLoadState("networkidle");
  // The Papers activity-bar button exists (it's one of the fixed
  // sidebar tabs in index.html).
  const papersBtn = page.locator('[data-sidebar-view="papers"]');
  await expect(papersBtn).toHaveCount(1);
});


test("server /papers endpoint lists the seeded paper", async ({ page, serverContext }) => {
  // Sanity check — if this fails, the state-seeding fixture is the
  // problem, not the UI.
  const resp = await page.request.get(
    `http://127.0.0.1:${serverContext.port}/papers`,
  );
  expect(resp.status()).toBe(200);
  const body = await resp.json();
  expect(body.ok).toBe(true);
  const keys = body.papers.map((p) => p.key);
  expect(keys).toContain(serverContext.paperKey);
});


test("selecting a paper renders tabs (Overview + PDF)", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  await page.waitForLoadState("networkidle");

  // Switch to the Papers view.
  await page.locator('[data-sidebar-view="papers"]').click();

  // Click the seeded paper in the sidebar.
  const item = page.locator('.sidebar-item[data-key="E2EPAPER"]');
  await expect(item).toHaveCount(1, { timeout: 10_000 });
  await item.click();

  // Both tabs should exist.
  await expect(page.locator('.paper-tab-btn').filter({ hasText: 'Overview' })).toHaveCount(1);
  await expect(page.locator('.paper-tab-btn').filter({ hasText: 'PDF' })).toHaveCount(1);
});


test("PDF endpoint serves the seeded PDF bytes", async ({ page, serverContext }) => {
  // Direct check — verifies the server-side resolver finds the PDF
  // before we worry about PDF.js client-side rendering.
  const resp = await page.request.get(
    `http://127.0.0.1:${serverContext.port}/papers/${serverContext.paperKey}/pdf`,
  );
  expect(resp.status()).toBe(200);
  expect(resp.headers()["content-type"]).toContain("application/pdf");
  const bytes = await resp.body();
  expect(bytes.slice(0, 5).toString()).toBe("%PDF-");
});


test("PDF tab renders canvas pages from PyMuPDF-backed endpoint", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  await page.waitForLoadState("networkidle");
  await page.locator('[data-sidebar-view="papers"]').click();
  await page.waitForTimeout(1500);  // let papers home render + list populate
  await page.locator('.sidebar-item[data-key="E2EPAPER"]').click();
  await page.waitForTimeout(1000);  // let _renderPaperDetail finish

  await expect(page.locator('.paper-tab-bar')).toHaveCount(1, { timeout: 10_000 });

  await expect(page.locator('.paper-reader-page')).toHaveCount(
    1, { timeout: 25_000 }
  );
  await expect(page.locator('.paper-reader-page canvas')).toHaveCount(1);
  const canvasDim = await page.locator('.paper-reader-page canvas').evaluate(
    (c) => ({ w: c.width, h: c.height })
  );
  expect(canvasDim.w).toBeGreaterThan(100);
  expect(canvasDim.h).toBeGreaterThan(100);
});


test("selection menu appears with Highlight + Copy buttons", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  await page.waitForLoadState("networkidle");
  await page.locator('[data-sidebar-view="papers"]').click();
  await page.waitForTimeout(1500);
  await page.locator('.sidebar-item[data-key="E2EPAPER"]').click();
  await page.waitForTimeout(1000);
  await expect(page.locator('.paper-tab-bar')).toHaveCount(1, { timeout: 10_000 });

  const pdfTab = page.locator('.paper-tab-btn[data-tab="pdf"]');
  const isActive = await pdfTab.evaluate((el) => el.classList.contains("active"));
  if (!isActive) await pdfTab.click();

  // Wait for text layer spans to be present (PDF.js TextLayer rendered).
  await expect(page.locator('.paper-reader-textlayer > span')).toHaveCount(
    2, { timeout: 20_000 }
  );

  // Simulate a selection + fire the handler. We call the handler
  // directly because selectionchange is async / may be debounced.
  await page.evaluate(() => {
    const spans = document.querySelectorAll('.paper-reader-textlayer > span');
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(spans[0]);
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event("mouseup", { bubbles: true }));
  });

  // Menu should now be in the DOM with both buttons.
  await expect(page.locator('.paper-reader-select-menu')).toHaveCount(1, { timeout: 5_000 });
  await expect(
    page.locator('.paper-reader-select-menu .paper-reader-menu-btn.highlight')
  ).toHaveText('Highlight');
  await expect(
    page.locator('.paper-reader-select-menu .paper-reader-menu-btn.copy')
  ).toHaveText('Copy');
});
