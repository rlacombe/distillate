/**
 * End-to-end tests for opening papers from the search bar.
 *
 * Covers: renderer/papers.js, renderer/layout.js
 *
 * Scope: boots the real Python server against a tmp config + seeded papers,
 * drives the UI in Chromium, and asserts on the DOM. Tests the critical
 * regression: clicking a paper in the search bar should open the paper detail,
 * not show the papers home view.
 */

const { test, expect } = require("./fixtures");


test("opens paper detail when clicked from search bar", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  await page.waitForLoadState("networkidle");

  // Open the search bar by pressing Cmd+K (or Ctrl+K on non-Mac)
  const isMac = process.platform === "darwin";
  await page.keyboard.press(isMac ? "Meta+K" : "Control+K");

  // Wait for the search input to appear
  const searchInput = page.locator("#resource-search-input");
  await expect(searchInput).toBeVisible({ timeout: 5000 });

  // Type paper name to search
  await searchInput.fill("E2E");

  // Wait for search results and click the paper
  const paperResult = page.locator('.resource-search-item[data-type="paper"]').first();
  await expect(paperResult).toBeVisible({ timeout: 5000 });
  await paperResult.click();

  // The search bar should close
  await expect(searchInput).not.toBeVisible({ timeout: 2000 });

  // Paper detail should be shown, not papers home
  // Check for paper detail headers/tabs
  const paperDetail = page.locator('.paper-detail-header-band');
  await expect(paperDetail).toBeVisible({ timeout: 5000 });

  // Check that the paper title is visible in the detail pane
  const paperTitle = page.locator('.exp-detail-title');
  await expect(paperTitle).toBeVisible();

  // The papers home page should NOT be visible
  // (papers-home would show "Papers" title + velocity/reading sections)
  const papersHomeTitle = page.locator('.papers-home-title');
  await expect(papersHomeTitle).not.toBeVisible();
});


test("search bar shows paper results", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  await page.waitForLoadState("networkidle");

  // Open search
  const isMac = process.platform === "darwin";
  await page.keyboard.press(isMac ? "Meta+K" : "Control+K");

  const searchInput = page.locator("#resource-search-input");
  await expect(searchInput).toBeVisible({ timeout: 5000 });

  // Search for the seeded paper
  await searchInput.fill("E2E");

  // Paper result should appear
  const paperResult = page.locator('.resource-search-item[data-type="paper"]');
  await expect(paperResult.first()).toBeVisible({ timeout: 5000 });
  await expect(paperResult).toHaveCount(1);

  // Result should show the paper key/title
  const resultName = page.locator('.resource-search-item[data-type="paper"] .resource-search-name');
  await expect(resultName).toContainText("E2EPAPER");
});


test("paper selection state is preserved when clicking from search", async ({ page, serverContext }) => {
  await page.goto(`http://127.0.0.1:${serverContext.port}/ui/`);
  await page.waitForLoadState("networkidle");

  // Open search and select a paper
  const isMac = process.platform === "darwin";
  await page.keyboard.press(isMac ? "Meta+K" : "Control+K");
  const searchInput = page.locator("#resource-search-input");
  await expect(searchInput).toBeVisible({ timeout: 5000 });

  await searchInput.fill("E2E");
  const paperResult = page.locator('.resource-search-item[data-type="paper"]').first();
  await expect(paperResult).toBeVisible({ timeout: 5000 });
  await paperResult.click();

  // Wait for paper detail to render
  const paperDetail = page.locator('.paper-detail-header-band');
  await expect(paperDetail).toBeVisible({ timeout: 5000 });

  // The sidebar item should be marked as active
  const activeItem = page.locator('.sidebar-item.active');
  await expect(activeItem).toHaveCount(1);
  await expect(activeItem).toHaveAttribute("data-key", serverContext.paperKey);
});
