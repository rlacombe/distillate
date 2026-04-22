# Covers: desktop/renderer/layout.js, desktop/renderer/index.html
"""Tests for papers sidebar visibility and layout state persistence.

Bug: the papers sidebar kept disappearing because:
1. index.html defaulted sidebar-right to class="collapsed"
2. saveLayoutState() wrote rightCollapsed:true to localStorage with no version
3. restoreLayoutState() re-applied the stale collapsed state on every reload
4. sessionStorage guard was unreliable in Electron (persists across reloads)

These tests validate the HTML defaults and JS logic to prevent regressions.
"""

import re
from pathlib import Path

RENDERER_DIR = Path(__file__).parent.parent / "desktop" / "renderer"
INDEX_HTML = RENDERER_DIR / "index.html"
LAYOUT_JS = RENDERER_DIR / "layout.js"


class TestPapersSidebarHTMLDefaults:
    """The papers sidebar must be accessible in the HTML."""

    def test_papers_activity_btn_exists_in_left_sidebar(self):
        """Papers must have an activity-bar button in the left sidebar."""
        html = INDEX_HTML.read_text()
        match = re.search(r'<button\s+class="[^"]*"[^>]*data-sidebar-view="papers"', html)
        assert match, (
            "Could not find papers activity-btn (data-sidebar-view='papers') in index.html. "
            "Papers must be accessible via the left sidebar."
        )

    def test_nicolas_activity_btn_active_by_default(self):
        """The Nicolas activity-bar button must have class 'active' as the default view."""
        html = INDEX_HTML.read_text()
        match = re.search(r'<button\s+class="([^"]*)"[^>]*data-sidebar-view="nicolas"', html)
        assert match, "Could not find Nicolas activity-btn in index.html"
        classes = match.group(1)
        assert "active" in classes, (
            "Nicolas activity-btn is missing 'active' class. "
            "Nicolas must be the default active view."
        )


class TestLayoutStatePersistenceJS:
    """The layout persistence logic in layout.js must handle migrations."""

    def _read_js(self):
        return LAYOUT_JS.read_text()

    def test_no_session_storage_guard(self):
        """Layout restore must NOT be gated behind sessionStorage.

        Electron's sessionStorage persists across page reloads within the same
        BrowserWindow, making the guard unreliable. restoreLayoutState() should
        always run — the version migration handles stale state instead.
        """
        js = self._read_js()
        # Look for the old pattern: if (sessionStorage...) { restoreLayoutState() }
        has_guard = re.search(
            r'if\s*\(\s*sessionStorage\.getItem.*\)\s*\{[^}]*restoreLayoutState',
            js,
        )
        assert not has_guard, (
            "restoreLayoutState is still gated behind a sessionStorage check. "
            "Remove the guard — Electron persists sessionStorage across reloads, "
            "making it unreliable for detecting fresh launches."
        )

    def test_restore_explicitly_opens_papers_sidebar(self):
        """When saved state says papers sidebar is open, restore must ensure it."""
        js = self._read_js()
        match = re.search(r'function restoreLayoutState\(\)\s*\{(.*?)\n\}', js, re.DOTALL)
        assert match, "Could not find restoreLayoutState function"
        body = match.group(1)
        # Must have an else branch that removes collapsed / adds active
        assert re.search(r'else\s*\{[^}]*classList\.remove\(["\']collapsed["\']\)', body), (
            "restoreLayoutState must explicitly remove 'collapsed' and add 'active' "
            "when the saved state says papers sidebar is open. Without this, the "
            "HTML default (if it somehow regresses) would win."
        )
