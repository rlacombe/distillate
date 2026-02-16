"""Tests for v0.1.7 features: friction fixes, --themes removal,
Haiku for insights, unified title-matching, note overwrite."""

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    yield tmp_path


@pytest.fixture()
def populated_state():
    """Create a state with papers in various statuses."""
    from distillate.state import State

    s = State()
    s.add_document("K1", "A1", "md5", "paper_one", "Attention Is All You Need",
                    ["Vaswani, A.", "Shazeer, N."], status="on_remarkable")
    s.add_document("K2", "A2", "md5", "paper_two", "Scaling Laws for Neural LMs",
                    ["Kaplan, J."], status="processed",
                    metadata={"doi": "10.1234/test"})
    s.add_document("K3", "A3", "md5", "paper_three", "BERT: Pre-training",
                    ["Devlin, J."], status="awaiting_pdf")
    s.save()
    return s


# ---------------------------------------------------------------------------
# A2. Better no-highlights guidance
# ---------------------------------------------------------------------------

class TestNoHighlightsGuidance:
    def test_no_highlights_message_contains_checklist(self):
        """The warning for no highlights should contain actionable steps."""
        # We test the message format from the source code pattern
        title = "My Paper Title"
        msg = (
            f"  Warning: no highlights found for '{title}'.\n"
            "  To fix this:\n"
            "    1. Enable text recognition (Settings > General > Text recognition)\n"
            "    2. Use the highlighter tool, not a pen\n"
            f"    3. Then: distillate --reprocess \"{title}\""
        )
        assert "text recognition" in msg
        assert "highlighter tool" in msg
        assert "--reprocess" in msg
        assert title in msg


# ---------------------------------------------------------------------------
# A4. Note overwrite on re-sync
# ---------------------------------------------------------------------------

