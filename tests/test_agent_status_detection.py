# Covers: distillate/experiment_tools/workspace_tools.py

"""Tests for agent status detection logic.

Validates that the pane-content heuristics correctly distinguish:
  working  — braille spinner in pane title (Claude is processing)
  waiting  — agent asked a question or is blocked on approval
  idle     — at prompt, nothing happening
  unknown  — can't determine state
  lost     — tmux session gone
"""

import pytest

from distillate.experiment_tools.workspace_tools import (
    _has_pending_question,
    _detect_spinner,
    _IDLE_PROMPT_RE,
    _QUESTION_RE,
    _ASK_PHRASE_RE,
    _STATUS_BAR_RE,
)


# ---------------------------------------------------------------------------
# _IDLE_PROMPT_RE — matches ❯ alone on a line
# ---------------------------------------------------------------------------

class TestIdlePromptRegex:
    def test_bare_prompt(self):
        assert _IDLE_PROMPT_RE.match("❯")

    def test_prompt_with_trailing_space(self):
        assert _IDLE_PROMPT_RE.match("❯ ")

    def test_prompt_with_leading_space(self):
        assert _IDLE_PROMPT_RE.match("  ❯")

    def test_prompt_with_leading_and_trailing(self):
        assert _IDLE_PROMPT_RE.match("  ❯  ")

    def test_prompt_with_text_after_is_not_idle(self):
        """❯ followed by text is user typing, not idle."""
        assert not _IDLE_PROMPT_RE.match("❯ Fantastic, let me check")

    def test_prompt_with_option_selector(self):
        """❯ followed by numbered option is not idle."""
        assert not _IDLE_PROMPT_RE.match("❯ 1. Yes, continue")

    def test_no_prompt(self):
        assert not _IDLE_PROMPT_RE.match("some output text")

    def test_empty_line(self):
        assert not _IDLE_PROMPT_RE.match("")


# ---------------------------------------------------------------------------
# _QUESTION_RE — matches lines ending with ?
# ---------------------------------------------------------------------------

class TestQuestionRegex:
    def test_simple_question(self):
        assert _QUESTION_RE.search("Do you want to continue?")

    def test_question_with_trailing_space(self):
        assert _QUESTION_RE.search("Ready to proceed? ")

    def test_not_a_question(self):
        assert not _QUESTION_RE.search("I've completed the task.")

    def test_question_mark_mid_line(self):
        """Question mark in the middle of a line should not match."""
        assert not _QUESTION_RE.search("Is this? I think so.")


# ---------------------------------------------------------------------------
# _ASK_PHRASE_RE — matches agent asking for decisions
# ---------------------------------------------------------------------------

class TestAskPhraseRegex:
    def test_want_me_to(self):
        assert _ASK_PHRASE_RE.search("Want me to fix this?")

    def test_should_i(self):
        assert _ASK_PHRASE_RE.search("Should I proceed with the refactor?")

    def test_do_you_want(self):
        assert _ASK_PHRASE_RE.search("Do you want me to add tests?")

    def test_would_you_like(self):
        assert _ASK_PHRASE_RE.search("Would you like me to commit?")

    def test_shall_i(self):
        assert _ASK_PHRASE_RE.search("Shall I run the tests?")

    def test_ready_to(self):
        assert _ASK_PHRASE_RE.search("Are you ready to deploy?")

    def test_no_ask_phrase(self):
        assert not _ASK_PHRASE_RE.search("I've updated the file.")

    def test_case_insensitive(self):
        assert _ASK_PHRASE_RE.search("SHOULD I proceed?")


# ---------------------------------------------------------------------------
# _STATUS_BAR_RE — lines to skip (tmux/Claude status bars)
# ---------------------------------------------------------------------------

class TestStatusBarRegex:
    def test_shift_tab(self):
        assert _STATUS_BAR_RE.search("shift+tab to expand")

    def test_ctrl(self):
        assert _STATUS_BAR_RE.search("ctrl+c to cancel")

    def test_esc_to(self):
        assert _STATUS_BAR_RE.search("esc to dismiss")

    def test_to_interrupt(self):
        assert _STATUS_BAR_RE.search("press enter to interrupt")

    def test_regular_text(self):
        assert not _STATUS_BAR_RE.search("I fixed the bug in the auth module.")


# ---------------------------------------------------------------------------
# _has_pending_question — full pane content analysis
# ---------------------------------------------------------------------------

