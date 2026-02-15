"""Tests for auto-promote: stat_document, smart demotion, and Sonnet suggestions."""

import subprocess
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# stat_document
# ---------------------------------------------------------------------------


class TestStatDocument:
    """Tests for remarkable_client.stat_document()."""

    SAMPLE_STAT_OUTPUT = (
        "ModifiedClient: 2026-02-07 08:30:00.000000000 +0000 UTC\n"
        "CurrentPage: 3\n"
        "PageCount: 12\n"
    )

    def test_parses_current_page_and_modified(self):
        from distillate.remarkable_client import stat_document

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self.SAMPLE_STAT_OUTPUT, stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "My Paper")

        assert info is not None
        assert info["current_page"] == 3
        assert "2026-02-07" in info["modified_client"]

    def test_current_page_zero(self):
        from distillate.remarkable_client import stat_document

        output = (
            "ModifiedClient: 2026-02-07 08:30:00.000000000 +0000 UTC\n"
            "CurrentPage: 0\n"
        )
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=output, stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "My Paper")

        assert info is not None
        assert info["current_page"] == 0

    def test_returns_none_on_failure(self):
        from distillate.remarkable_client import stat_document

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "Missing Doc")

        assert info is None

    def test_returns_empty_dict_on_unparseable_output(self):
        from distillate.remarkable_client import stat_document

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="SomeOtherField: value\n", stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "Weird Doc")

        assert info == {}
        assert info is not None  # command succeeded, should not be None

    def test_handles_non_numeric_current_page(self):
        from distillate.remarkable_client import stat_document

        output = (
            "ModifiedClient: 2026-02-07 08:30:00.000000000 +0000 UTC\n"
            "CurrentPage: unknown\n"
        )
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=output, stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "Bad Page")

        # Should still return modified_client, but no current_page
        assert info is not None
        assert "modified_client" in info
        assert "current_page" not in info


class TestRmapiTimeout:
    """Test that rmapi timeout is caught and wrapped in RuntimeError."""

    def test_timeout_raises_runtime_error(self):
        import pytest
        from distillate.remarkable_client import _run

        with patch("shutil.which", return_value="/usr/bin/rmapi"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="rmapi", timeout=120)):
            with pytest.raises(RuntimeError, match="timed out"):
                _run(["ls", "/"])


# ---------------------------------------------------------------------------
# _suggest — smart demotion logic (formerly _promote)
# ---------------------------------------------------------------------------


def _make_state(documents: dict, promoted: list, pending: list = None):
    """Create a mock State with the given documents and promoted list."""
    state = MagicMock()
    state.promoted_papers = list(promoted)
    state.pending_promotions = list(pending or [])
    state.get_document = lambda key: documents.get(key)
    state.documents_with_status = lambda status: [
        d for d in documents.values() if d["status"] == status
    ]
    state.documents_processed_since = lambda since: []
    return state


def _make_doc(key, title, rm_name="doc_rm_name", status="on_remarkable"):
    return {
        "zotero_item_key": key,
        "title": title,
        "remarkable_doc_name": rm_name,
        "status": status,
        "metadata": {"tags": ["ml"]},
        "uploaded_at": "2026-01-01T00:00:00+00:00",
    }