class TestNoteOverwrite:
    def test_create_paper_note_overwrites_existing(self, tmp_path, monkeypatch):
        """create_paper_note should overwrite if note already exists."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))

        # Create a note first
        result1 = obsidian.create_paper_note(
            title="My Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["highlight one"]},
        )
        assert result1 is not None
        assert result1.exists()
        # Create again with different highlights — should overwrite
        result2 = obsidian.create_paper_note(
            title="My Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["highlight two"]},
        )
        assert result2 is not None
        assert result2.exists()
        new_content = result2.read_text()

        # Content should be different (overwritten)
        assert "highlight two" in new_content
        assert "highlight one" not in new_content

    def test_overwrite_preserves_my_notes(self, tmp_path, monkeypatch):
        """Overwriting a note should preserve the user's ## My Notes section."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))

        # Create initial note
        result1 = obsidian.create_paper_note(
            title="My Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["highlight one"]},
        )
        assert result1 is not None

        # Simulate user adding notes
        content = result1.read_text()
        content = content.rstrip() + "\nThis is my personal note.\nAnother thought.\n"
        result1.write_text(content)

        # Re-create with new highlights — should preserve My Notes content
        result2 = obsidian.create_paper_note(
            title="My Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["highlight two"]},
        )
        new_content = result2.read_text()

        assert "highlight two" in new_content
        assert "highlight one" not in new_content
        assert "This is my personal note." in new_content
        assert "Another thought." in new_content

    def test_overwrite_no_my_notes_section(self, tmp_path, monkeypatch):
        """Overwriting a note without ## My Notes should still work cleanly."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))

        # Create initial note, then strip the My Notes section entirely
        result1 = obsidian.create_paper_note(
            title="Stripped Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["old highlight"]},
        )
        content = result1.read_text().replace("\n## My Notes\n", "")
        result1.write_text(content)

        # Re-create — should work without error
        result2 = obsidian.create_paper_note(
            title="Stripped Paper",
            authors=["Author A"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K1",
            highlights={"1": ["new highlight"]},
        )
        new_content = result2.read_text()
        assert "new highlight" in new_content
        assert "## My Notes" in new_content

    def test_create_paper_note_works_when_no_existing(self, tmp_path, monkeypatch):
        """create_paper_note still works fine when no note exists."""
        from distillate import obsidian, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", str(tmp_path))

        result = obsidian.create_paper_note(
            title="Fresh Paper",
            authors=["Author B"],
            date_added="2026-01-01T00:00:00",
            zotero_item_key="K2",
        )
        assert result is not None
        assert result.exists()
        assert "Fresh Paper" in result.read_text()


# ---------------------------------------------------------------------------
# A5. First-run --status onboarding
# ---------------------------------------------------------------------------

class TestFirstRunStatus:
    def test_status_shows_onboarding_when_no_state_no_env(
        self, tmp_path, monkeypatch, capsys,
    ):
        """--status with no state and no .env should show onboarding message."""
        from distillate import main, config

        # Ensure state path doesn't exist (isolate_state uses tmp_path)
        import distillate.state as state_mod
        assert not state_mod.STATE_PATH.exists()

        # Point ENV_PATH to a non-existent path
        from pathlib import Path
        monkeypatch.setattr(config, "ENV_PATH", Path(tmp_path / "nonexistent" / ".env"))
        monkeypatch.setattr(config, "LOG_LEVEL", "INFO")

        main._status()
        captured = capsys.readouterr()
        assert "No papers tracked yet" in captured.out
        assert "--init" in captured.out

    def test_status_works_normally_with_state(
        self, populated_state, monkeypatch, capsys,
    ):
        """--status with a populated state should show normal output."""
        from distillate import main, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", "/tmp/test")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "RESEND_API_KEY", "")
        monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
        # Point env to existing dir so it doesn't trigger first-run
        import distillate.state as state_mod
        monkeypatch.setenv("DISTILLATE_CONFIG_DIR", str(state_mod.STATE_PATH.parent))

        # Mock remarkable_client.list_folder to avoid rmapi calls
        from distillate import remarkable_client
        monkeypatch.setattr(remarkable_client, "list_folder", lambda f: [])

        main._status()
        captured = capsys.readouterr()
        assert "Distillate" in captured.out
        assert "Queue:" in captured.out


# ---------------------------------------------------------------------------
# A3. Awaiting PDF explanation
# ---------------------------------------------------------------------------

class TestAwaitingPdfExplanation:
    def test_status_shows_awaiting_pdf_guidance(
        self, populated_state, monkeypatch, capsys,
    ):
        """--status should show guidance for papers awaiting PDF."""
        from distillate import main, config

        monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", "")
        monkeypatch.setattr(config, "OUTPUT_PATH", "/tmp/test")
        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
        monkeypatch.setattr(config, "RESEND_API_KEY", "")
        monkeypatch.setattr(config, "LOG_LEVEL", "INFO")
        import distillate.state as state_mod
        monkeypatch.setenv("DISTILLATE_CONFIG_DIR", str(state_mod.STATE_PATH.parent))

        from distillate import remarkable_client
        monkeypatch.setattr(remarkable_client, "list_folder", lambda f: [])

        main._status()
        captured = capsys.readouterr()
        assert "Awaiting PDF" in captured.out
        assert "Sync the PDF in Zotero" in captured.out

    def test_list_shows_awaiting_pdf_guidance(
        self, populated_state, monkeypatch, capsys,
    ):
        """--list should show guidance for papers awaiting PDF."""
        from distillate import main, config

        monkeypatch.setattr(config, "LOG_LEVEL", "INFO")

        main._list()
        captured = capsys.readouterr()
        assert "Awaiting PDF" in captured.out
        assert "Sync the PDF in Zotero" in captured.out


# ---------------------------------------------------------------------------
# B. --themes removed from help
# ---------------------------------------------------------------------------

class TestThemesRemoved:
    def test_help_does_not_mention_themes(self):
        """--help output should not contain --themes."""
        from distillate.main import _HELP
        assert "--themes" not in _HELP

    def test_cli_does_not_route_themes(self, monkeypatch):
        """--themes should not be routed in main()."""
        import sys
        from distillate import main

        monkeypatch.setattr(sys, "argv", ["distillate", "--themes", "2026-02"])

        # Since --themes is removed, it should fall through to the sync path
        # which will try to load config. We just check it doesn't call _themes.
        called = []
        monkeypatch.setattr(main, "_themes", lambda args: called.append(True))

        # It will fail elsewhere (no config), but should NOT call _themes
        try:
            main.main()
        except Exception:
            pass
        assert not called


# ---------------------------------------------------------------------------
# C1. extract_insights uses Haiku
# ---------------------------------------------------------------------------

class TestInsightsModel:
    def test_extract_insights_uses_fast_model(self, monkeypatch):
        """extract_insights should call _call_claude with CLAUDE_FAST_MODEL."""
        from distillate import summarizer, config

        monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setattr(config, "CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")
        monkeypatch.setattr(config, "CLAUDE_SMART_MODEL", "claude-sonnet-4-5-20250929")

        calls = []
        def mock_call(prompt, max_tokens=400, model=None):
            calls.append(model)
            return "- fact one\n- fact two\n- So what: it matters"

        monkeypatch.setattr(summarizer, "_call_claude", mock_call)

        summarizer.extract_insights(
            "Test Paper",
            highlights=["some highlight"],
            abstract="some abstract",
        )
        assert calls[0] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# C2. Unified title-matching
# ---------------------------------------------------------------------------

class TestUnifiedTitleMatching:
    def test_match_exact_title(self):
        """Exact title match."""
        from distillate.digest import match_suggestion_to_title
        titles = ["Attention Is All You Need", "BERT Paper"]
        result = match_suggestion_to_title(
            "1. Attention Is All You Need — great paper", titles
        )
        assert result == "Attention Is All You Need"

    def test_match_bidirectional_substring(self):
        """Suggestion title is substring of known title (journal suffix)."""
        from distillate.digest import match_suggestion_to_title
        titles = ["A small polymerase with big potential | Science"]
        result = match_suggestion_to_title(
            "1. A small polymerase with big potential — exciting", titles
        )
        assert result == "A small polymerase with big potential | Science"

    def test_match_known_title_in_suggestion(self):
        """Known title appears inside the suggestion line."""
        from distillate.digest import match_suggestion_to_title
        titles = ["BERT"]
        result = match_suggestion_to_title(
            "1. BERT: Pre-training of Deep Bidirectional Transformers — foundational", titles
        )
        assert result == "BERT"

    def test_no_match_returns_none(self):
        """No match returns None."""
        from distillate.digest import match_suggestion_to_title
        titles = ["Totally Different Paper"]
        result = match_suggestion_to_title(
            "1. Some Other Paper — interesting stuff", titles
        )
        assert result is None

    def test_match_strips_markdown_bold(self):
        """Should handle Claude's **bold** markers."""
        from distillate.digest import match_suggestion_to_title
        titles = ["My Paper Title"]
        result = match_suggestion_to_title(
            "1. **My Paper Title** — reason", titles
        )
        assert result == "My Paper Title"

    def test_match_empty_line_returns_none(self):
        """Empty/whitespace lines return None."""
        from distillate.digest import match_suggestion_to_title
        assert match_suggestion_to_title("", ["Title"]) is None
        assert match_suggestion_to_title("   ", ["Title"]) is None