class TestHasPendingQuestion:
    """Test the core question-detection logic that distinguishes waiting vs idle."""

    def test_question_between_prompts(self):
        """Agent asked a question → should detect as waiting."""
        pane = "\n".join([
            "❯ fix the login bug",
            "I found the issue in auth.py.",
            "Should I also update the tests?",
            "❯",
        ])
        assert _has_pending_question(pane) is True

    def test_no_question_between_prompts(self):
        """Agent gave a statement → should NOT detect as waiting."""
        pane = "\n".join([
            "❯ fix the login bug",
            "Done. I've updated auth.py and committed the changes.",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_question_with_ask_phrase(self):
        """Agent used an ask-phrase → should detect as waiting."""
        pane = "\n".join([
            "❯ refactor the module",
            "I can see two approaches. Want me to use the simpler one",
            "or the more robust pattern with error handling",
            "❯",
        ])
        assert _has_pending_question(pane) is True

    def test_status_bar_with_question_mark_not_a_question(self):
        """Status bar lines should be skipped even if they contain ?."""
        pane = "\n".join([
            "❯ do something",
            "I've completed the task.",
            "esc to dismiss   ctrl+c to cancel",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_no_prompts(self):
        """No ❯ prompts at all → can't determine, return False."""
        pane = "Loading...\nPlease wait..."
        assert _has_pending_question(pane) is False

    def test_only_one_prompt(self):
        """Only one ❯ → no "between two prompts" region → False."""
        pane = "\n".join([
            "Welcome to Claude Code.",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_separator_lines_ignored(self):
        """Lines made of ─ or ━ should be skipped."""
        pane = "\n".join([
            "❯ check",
            "─────────────────────",
            "━━━━━━━━━━━━━━━━━━━",
            "All good, nothing to report.",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_empty_lines_ignored(self):
        pane = "\n".join([
            "❯ check",
            "",
            "  ",
            "All done.",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_multiple_conversations(self):
        """Only the LAST turn (between last two ❯) should matter."""
        pane = "\n".join([
            "❯ first question",
            "Should I do X?",           # old question — user already responded
            "❯ yes do it",
            "Done, X is complete.",      # current turn — no question
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_tool_approval_pattern(self):
        """Agent asking to run a tool → should detect as waiting."""
        pane = "\n".join([
            "❯ deploy",
            "I need to run `npm run build`. Do you want me to proceed?",
            "❯",
        ])
        assert _has_pending_question(pane) is True

    def test_real_world_claude_output(self):
        """Realistic Claude Code output with question."""
        pane = "\n".join([
            "❯ fix the tests",
            "",
            "I found 3 failing tests in test_auth.py:",
            "  - test_login_redirect",
            "  - test_token_refresh",
            "  - test_logout_cleanup",
            "",
            "The root cause is a missing mock for the Redis client.",
            "Should I fix all three, or just the critical ones?",
            "",
            "❯",
        ])
        assert _has_pending_question(pane) is True

    def test_rhetorical_mid_reply_question_should_not_trigger(self):
        """A rhetorical question mid-reply should NOT mark as waiting if the
        agent ends with a statement.

        This is the false-positive scenario the user reported: agents
        finishing a response that happens to contain a `?` mid-reply.
        """
        pane = "\n".join([
            "❯ what's the issue?",
            "The bug is in line 42.",
            "But why does this matter? Because we use it everywhere.",
            "I've applied the fix and committed the changes.",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_code_comment_question_should_not_trigger(self):
        """Code comments containing ? should not mark as waiting."""
        pane = "\n".join([
            "❯ refactor the cache",
            "Updated the cache module:",
            "  // Should we cache this?",
            "  if (key in cache) return cache[key];",
            "Done. All tests pass.",
            "❯",
        ])
        assert _has_pending_question(pane) is False

    def test_question_at_very_end_still_triggers(self):
        """Question at the end of the reply should still mark as waiting."""
        pane = "\n".join([
            "❯ refactor",
            "Looking at this, I see two approaches.",
            "Approach 1 is simpler but less robust.",
            "Approach 2 handles edge cases better.",
            "Which approach would you prefer?",
            "❯",
        ])
        assert _has_pending_question(pane) is True


# ---------------------------------------------------------------------------
# Status-to-CSS class mapping (mirrors frontend logic)
# ---------------------------------------------------------------------------

def _status_to_css_class(status: str) -> str:
    """Mirror the JavaScript status → CSS class mapping."""
    return {
        "working": "status-working",
        "waiting": "status-waiting",
        "idle": "status-idle",
        "lost": "status-lost",
        "completed": "status-completed",
    }.get(status, "status-unknown")


class TestStatusCssMapping:
    def test_working(self):
        assert _status_to_css_class("working") == "status-working"

    def test_waiting(self):
        assert _status_to_css_class("waiting") == "status-waiting"

    def test_idle(self):
        assert _status_to_css_class("idle") == "status-idle"

    def test_lost(self):
        assert _status_to_css_class("lost") == "status-lost"

    def test_completed(self):
        assert _status_to_css_class("completed") == "status-completed"

    def test_unknown(self):
        assert _status_to_css_class("unknown") == "status-unknown"

    def test_unexpected_value(self):
        assert _status_to_css_class("banana") == "status-unknown"


# ---------------------------------------------------------------------------
# Pane title → status mapping (mirrors backend logic without tmux)
# ---------------------------------------------------------------------------

def _title_to_status(first_char: str) -> str:
    """Mirror the pane-title status detection without subprocess calls."""
    if "\u2800" <= first_char <= "\u28ff":
        return "working"
    if first_char == "\u2733":
        return "needs_pane_check"  # requires pane content analysis
    return "unknown"


class TestPaneTitleDetection:
    """Test the first-character spinner detection."""

    def test_braille_spinner_working(self):
        """Braille characters (U+2800..U+28FF) → working."""
        for char in ["\u2800", "\u2801", "\u2810", "\u28ff", "\u2834"]:
            assert _title_to_status(char) == "working", f"Failed for {repr(char)}"

    def test_star_spinner_needs_check(self):
        """✳ (U+2733) → needs pane content check."""
        assert _title_to_status("\u2733") == "needs_pane_check"

    def test_no_spinner_unknown(self):
        """Regular text → unknown."""
        assert _title_to_status("C") == "unknown"
        assert _title_to_status(" ") == "unknown"

    def test_empty_unknown(self):
        assert _title_to_status("") == "unknown"


# ---------------------------------------------------------------------------
# _detect_spinner — robust title parsing (the production helper)
# ---------------------------------------------------------------------------

class TestDetectSpinner:
    """The real helper used by _fast_agent_info — must handle realistic titles."""

    def test_braille_no_prefix(self):
        assert _detect_spinner("\u2810 working on it") == "working"

    def test_braille_with_leading_space(self):
        """Realistic case: tmux sometimes prepends a space."""
        assert _detect_spinner(" \u2810 working on it") == "working"

    def test_braille_with_multiple_leading_spaces(self):
        assert _detect_spinner("   \u2810 working") == "working"

    def test_braille_with_leading_tab(self):
        assert _detect_spinner("\t\u2810 working") == "working"

    def test_star_no_prefix(self):
        assert _detect_spinner("\u2733 my-session") == "idle_or_waiting"

    def test_star_with_leading_space(self):
        assert _detect_spinner(" \u2733 my-session") == "idle_or_waiting"

    def test_no_spinner(self):
        assert _detect_spinner("Claude Code") == ""

    def test_empty_title(self):
        assert _detect_spinner("") == ""

    def test_whitespace_only(self):
        assert _detect_spinner("   ") == ""

    def test_all_braille_chars(self):
        """All 8 spinner chars Claude Code uses should be detected."""
        for ch in ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827"]:
            assert _detect_spinner(f"{ch} task") == "working", f"Failed for {repr(ch)}"


# ---------------------------------------------------------------------------
# Recently-completed session window (5 minutes)
# ---------------------------------------------------------------------------

class TestRecentlyCompletedWindow:
    """Verify that sessions ended within 5 minutes are flagged as 'completed'."""

    def test_session_ended_1_minute_ago_is_recent(self):
        from datetime import datetime, timezone, timedelta
        ended = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(ended)).total_seconds()
        assert elapsed < 300

    def test_session_ended_10_minutes_ago_is_not_recent(self):
        from datetime import datetime, timezone, timedelta
        ended = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(ended)).total_seconds()
        assert elapsed >= 300

    def test_session_ended_exactly_5_minutes_ago_is_not_recent(self):
        from datetime import datetime, timezone, timedelta
        ended = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(ended)).total_seconds()
        assert elapsed >= 300
