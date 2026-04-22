# Covers: distillate/main.py

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _legacy_remarkable_mode(monkeypatch):
    """These tests assert on the reMarkable status groups; force that mode."""
    monkeypatch.setattr("distillate.config.READING_SOURCE", "remarkable")


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
# --list command (from test_v016.py)
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

    def test_list_shows_title_and_index(self, capsys, populated_state):
        from distillate.main import _list
        _list()
        output = capsys.readouterr().out
        assert "Attention Is All You Need" in output
        assert "[1]" in output

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
# --remove command (from test_v016.py)
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
# --status queue contents (from test_v016.py)
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
# --list/--remove/--status don't require Zotero credentials (from test_v016.py)
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
# PDF delete guard (from test_v016.py)
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
            lambda *a, **kw: None,  # save fails!
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.sanitize_filename",
            lambda t: "Test_Paper",
        )
        monkeypatch.setattr(
            "distillate.integrations.remarkable.client.upload_pdf_bytes",
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
# From test_v017.py
# ---------------------------------------------------------------------------

class TestFirstRunStatus:
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
        from distillate.integrations.remarkable import client as remarkable_client
        monkeypatch.setattr(remarkable_client, "list_folder", lambda f: [])

        main._status()
        captured = capsys.readouterr()
        assert "Distillate" in captured.out
        assert "Queue:" in captured.out

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
        assert "No experiments or papers tracked yet" in captured.out
        assert "--init" in captured.out


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

        from distillate.integrations.remarkable import client as remarkable_client
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
# Migrated from test_v032.py
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch


class TestSyncTagsMarkRead:
    """_sync_tags() should mark papers as processed when Zotero tag is 'read'."""

    def _make_state_and_items(self, zotero_tags, doc_status="on_remarkable"):
        """Helper to build mock state and Zotero items for _sync_tags tests."""
        state = MagicMock()
        state.zotero_library_version = 0
        state.has_document.return_value = True
        doc = {
            "title": "Test Paper",
            "status": doc_status,
            "authors": ["Author"],
            "metadata": {},
        }
        state.get_document.return_value = doc
        state.save = MagicMock()

        item = {
            "key": "ABC123",
            "data": {
                "title": "Test Paper",
                "tags": [{"tag": t} for t in zotero_tags],
                "creators": [{"creatorType": "author", "lastName": "Author"}],
                "date": "2024",
            },
        }
        return state, doc, item

    def _run_sync_tags(self, state, item, monkeypatch):
        """Run _sync_tags with mocked zotero_client."""
        monkeypatch.setenv("ZOTERO_LIBRARY_ID", "123")
        monkeypatch.setenv("ZOTERO_API_KEY", "key")

        mock_zc = MagicMock()
        mock_zc.get_library_version.return_value = 1
        mock_zc.get_changed_item_keys.return_value = (["ABC123"], 1)
        mock_zc.get_items_by_keys.return_value = [item]
        mock_zc.extract_metadata.return_value = {"authors": ["Author"], "tags": []}

        with patch("distillate.zotero_client.get_library_version", mock_zc.get_library_version), \
             patch("distillate.zotero_client.get_changed_item_keys", mock_zc.get_changed_item_keys), \
             patch("distillate.zotero_client.get_items_by_keys", mock_zc.get_items_by_keys), \
             patch("distillate.zotero_client.extract_metadata", mock_zc.extract_metadata):
            from distillate.digest import _sync_tags
            _sync_tags(state)

    def test_marks_read_paper_as_processed(self, monkeypatch):
        state, doc, item = self._make_state_and_items(["read"])
        self._run_sync_tags(state, item, monkeypatch)

        assert doc["status"] == "processed"
        assert "processed_at" in doc

    def test_ignores_inbox_tag(self, monkeypatch):
        state, doc, item = self._make_state_and_items(["inbox"])
        self._run_sync_tags(state, item, monkeypatch)

        assert doc["status"] == "on_remarkable"

    def test_skips_already_processed(self, monkeypatch):
        state, doc, item = self._make_state_and_items(["read"], doc_status="processed")
        self._run_sync_tags(state, item, monkeypatch)

        # Should not have set processed_at since it was already processed
        assert "processed_at" not in doc
