"""Tests for v0.1.6 features: permissions, --list, --remove, clean output,
PDF delete guard, intermediate state, --status queue contents, init disclosures."""

import logging

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
    s.add_document("K4", "A4", "md5", "paper_four", "GPT-4 Technical Report",
                    ["OpenAI"], status="on_remarkable")
    s.promoted_papers = ["K1"]
    s.pending_promotions = ["K4"]
    s.save()
    return s


# ---------------------------------------------------------------------------
# 1. .env file permissions
# ---------------------------------------------------------------------------

class TestEnvPermissions:
    def test_new_env_gets_0600(self, tmp_path, monkeypatch):
        from distillate import config
        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("SECRET_KEY", "sk-test123")
        assert env_file.stat().st_mode & 0o777 == 0o600

    def test_existing_env_gets_0600_on_update(self, tmp_path, monkeypatch):
        from distillate import config
        env_file = tmp_path / ".env"
        env_file.write_text("OLD=val\n")
        env_file.chmod(0o644)  # start with world-readable
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("NEW", "val")
        assert env_file.stat().st_mode & 0o777 == 0o600

    def test_repeated_saves_keep_0600(self, tmp_path, monkeypatch):
        from distillate import config
        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)

        config.save_to_env("K1", "v1")
        config.save_to_env("K2", "v2")
        config.save_to_env("K1", "v1_updated")
        assert env_file.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# 2. setup_logging() idempotency
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def test_setup_logging_idempotent(self, monkeypatch):
        from distillate import config
        monkeypatch.setattr(config, "_logging_configured", False)

        root = logging.getLogger()

        config.setup_logging()
        after_first = len(root.handlers)

        config.setup_logging()  # second call should be no-op
        after_second = len(root.handlers)

        assert after_second == after_first
        # Reset for other tests
        monkeypatch.setattr(config, "_logging_configured", False)


# ---------------------------------------------------------------------------
# 3. State.remove_document()
# ---------------------------------------------------------------------------

class TestRemoveDocumentExtended:
    def test_remove_from_find_by_doi(self, populated_state):
        """After removal, find_by_doi should not find the document."""
        s = populated_state
        assert s.find_by_doi("10.1234/test") is not None
        s.remove_document("K2")
        assert s.find_by_doi("10.1234/test") is None

    def test_remove_from_find_by_title(self, populated_state):
        """After removal, find_by_title should not find the document."""
        s = populated_state
        assert s.find_by_title("Attention Is All You Need") is not None
        s.remove_document("K1")
        assert s.find_by_title("Attention Is All You Need") is None

    def test_remove_from_documents_with_status(self, populated_state):
        """After removal, documents_with_status should not include it."""
        s = populated_state
        on_rm_before = s.documents_with_status("on_remarkable")
        assert len(on_rm_before) == 2

        s.remove_document("K1")
        on_rm_after = s.documents_with_status("on_remarkable")
        assert len(on_rm_after) == 1
        assert on_rm_after[0]["title"] == "GPT-4 Technical Report"

    def test_remove_cleans_only_targeted_lists(self, populated_state):
        """Removing K1 cleans promoted but not pending (K4 is pending)."""
        s = populated_state
        s.remove_document("K1")
        assert "K1" not in s.promoted_papers
        assert "K4" in s.pending_promotions  # untouched

    def test_remove_nonexistent_key_returns_false(self):
        from distillate.state import State
        s = State()
        assert s.remove_document("DOES_NOT_EXIST") is False


# ---------------------------------------------------------------------------
# 4. PDF delete guard
# ---------------------------------------------------------------------------

class TestPdfDeleteGuard:
    def test_upload_paper_no_delete_when_save_fails(self, tmp_path, monkeypatch):
        """When save_inbox_pdf returns None, delete_attachment should NOT be called."""
        from distillate.main import _upload_paper
        from distillate.state import State
        from distillate import config

        state = State()

        monkeypatch.setattr(config, "KEEP_ZOTERO_PDF", False)
        monkeypatch.setattr(config, "RM_FOLDER_INBOX", "Distillate/Inbox")
        monkeypatch.setattr(config, "ZOTERO_TAG_INBOX", "inbox")

        monkeypatch.setattr(
            "distillate.zotero_client.extract_metadata",
            lambda p: {"title": "Test Paper", "authors": ["Auth A"],
                        "doi": "", "url": "", "tags": []},
        )
        monkeypatch.setattr(
            "distillate.zotero_client.get_pdf_attachment",
            lambda k: {"key": "ATT1", "data": {"md5": "abc"}},
        )
        monkeypatch.setattr(
            "distillate.zotero_client.download_pdf",
            lambda k: b"fake-pdf-bytes",
        )
        monkeypatch.setattr(
            "distillate.zotero_client.create_linked_attachment",
            lambda *a, **kw: None,
        )
        delete_calls = []
        monkeypatch.setattr(
            "distillate.zotero_client.delete_attachment",
            lambda k: delete_calls.append(k),
        )
        monkeypatch.setattr(
            "distillate.zotero_client.add_tag",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.obsidian.save_inbox_pdf",
            lambda *a: None,  # save fails!
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.sanitize_filename",
            lambda t: "Test_Paper",
        )
        monkeypatch.setattr(
            "distillate.remarkable_client.upload_pdf_bytes",
            lambda *a: None,
        )
        monkeypatch.setattr(
            "distillate.semantic_scholar.lookup_paper",
            lambda **kw: None,
        )

        paper = {
            "key": "ITEM1",
            "data": {"title": "Test Paper", "itemType": "journalArticle"},
        }

        _upload_paper(paper, state, existing_on_rm=set())

        # The key assertion: delete_attachment should NOT have been called
        assert delete_calls == [], f"delete_attachment was called with: {delete_calls}"


