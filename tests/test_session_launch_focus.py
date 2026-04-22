# Covers: desktop/renderer/layout.js, desktop/renderer/index.html
"""Regression tests for session launch focus revert.

Bug: when the user launched a new agent session from a project, the terminal
came up for a moment and then the view reverted to the Project Workspace
("control-panel") view after a few seconds.

Flow:
  1. `launchCodingSession` POSTs the new session.
  2. It calls `selectWorkspace(workspaceId)` which synchronously sets
     `_selectedSession = null` and kicks off an async workspace-detail fetch.
  3. It schedules `attachToCodingSession` 500ms later to show the terminal.
  4. If the async workspace-detail fetch resolves *after* the 500ms attach,
     `renderWorkspaceDetail` unconditionally calls
     `switchEditorTab("control-panel")`, reverting focus away from the
     terminal.

These tests use static analysis of the JS source (same pattern as
`test_layout_persistence.py`) to lock in the two guardrails that fix the
race:

  - `renderWorkspaceDetail` must not switch the editor tab away when a
    session is currently selected.
  - `launchCodingSession` must claim the selection eagerly so that a stale
    workspace-detail fetch can see it.
"""

import re
from pathlib import Path

RENDERER_DIR = Path(__file__).parent.parent / "desktop" / "renderer"
PROJECTS_JS = RENDERER_DIR / "projects.js"


def _read_projects_js() -> str:
    return PROJECTS_JS.read_text()


def _extract_function(js: str, name: str) -> str:
    """Return the body of a top-level function by brace-matching.

    Handles both `function name(...) { ... }` and
    `async function name(...) { ... }` forms.
    """
    match = re.search(rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", js)
    assert match, f"Could not find function {name} in projects.js"
    start = match.end() - 1  # position of the opening `{`
    depth = 0
    for i in range(start, len(js)):
        c = js[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return js[start + 1 : i]
    raise AssertionError(f"Unbalanced braces while extracting {name}")


class TestRenderWorkspaceDetailGuardsSelectedSession:
    """`renderWorkspaceDetail` must not steal focus from an active session."""

    def test_control_panel_switch_checks_selected_session(self):
        """The `switchEditorTab('control-panel')` call must be gated on
        `_selectedSession` being falsy.

        Without this guard, a slow/stale workspace-detail fetch that resolves
        *after* the session terminal has been shown will revert the editor
        back to the control-panel view.
        """
        body = _extract_function(_read_projects_js(), "renderWorkspaceDetail")

        # Locate the `switchEditorTab("control-panel")` call.
        call_match = re.search(
            r'switchEditorTab\(\s*["\']control-panel["\']',
            body,
        )
        assert call_match, (
            "renderWorkspaceDetail no longer contains a "
            "switchEditorTab('control-panel') call — test is stale."
        )

        # The call lives inside a nested if — first the outer
        # `if (cpView && ... && !_selectedSession)` guard, then an inner
        # `if (typeof switchEditorTab === "function")`. We need the OUTER
        # guard's condition, so walk backward to the second-nearest `if (`.
        before = body[: call_match.start()]
        if_positions = [m.start() for m in re.finditer(r"\bif\s*\(", before)]
        assert len(if_positions) >= 2, (
            "Expected at least two enclosing `if (` statements before the "
            "switchEditorTab('control-panel') call (outer guard + inner "
            "typeof check). Found: "
            f"{len(if_positions)}. The outer guard is missing."
        )
        outer_if_idx = if_positions[-2]

        # Brace-match the outer condition, handling nested parens.
        paren_start = body.index("(", outer_if_idx)
        depth = 0
        end = None
        for i in range(paren_start, len(body)):
            c = body[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        assert end is not None, "Unbalanced parens in renderWorkspaceDetail if-guard"
        guard_expr = body[outer_if_idx : end + 1]

        assert "_selectedSession" in guard_expr, (
            "renderWorkspaceDetail's switchEditorTab('control-panel') call "
            "is not gated on _selectedSession. A stale workspace-detail fetch "
            "will revert focus away from a freshly-attached session terminal. "
            "The guard must include `!_selectedSession` (or equivalent) so "
            "that an active session keeps focus. Current outer guard:\n"
            f"  {guard_expr}"
        )


class TestLaunchCodingSessionClaimsSelectionEagerly:
    """`launchCodingSession` must set `_selectedSession` before the stale
    workspace-detail fetch can resolve and trample focus."""

    def test_selected_session_assigned_in_launch_flow(self):
        """`launchCodingSession` must assign `_selectedSession = { ... }`
        synchronously after calling `selectWorkspace` but before (or around)
        the `setTimeout` that attaches the terminal.

        `selectWorkspace` nulls `_selectedSession` synchronously, so the
        claim must happen *after* the `selectWorkspace` call site.
        """
        body = _extract_function(_read_projects_js(), "launchCodingSession")

        assert "_selectedSession" in body, (
            "launchCodingSession does not touch _selectedSession at all. "
            "It must eagerly claim the session as selected so that the "
            "async workspace-detail fetch kicked off inside selectWorkspace "
            "does not revert focus away from the terminal that is about to "
            "be attached."
        )

        # The assignment must be an object with workspaceId / sessionId /
        # tmuxName-shaped fields — matching the structure selectSession uses.
        assign_match = re.search(
            r"_selectedSession\s*=\s*\{[^}]*\}",
            body,
        )
        assert assign_match, (
            "launchCodingSession references _selectedSession but does not "
            "assign an object to it. The claim must be structural "
            "(`{ workspaceId, sessionId, tmuxName }`) so the rest of the "
            "renderer recognises the selection."
        )
        assigned = assign_match.group(0)
        assert "workspaceId" in assigned and "sessionId" in assigned, (
            "launchCodingSession assigns _selectedSession but the shape is "
            "wrong — it must include at least workspaceId and sessionId so "
            "it matches the format used elsewhere in projects.js."
        )

    def test_selection_claimed_after_select_workspace(self):
        """The eager claim must come *after* `selectWorkspace(...)` in the
        source order, because `selectWorkspace` synchronously resets
        `_selectedSession = null` before awaiting its fetch. If the claim
        came first, it would be immediately clobbered.
        """
        body = _extract_function(_read_projects_js(), "launchCodingSession")

        select_ws = re.search(r"selectWorkspace\s*\(", body)
        claim = re.search(r"_selectedSession\s*=\s*\{", body)
        assert select_ws and claim, (
            "launchCodingSession is missing either the selectWorkspace call "
            "or the _selectedSession claim."
        )
        assert claim.start() > select_ws.start(), (
            "In launchCodingSession, `_selectedSession = { ... }` must come "
            "AFTER `selectWorkspace(...)`. selectWorkspace synchronously "
            "sets _selectedSession = null on entry, so claiming the "
            "selection before that call would be immediately clobbered."
        )
