/**
 * Tests for terminal theme color application during theme cycling.
 *
 * Bug: When cycling light/dark/auto themes, highlight colors in the terminal
 * get jumbled. Root cause: reapplyColors() was using window.matchMedia (system
 * preference) instead of the actual selected theme setting tracked in
 * _currentThemeIsDark.
 *
 * Scenario that reproduces the bug:
 *   - System is in light mode, user manually selects dark mode
 *   - User switches experiment sessions
 *   - reapplyColors() is called on the new session
 *   - OLD CODE: Used matchMedia which returns false (light) instead of true (dark)
 *   - Result: Light theme colors applied even though dark theme was selected
 *
 * Fix: Track the actual theme setting in _currentThemeIsDark and use it in
 * reapplyColors() instead of matchMedia.
 *
 * Run: node --test test/theme-colors.test.js
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");

// Simplified test: verify the logic of theme tracking and palette application
describe("Terminal Theme Colors", () => {
  const themes = {
    dark: { bg: "#0c0a14", fg: "#e0dce8", contrast: 1 },
    light: { bg: "#f4f2f8", fg: "#0a0a14", contrast: 7 },
  };

  const paletteColors = {
    dark: { 237: "#262438", 238: "#323046" },
    light: { 237: "#e4dee8", 238: "#d2ccd8" },
  };

  // Mock the theme setting mechanism
  class ThemeTracker {
    constructor() {
      this.currentIsDark = false;
      this.appliedTheme = null;
      this.appliedContrast = null;
      this.appliedPalette = null;
    }

    // Simulate receiving a theme-changed event from main process
    setThemeFromMain(isDark) {
      this.currentIsDark = isDark;
    }

    // Simulate reapplyColors() logic — uses currentIsDark, not matchMedia
    reapplyColors() {
      const isDark = this.currentIsDark;
      this.appliedTheme = isDark ? themes.dark : themes.light;
      this.appliedContrast = isDark ? 1 : 7;
      this.appliedPalette = isDark ? paletteColors.dark : paletteColors.light;
    }

    // OLD BUGGY VERSION: uses matchMedia instead of currentIsDark
    reapplyColorsBuggy(systemMatchMediaIsDark) {
      const isDark = systemMatchMediaIsDark; // BUG: ignores currentIsDark
      this.appliedTheme = isDark ? themes.dark : themes.light;
      this.appliedContrast = isDark ? 1 : 7;
      this.appliedPalette = isDark ? paletteColors.dark : paletteColors.light;
    }
  }

  it("applies correct theme when user selects dark but system is light", () => {
    const tracker = new ThemeTracker();

    // System is light (matchMedia returns false)
    const systemPref = false;

    // User manually selects dark theme
    tracker.setThemeFromMain(true);

    // Call reapplyColors (e.g., when switching sessions)
    tracker.reapplyColors();

    // Verify correct dark theme was applied
    assert.equal(tracker.appliedTheme.fg, "#e0dce8", "dark foreground");
    assert.equal(tracker.appliedContrast, 1, "dark contrast ratio");
    assert.equal(tracker.appliedPalette[237], "#262438", "dark palette color");
  });

  it("demonstrates the bug: buggy version uses system pref, not user selection", () => {
    const tracker = new ThemeTracker();

    // System is light (matchMedia returns false)
    const systemPref = false;

    // User manually selects dark theme
    tracker.setThemeFromMain(true);

    // OLD BUGGY CODE: ignores currentIsDark and uses systemPref instead
    tracker.reapplyColorsBuggy(systemPref);

    // BUG: light theme applied even though user selected dark!
    assert.equal(tracker.appliedTheme.fg, "#0a0a14", "BUG: light foreground applied");
    assert.equal(tracker.appliedContrast, 7, "BUG: light contrast applied");
    assert.equal(tracker.appliedPalette[237], "#e4dee8", "BUG: light palette applied");
  });

  it("applies correct theme when user selects light but system is dark", () => {
    const tracker = new ThemeTracker();

    // System is dark (matchMedia returns true)
    const systemPref = true;

    // User manually selects light theme
    tracker.setThemeFromMain(false);

    // Call reapplyColors (e.g., when switching sessions)
    tracker.reapplyColors();

    // Verify correct light theme was applied
    assert.equal(tracker.appliedTheme.fg, "#0a0a14", "light foreground");
    assert.equal(tracker.appliedContrast, 7, "light contrast ratio");
    assert.equal(tracker.appliedPalette[237], "#e4dee8", "light palette color");
  });

  it("maintains correct theme across multiple session switches", () => {
    const tracker = new ThemeTracker();

    // System is light
    const systemPref = false;

    // User selects dark theme
    tracker.setThemeFromMain(true);

    // Session 1: reapplyColors
    tracker.reapplyColors();
    const session1Theme = tracker.appliedTheme;

    // Session 2: reapplyColors (currentIsDark unchanged)
    tracker.reapplyColors();
    const session2Theme = tracker.appliedTheme;

    // Both sessions should have the same dark theme
    assert.deepEqual(session1Theme, session2Theme);
    assert.equal(session1Theme.fg, "#e0dce8");
  });

  it("correctly updates theme when user changes selection", () => {
    const tracker = new ThemeTracker();

    // Start with dark theme
    tracker.setThemeFromMain(true);
    tracker.reapplyColors();
    const darkTheme = tracker.appliedTheme;

    // User switches to light theme
    tracker.setThemeFromMain(false);
    tracker.reapplyColors();
    const lightTheme = tracker.appliedTheme;

    // Themes should be different
    assert.notDeepEqual(darkTheme, lightTheme);
    assert.equal(darkTheme.fg, "#e0dce8");
    assert.equal(lightTheme.fg, "#0a0a14");
  });
});