class TestPromoteSmartDemotion:
    """Test the demotion logic in _suggest()."""

    def _run_suggest(self, state, rm_mock, summarizer_mock):
        """Run _suggest() with mocked dependencies."""
        with patch("distillate.remarkable_client.list_folder", rm_mock.list_folder), \
             patch("distillate.remarkable_client.stat_document", rm_mock.stat_document), \
             patch("distillate.remarkable_client.move_document", rm_mock.move_document), \
             patch("distillate.summarizer.suggest_papers", summarizer_mock.suggest_papers), \
             patch("distillate.state.State", return_value=state), \
             patch("distillate.state.acquire_lock", return_value=True), \
             patch("distillate.state.release_lock"), \
             patch("distillate.config.RM_FOLDER_PAPERS", "Distillate"), \
             patch("distillate.config.RM_FOLDER_INBOX", "Distillate/Inbox"), \
             patch("distillate.config.ANTHROPIC_API_KEY", ""), \
             patch("distillate.config.STATE_GIST_ID", ""), \
             patch("distillate.digest.fetch_pending_from_gist", return_value=None):
            from distillate.main import _suggest
            _suggest()

    def test_skips_demotion_when_user_started_reading(self):
        """Papers with CurrentPage > 0 should NOT be demoted."""
        doc_a = _make_doc("KEY_A", "Paper A", "Paper_A")
        state = _make_state({"KEY_A": doc_a}, ["KEY_A"])

        rm = MagicMock()
        rm.list_folder.return_value = ["Paper_A"]
        rm.stat_document.return_value = {"current_page": 3, "modified_client": "..."}

        summarizer = MagicMock()
        summarizer.suggest_papers.return_value = None

        self._run_suggest(state, rm, summarizer)

        rm.move_document.assert_not_called()
        assert "KEY_A" in state.promoted_papers

    def test_demotes_unread_paper(self):
        """Papers with CurrentPage == 0 should be demoted back to Inbox."""
        doc_a = _make_doc("KEY_A", "Paper A", "Paper_A")
        state = _make_state({"KEY_A": doc_a}, ["KEY_A"])

        rm = MagicMock()
        rm.list_folder.return_value = ["Paper_A"]
        rm.stat_document.return_value = {"current_page": 0, "modified_client": "..."}

        summarizer = MagicMock()
        summarizer.suggest_papers.return_value = None

        self._run_suggest(state, rm, summarizer)

        rm.move_document.assert_called_once_with(
            "Paper_A", "Distillate", "Distillate/Inbox"
        )

    def test_skips_demotion_when_stat_fails(self):
        """If stat_document returns None, don't demote (safety)."""
        doc_a = _make_doc("KEY_A", "Paper A", "Paper_A")
        state = _make_state({"KEY_A": doc_a}, ["KEY_A"])

        rm = MagicMock()
        rm.list_folder.return_value = ["Paper_A"]
        rm.stat_document.return_value = None

        summarizer = MagicMock()
        summarizer.suggest_papers.return_value = None

        self._run_suggest(state, rm, summarizer)

        rm.move_document.assert_not_called()
        assert "KEY_A" in state.promoted_papers

    def test_skips_demotion_when_paper_not_at_root(self):
        """If paper was manually moved away from root, skip demotion."""
        doc_a = _make_doc("KEY_A", "Paper A", "Paper_A")
        state = _make_state({"KEY_A": doc_a}, ["KEY_A"])

        rm = MagicMock()
        # Paper is NOT in the papers root listing
        rm.list_folder.return_value = []

        summarizer = MagicMock()
        summarizer.suggest_papers.return_value = None

        self._run_suggest(state, rm, summarizer)

        rm.move_document.assert_not_called()
        rm.stat_document.assert_not_called()


# ---------------------------------------------------------------------------
# suggest_papers uses Sonnet
# ---------------------------------------------------------------------------


class TestSuggestPapersSonnet:
    """Verify suggest_papers passes the smart model to _call_claude."""

    @patch("distillate.summarizer._call_claude")
    @patch("distillate.summarizer.config")
    def test_uses_smart_model(self, mock_config, mock_call):
        mock_config.ANTHROPIC_API_KEY = "test-key"
        mock_config.CLAUDE_SMART_MODEL = "claude-sonnet-4-5"
        mock_call.return_value = "1. Paper A — reason"

        from distillate.summarizer import suggest_papers

        unread = [{"title": "Paper A", "tags": ["ml"], "paper_type": "", "uploaded_at": "2026-01-01"}]
        result = suggest_papers(unread, [])

        assert result is not None
        mock_call.assert_called_once()
        _, kwargs = mock_call.call_args
        assert kwargs["model"] == "claude-sonnet-4-5"
        assert kwargs["max_tokens"] == 300


# ---------------------------------------------------------------------------
# promoted_at timestamp
# ---------------------------------------------------------------------------


class TestPromotedAtTimestamp:
    """Verify that promoted papers get a promoted_at timestamp."""

    def test_sets_promoted_at_on_new_promotion(self):
        doc_a = _make_doc("KEY_A", "Paper A", "Paper_A")
        documents = {"KEY_A": doc_a}
        state = _make_state(documents, [])

        rm = MagicMock()

        # First call: papers root (demotion phase — no old promoted, so just
        # returns empty for the check). Second call: inbox listing.
        def list_folder_side_effect(folder):
            if folder == "Distillate/Inbox":
                return ["Paper_A"]
            return []
        rm.list_folder.side_effect = list_folder_side_effect

        summarizer = MagicMock()
        summarizer.suggest_papers.return_value = "1. Paper A — great paper"

        with patch("distillate.remarkable_client.list_folder", rm.list_folder), \
             patch("distillate.remarkable_client.stat_document", rm.stat_document), \
             patch("distillate.remarkable_client.move_document", rm.move_document), \
             patch("distillate.summarizer.suggest_papers", summarizer.suggest_papers), \
             patch("distillate.state.State", return_value=state), \
             patch("distillate.state.acquire_lock", return_value=True), \
             patch("distillate.state.release_lock"), \
             patch("distillate.config.RM_FOLDER_PAPERS", "Distillate"), \
             patch("distillate.config.RM_FOLDER_INBOX", "Distillate/Inbox"), \
             patch("distillate.config.ANTHROPIC_API_KEY", ""), \
             patch("distillate.config.STATE_GIST_ID", ""), \
             patch("distillate.digest.fetch_pending_from_gist", return_value=None):
            from distillate.main import _suggest
            _suggest()

        assert "promoted_at" in doc_a
