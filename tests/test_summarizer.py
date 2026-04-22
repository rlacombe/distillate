# Covers: distillate/summarizer.py
"""Tests for distillate.summarizer — fallback summary logic."""


class TestFallbackRead:
    def test_prefers_hf_summary_over_abstract(self):
        from distillate.summarizer import _fallback_read
        summary, one_liner = _fallback_read(
            "Test Paper",
            abstract="Long abstract. Second sentence. Third sentence.",
            key_learnings=["A learning"],
            hf_summary="HF generated summary.",
        )
        assert summary == "HF generated summary."
        assert one_liner == "HF generated summary."

    def test_falls_back_to_abstract_without_hf(self):
        from distillate.summarizer import _fallback_read
        summary, one_liner = _fallback_read(
            "Test Paper",
            abstract="First sentence. Second sentence. Third sentence.",
            key_learnings=None,
            hf_summary="",
        )
        assert "First sentence" in summary
        assert one_liner == "First sentence."

    def test_falls_back_to_key_learnings(self):
        from distillate.summarizer import _fallback_read
        summary, one_liner = _fallback_read(
            "Test Paper",
            abstract="",
            key_learnings=["Main insight here"],
            hf_summary="",
        )
        assert summary == "Main insight here"
        assert one_liner == "Main insight here"

    def test_falls_back_to_pending_marker(self):
        from distillate.summarizer import _fallback_read, _PENDING_SUMMARY
        summary, one_liner = _fallback_read(
            "Test Paper", abstract="", key_learnings=None, hf_summary="",
        )
        assert summary == _PENDING_SUMMARY
        assert one_liner == _PENDING_SUMMARY

    def test_hf_summary_default_param(self):
        """hf_summary defaults to empty string (backwards compat)."""
        from distillate.summarizer import _fallback_read
        summary, _ = _fallback_read("Test", abstract="Sentence one.", key_learnings=None)
        assert "Sentence one" in summary


# ---------------------------------------------------------------------------
# Migrated from test_v017.py
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
# Migrated from test_v032.py
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock, patch


class TestSuggestionDedup:
    """Suggestion computation dedup — Claude called at most once per day."""

    @patch("distillate.digest.fetch_pending_from_gist")
    @patch("distillate.digest._sync_tags")
    @patch("distillate.digest.State")
    @patch("distillate.digest.summarizer")
    @patch("distillate.digest._send_email")
    def test_reuses_local_cache(self, mock_email, mock_summarizer,
                                 mock_state_cls, mock_sync, mock_fetch):
        """Re-run same day should reuse local cache, skip Claude, still send email."""
        from datetime import datetime, timezone
        from distillate.digest import send_suggestion

        today = datetime.now(timezone.utc).isoformat()
        mock_state = MagicMock()
        mock_state.documents_with_status.return_value = [
            {"zotero_item_key": "K1", "title": "Test Paper",
             "status": "on_remarkable", "metadata": {"tags": []}},
        ]
        mock_state._data = {
            "last_suggestion": {"text": "1. Test Paper — reason", "timestamp": today},
        }
        mock_state_cls.return_value = mock_state
        mock_fetch.return_value = None

        send_suggestion()

        mock_summarizer.suggest_papers.assert_not_called()
        mock_email.assert_called_once()

    @patch("distillate.digest.fetch_pending_from_gist")
    @patch("distillate.digest._sync_tags")
    @patch("distillate.digest.State")
    @patch("distillate.digest.summarizer")
    @patch("distillate.digest._send_email")
    def test_reuses_gist_cache(self, mock_email, mock_summarizer,
                                mock_state_cls, mock_sync, mock_fetch):
        """GH Actions re-run should reuse Gist cache, skip Claude, still send email."""
        from datetime import datetime, timezone
        from distillate.digest import send_suggestion

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        mock_state = MagicMock()
        mock_state.documents_with_status.return_value = [
            {"zotero_item_key": "K1", "title": "Test Paper",
             "status": "on_remarkable", "metadata": {"tags": []}},
        ]
        mock_state._data = {}  # No local cache
        mock_state_cls.return_value = mock_state
        mock_fetch.return_value = {
            "timestamp": f"{today}T08:00:00+00:00",
            "suggestion_text": "1. Test Paper — reason",
        }

        send_suggestion()

        mock_summarizer.suggest_papers.assert_not_called()
        mock_email.assert_called_once()

    @patch("distillate.digest.fetch_pending_from_gist")
    @patch("distillate.digest._sync_tags")
    @patch("distillate.digest.State")
    @patch("distillate.digest.summarizer")
    @patch("distillate.digest._send_email")
    @patch("distillate.digest._push_pending_to_gist")
    def test_computes_if_yesterday(self, mock_push, mock_email,
                                    mock_summarizer, mock_state_cls,
                                    mock_sync, mock_fetch):
        """Should call Claude if last suggestions are from yesterday."""
        from datetime import datetime, timedelta, timezone
        from distillate.digest import send_suggestion

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mock_state = MagicMock()
        mock_state.documents_with_status.return_value = [
            {"zotero_item_key": "K1", "title": "Test Paper",
             "status": "on_remarkable", "metadata": {"tags": []}},
        ]
        mock_state.documents_processed_since.return_value = []
        mock_state._data = {
            "last_suggestion": {"text": "old", "timestamp": yesterday},
        }
        mock_state_cls.return_value = mock_state
        mock_fetch.return_value = None

        mock_summarizer.suggest_papers.return_value = "1. Test Paper — reason"

        send_suggestion()

        mock_summarizer.suggest_papers.assert_called_once()
        mock_email.assert_called_once()


