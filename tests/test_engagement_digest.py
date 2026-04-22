# Covers: distillate/obsidian.py, distillate/digest.py, distillate/summarizer.py

"""Tests for engagement and citation display in notes, digest email, and suggestions."""

from unittest.mock import patch


# ---------------------------------------------------------------------------
# Engagement in Obsidian note frontmatter
# ---------------------------------------------------------------------------


class TestEngagementInNote:
    """Tests for engagement field in Obsidian note frontmatter."""

    def test_engagement_in_frontmatter(self, tmp_path):
        from distillate.obsidian import create_paper_note

        with patch("distillate.obsidian._read_dir", return_value=tmp_path):
            path = create_paper_note(
                title="Test Paper",
                authors=["Author"],
                date_added="2026-01-01",
                zotero_item_key="KEY1",
                engagement=78,
            )

        assert path is not None
        content = path.read_text()
        assert "engagement: 78" in content

    def test_no_engagement_when_zero(self, tmp_path):
        from distillate.obsidian import create_paper_note

        with patch("distillate.obsidian._read_dir", return_value=tmp_path):
            path = create_paper_note(
                title="Test Paper Zero",
                authors=["Author"],
                date_added="2026-01-01",
                zotero_item_key="KEY2",
                engagement=0,
            )

        assert path is not None
        content = path.read_text()
        assert "engagement" not in content


# ---------------------------------------------------------------------------
# Engagement in digest email
# ---------------------------------------------------------------------------


class TestEngagementInDigest:
    """Tests for engagement and stats in digest _paper_html."""

    def test_paper_html_with_engagement(self):
        from distillate.digest import _paper_html

        paper = {
            "title": "Test Paper",
            "summary": "A great paper.",
            "metadata": {"url": "https://example.com", "tags": []},
            "highlight_count": 12,
            "engagement": 78,
            "highlight_word_count": 450,
            "processed_at": "2026-02-10T12:00:00+00:00",
        }
        html = _paper_html(paper)
        assert "Test Paper" in html
        assert "A great paper." in html
        assert "Feb 10" in html

    def test_paper_html_without_engagement(self):
        from distillate.digest import _paper_html

        paper = {
            "title": "Old Paper",
            "summary": "Old stuff.",
            "metadata": {"tags": []},
            "highlight_count": 0,
            "processed_at": "",
        }
        html = _paper_html(paper)
        assert "engaged" not in html
        assert "highlight" not in html


# ---------------------------------------------------------------------------
# Engagement in suggestion prompt context
# ---------------------------------------------------------------------------


class TestEngagementInSuggestions:
    """Tests for engagement in suggest_papers prompt."""

    @patch("distillate.summarizer._call_claude")
    @patch("distillate.summarizer.config")
    def test_engagement_in_prompt(self, mock_config, mock_call):
        mock_config.ANTHROPIC_API_KEY = "test-key"
        mock_config.CLAUDE_SMART_MODEL = "claude-sonnet-4-5"
        mock_call.return_value = "1. Paper A — reason"

        from distillate.summarizer import suggest_papers

        unread = [{"title": "Paper A", "tags": ["ml"], "paper_type": "", "uploaded_at": "2026-01-01"}]
        recent = [{"title": "Read Paper", "tags": ["dl"], "summary": "Good.", "engagement": 85}]

        suggest_papers(unread, recent)

        prompt = mock_call.call_args[0][0]
        assert "engagement:85%" in prompt


# ---------------------------------------------------------------------------
# Citation data surfacing
# ---------------------------------------------------------------------------


class TestCitationInDigest:
    """Tests for citation count in digest email."""

    def test_paper_html_with_citations(self):
        from distillate.digest import _paper_html

        paper = {
            "title": "Cited Paper",
            "summary": "Important work.",
            "metadata": {"url": "https://example.com", "tags": [], "citation_count": 142},
            "highlight_count": 5,
            "engagement": 60,
            "highlight_word_count": 200,
            "processed_at": "2026-02-10T12:00:00+00:00",
        }
        html = _paper_html(paper)
        assert "Cited Paper" in html
        assert "Important work." in html

    def test_paper_html_without_citations(self):
        from distillate.digest import _paper_html

        paper = {
            "title": "Uncited Paper",
            "summary": "New work.",
            "metadata": {"url": "", "tags": [], "citation_count": 0},
            "highlight_count": 3,
            "processed_at": "",
        }
        html = _paper_html(paper)
        assert "citations" not in html

    def test_paper_html_no_metadata_citation(self):
        from distillate.digest import _paper_html

        paper = {
            "title": "Paper No Meta",
            "summary": "",
            "metadata": {"tags": []},
            "highlight_count": 0,
            "processed_at": "",
        }
        html = _paper_html(paper)
        assert "citations" not in html


class TestCitationInSuggestionPrompt:
    """Tests for citation count in suggest_papers prompt context."""

    @patch("distillate.summarizer._call_claude")
    @patch("distillate.summarizer.config")
    def test_citation_count_in_queue(self, mock_config, mock_call):
        mock_config.ANTHROPIC_API_KEY = "test-key"
        mock_config.CLAUDE_SMART_MODEL = "claude-sonnet-4-5"
        mock_call.return_value = "1. Paper A — reason"

        from distillate.summarizer import suggest_papers

        unread = [{
            "title": "Paper A", "tags": ["ml"], "paper_type": "",
            "uploaded_at": "2026-01-01", "citation_count": 500,
        }]
        recent = [{
            "title": "Read Paper", "tags": ["dl"], "summary": "Good.",
            "engagement": 50, "citation_count": 200,
        }]

        suggest_papers(unread, recent)

        prompt = mock_call.call_args[0][0]
        assert "500 citations" in prompt
        assert "200 citations" in prompt

    @patch("distillate.summarizer._call_claude")
    @patch("distillate.summarizer.config")
    def test_no_citation_when_zero(self, mock_config, mock_call):
        mock_config.ANTHROPIC_API_KEY = "test-key"
        mock_config.CLAUDE_SMART_MODEL = "claude-sonnet-4-5"
        mock_call.return_value = "1. Paper A — reason"

        from distillate.summarizer import suggest_papers

        unread = [{
            "title": "Paper A", "tags": [], "paper_type": "",
            "uploaded_at": "2026-01-01", "citation_count": 0,
        }]
        recent = []

        suggest_papers(unread, recent)

        prompt = mock_call.call_args[0][0]
        assert "citations" not in prompt