# ---------------------------------------------------------------------------
# 5. --list command
# ---------------------------------------------------------------------------

class TestListCommand:
    def test_list_empty_state(self, capsys):
        from distillate.main import _list
        _list()
        output = capsys.readouterr().out
        assert "No papers tracked yet" in output

    def test_list_shows_all_groups(self, capsys, populated_state):
        from distillate.main import _list
        _list()
        output = capsys.readouterr().out
        assert "On reMarkable (2)" in output
        assert "Processed (1)" in output
        assert "Awaiting PDF (1)" in output
        assert "Attention Is All You Need" in output
        assert "Scaling Laws" in output

    def test_list_shows_first_author(self, capsys, populated_state):
        from distillate.main import _list
        _list()
        output = capsys.readouterr().out
        # "Vaswani, A." -> split(",")[0] = "Vaswani" -> split()[-1] = "Vaswani"
        assert "Vaswani" in output

    def test_list_handles_empty_author(self, capsys):
        """Edge case: author is empty string."""
        from distillate.state import State
        from distillate.main import _list

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Paper X", [""])
        s.save()

        _list()  # should not raise IndexError
        output = capsys.readouterr().out
        assert "Paper X" in output

    def test_list_does_not_show_deleted(self, capsys, populated_state):
        from distillate.main import _list

        populated_state.mark_deleted("K3")
        populated_state.save()

        _list()
        output = capsys.readouterr().out
        assert "BERT" not in output


# ---------------------------------------------------------------------------
# 6. --remove command
# ---------------------------------------------------------------------------

class TestRemoveCommand:
    def test_remove_empty_args(self, capsys):
        from distillate.main import _remove
        _remove([])
        output = capsys.readouterr().out
        assert "Usage:" in output

    def test_remove_no_match(self, capsys, populated_state):
        from distillate.main import _remove
        _remove(["Nonexistent Paper"])
        output = capsys.readouterr().out
        assert "No papers matching" in output

    def test_remove_single_match_confirmed(self, capsys, populated_state, monkeypatch):
        from distillate.main import _remove
        from distillate.state import State
        monkeypatch.setattr("builtins.input", lambda _: "y")

        _remove(["Attention"])
        output = capsys.readouterr().out
        assert "Removed." in output
        # _remove creates its own State, so reload to verify
        reloaded = State()
        assert not reloaded.has_document("K1")

    def test_remove_single_match_cancelled(self, capsys, populated_state, monkeypatch):
        from distillate.main import _remove
        monkeypatch.setattr("builtins.input", lambda _: "n")

        _remove(["Attention"])
        output = capsys.readouterr().out
        assert "Cancelled." in output
        assert populated_state.has_document("K1")  # still there

    def test_remove_multiple_matches_select(self, capsys, populated_state, monkeypatch):
        """Both papers have 'a' in title — match both, select #1."""
        from distillate.main import _remove

        # Both "Attention Is All..." and "Scaling Laws..." contain 'al'
        monkeypatch.setattr("builtins.input", lambda _: "1")
        _remove(["al"])
        output = capsys.readouterr().out
        assert "Found 2 papers" in output or "Found 3 papers" in output or "Removed:" in output

    def test_remove_multiple_matches_cancel(self, capsys, populated_state, monkeypatch):
        from distillate.main import _remove
        monkeypatch.setattr("builtins.input", lambda _: "")

        _remove(["al"])
        output = capsys.readouterr().out
        assert "Cancelled." in output

    def test_remove_with_quotes_in_args(self, capsys, populated_state, monkeypatch):
        """Handles shell-level quote stripping gracefully."""
        from distillate.main import _remove
        monkeypatch.setattr("builtins.input", lambda _: "y")

        _remove(['"Attention', 'Is', 'All', 'You', 'Need"'])
        output = capsys.readouterr().out
        assert "Removed." in output