class TestSuggestionFallback:
    """When Claude is unavailable, send a fallback email with queue + trending."""

    @patch("distillate.digest._fetch_trending_for_email")
    @patch("distillate.digest.fetch_pending_from_gist")
    @patch("distillate.digest._sync_tags")
    @patch("distillate.digest.State")
    @patch("distillate.digest.summarizer")
    @patch("distillate.digest._send_email")
    @patch("distillate.digest._push_pending_to_gist")
    def test_sends_fallback_when_claude_fails(
        self, mock_push, mock_email, mock_summarizer,
        mock_state_cls, mock_sync, mock_fetch, mock_trending,
    ):
        """When Claude returns None, should still send an email."""
        from datetime import datetime, timedelta, timezone
        from distillate.digest import send_suggestion

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mock_state = MagicMock()
        mock_state.documents_with_status.return_value = [
            {"zotero_item_key": "K1", "title": "Queued Paper",
             "status": "on_remarkable", "metadata": {"tags": ["ML"]},
             "uploaded_at": yesterday},
        ]
        mock_state.documents_processed_since.return_value = []
        mock_state._data = {}
        mock_state_cls.return_value = mock_state
        mock_fetch.return_value = None
        mock_trending.return_value = []

        mock_summarizer.suggest_papers.return_value = None

        send_suggestion()

        mock_email.assert_called_once()
        subject = mock_email.call_args[0][0]
        assert "reading queue" in subject.lower()
        body = mock_email.call_args[0][1]
        assert "Queued Paper" in body

    @patch("distillate.digest._fetch_trending_for_email")
    @patch("distillate.digest.fetch_pending_from_gist")
    @patch("distillate.digest._sync_tags")
    @patch("distillate.digest.State")
    @patch("distillate.digest.summarizer")
    @patch("distillate.digest._send_email")
    @patch("distillate.digest._push_pending_to_gist")
    def test_fallback_includes_trending(
        self, mock_push, mock_email, mock_summarizer,
        mock_state_cls, mock_sync, mock_fetch, mock_trending,
    ):
        """Fallback email should include trending papers if available."""
        from datetime import datetime, timedelta, timezone
        from distillate.digest import send_suggestion

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        mock_state = MagicMock()
        mock_state.documents_with_status.return_value = [
            {"zotero_item_key": "K1", "title": "Queued Paper",
             "status": "on_remarkable", "metadata": {"tags": []},
             "uploaded_at": yesterday},
        ]
        mock_state.documents_processed_since.return_value = []
        mock_state._data = {}
        mock_state_cls.return_value = mock_state
        mock_fetch.return_value = None
        mock_trending.return_value = [
            {"title": "Hot Paper", "hf_url": "https://hf.co/papers/1", "upvotes": 100},
        ]

        mock_summarizer.suggest_papers.return_value = None

        send_suggestion()

        mock_email.assert_called_once()
        body = mock_email.call_args[0][1]
        assert "Hot Paper" in body
        assert "Trending" in body


class TestNoneTimestampGuard:
    """Ensure _get_todays_suggestions handles None timestamps gracefully."""

    @patch("distillate.digest.fetch_pending_from_gist")
    def test_none_timestamp_in_local_state(self, mock_fetch):
        """A None timestamp in local state should not crash."""
        from distillate.digest import _get_todays_suggestions

        mock_state = MagicMock()
        mock_state._data = {"last_suggestion": {"text": "test", "timestamp": None}}
        mock_fetch.return_value = None

        result = _get_todays_suggestions(mock_state)
        assert result is None

    @patch("distillate.digest.fetch_pending_from_gist")
    def test_none_timestamp_in_gist(self, mock_fetch):
        """A None timestamp in Gist pending should not crash."""
        from distillate.digest import _get_todays_suggestions

        mock_state = MagicMock()
        mock_state._data = {}
        mock_fetch.return_value = {"timestamp": None, "suggestion_text": "test"}

        result = _get_todays_suggestions(mock_state)
        assert result is None


# ---------------------------------------------------------------------------
# Migrated from test_v070.py
# ---------------------------------------------------------------------------


class TestFallbackS2Tldr:
    def test_fallback_chain_s2_tldr_after_hf(self):
        from distillate.summarizer import _fallback_read
        # No HF summary, but S2 TLDR available
        summary, one_liner = _fallback_read(
            "Paper", abstract="Abstract text. Second. Third.",
            key_learnings=None, hf_summary="", s2_tldr="S2 summary.",
        )
        assert summary == "S2 summary."
        assert one_liner == "S2 summary."

    def test_fallback_hf_takes_precedence_over_s2(self):
        from distillate.summarizer import _fallback_read
        summary, _ = _fallback_read(
            "Paper", abstract="Abstract.", key_learnings=None,
            hf_summary="HF wins.", s2_tldr="S2 loses.",
        )
        assert summary == "HF wins."

    def test_fallback_abstract_used_when_no_s2_tldr(self):
        from distillate.summarizer import _fallback_read
        summary, _ = _fallback_read(
            "Paper", abstract="First sentence. Second. Third.",
            key_learnings=None, hf_summary="", s2_tldr="",
        )
        assert "First sentence" in summary