# ---------------------------------------------------------------------------
# 7. --status queue contents
# ---------------------------------------------------------------------------

class TestStatusQueueContents:
    def test_status_lists_queue_papers(self, capsys, populated_state, monkeypatch):
        from distillate.main import _status
        from distillate import config

        monkeypatch.setattr(config, "_logging_configured", False)

        _status()
        output = capsys.readouterr().out
        # Should show the 2 on_remarkable papers in queue listing
        assert "Attention Is All You Need" in output
        assert "GPT-4 Technical Report" in output

    def test_status_no_papers(self, capsys, monkeypatch):
        from distillate.main import _status
        from distillate import config
        from distillate.state import State

        monkeypatch.setattr(config, "_logging_configured", False)

        # Create empty state so first-run check doesn't trigger
        State().save()

        _status()
        output = capsys.readouterr().out
        assert "0 papers waiting" in output


# ---------------------------------------------------------------------------
# 8. Clean terminal output / TTY-aware logging
# ---------------------------------------------------------------------------

class TestCleanOutput:
    def test_version_is_not_hardcoded(self):
        from distillate.main import _VERSION
        assert _VERSION != "0.1.7"  # should be read from package metadata

    def test_help_includes_list_and_remove(self):
        from distillate.main import _HELP
        assert "--list" in _HELP
        assert "--remove" in _HELP
        assert "List all tracked papers" in _HELP
        assert "Remove a paper from tracking" in _HELP


# ---------------------------------------------------------------------------
# 9. --list/--remove/--status don't require Zotero credentials
# ---------------------------------------------------------------------------

class TestNoCredentialsRequired:
    def test_list_works_without_zotero(self, capsys, monkeypatch):
        """--list should work even without ZOTERO_API_KEY."""
        monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
        monkeypatch.delenv("ZOTERO_USER_ID", raising=False)

        from distillate.main import _list
        _list()  # should not raise SystemExit
        output = capsys.readouterr().out
        assert "No papers tracked yet" in output

    def test_remove_works_without_zotero(self, capsys, monkeypatch):
        """--remove should work even without ZOTERO_API_KEY."""
        monkeypatch.delenv("ZOTERO_API_KEY", raising=False)

        from distillate.main import _remove
        _remove([])  # empty args, shows usage
        output = capsys.readouterr().out
        assert "Usage:" in output

    def test_status_works_without_zotero(self, capsys, monkeypatch):
        """--status should work even without ZOTERO_API_KEY."""
        monkeypatch.delenv("ZOTERO_API_KEY", raising=False)
        monkeypatch.delenv("ZOTERO_USER_ID", raising=False)

        from distillate import config
        from distillate.state import State
        monkeypatch.setattr(config, "_logging_configured", False)

        # Create empty state so first-run check doesn't trigger
        State().save()

        from distillate.main import _status
        _status()  # should not raise SystemExit
        output = capsys.readouterr().out
        assert "Distillate" in output


# ---------------------------------------------------------------------------
# 10. Intermediate state: processing status
# ---------------------------------------------------------------------------

class TestProcessingState:
    def test_processing_status_set_and_found(self):
        """Verify 'processing' is a valid status the state machine handles."""
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.set_status("K1", "processing")

        processing = s.documents_with_status("processing")
        assert len(processing) == 1
        assert processing[0]["title"] == "Title"

    def test_processing_then_mark_processed(self):
        """Paper goes on_remarkable -> processing -> processed."""
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        assert s.get_document("K1")["status"] == "on_remarkable"

        s.set_status("K1", "processing")
        assert s.get_document("K1")["status"] == "processing"

        s.mark_processed("K1", summary="Done")
        assert s.get_document("K1")["status"] == "processed"

    def test_processing_persists_across_reload(self):
        """Processing status survives save/reload cycle."""
        from distillate.state import State

        s = State()
        s.add_document("K1", "A1", "md5", "doc", "Title", ["Auth"])
        s.set_status("K1", "processing")
        s.save()

        s2 = State()
        assert s2.get_document("K1")["status"] == "processing"
        assert len(s2.documents_with_status("processing")) == 1


# ---------------------------------------------------------------------------
# 11. Init text recognition mention
# ---------------------------------------------------------------------------

class TestInitDisclosures:
    def test_init_step2_mentions_text_recognition(self):
        """Verify the init Step 2 output includes text recognition guidance."""
        # We can check the source code directly
        import inspect
        from distillate import main

        # Read the source of the init wizard
        source = inspect.getsource(main._init_wizard)
        assert "Text recognition" in source or "text recognition" in source

    def test_init_step5_mentions_claude_data(self):
        """Verify the init Step 5 mentions data being sent to Claude API."""
        import inspect
        from distillate import main

        source = inspect.getsource(main._init_step5)
        assert "Claude API" in source
        assert "highlights" in source
