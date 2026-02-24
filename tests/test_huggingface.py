"""Tests for distillate.huggingface — HuggingFace Daily Papers integration."""

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_api_entry(arxiv_id="2501.12948", title="Test Paper", upvotes=42,
                    github_repo="https://github.com/org/repo", github_stars=100):
    """Build a realistic daily_papers API response entry."""
    return {
        "paper": {
            "id": arxiv_id,
            "title": title,
            "summary": "A test abstract.",
            "authors": [
                {"name": "Alice", "hidden": False},
                {"name": "Bob", "hidden": False},
                {"name": "Hidden Author", "hidden": True},
            ],
            "upvotes": upvotes,
            "ai_summary": "A one-liner summary.",
            "ai_keywords": ["transformers", "attention"],
            "githubRepo": github_repo,
            "githubStars": github_stars,
            "publishedAt": "2025-01-22T15:19:35.000Z",
        },
        "publishedAt": "2025-01-22T15:19:35.000Z",
        "title": title,
        "summary": "A test abstract.",
        "numComments": 3,
    }


def _make_lookup_response(arxiv_id="2501.12948"):
    """Build a realistic single-paper lookup response."""
    return {
        "id": arxiv_id,
        "title": "Test Paper",
        "summary": "Abstract.",
        "authors": [{"name": "Alice", "hidden": False}],
        "upvotes": 441,
        "githubRepo": "https://github.com/org/repo",
        "githubStars": 5000,
        "ai_keywords": ["RL", "reasoning"],
    }


# ---------------------------------------------------------------------------
# Tests: _parse_paper
# ---------------------------------------------------------------------------

class TestParsePaper:
    def test_extracts_fields(self):
        from distillate.huggingface import _parse_paper
        entry = _make_api_entry()
        result = _parse_paper(entry)
        assert result["arxiv_id"] == "2501.12948"
        assert result["title"] == "Test Paper"
        assert result["upvotes"] == 42
        assert result["github_repo"] == "https://github.com/org/repo"
        assert result["github_stars"] == 100
        assert result["ai_keywords"] == ["transformers", "attention"]
        assert result["pdf_url"] == "https://arxiv.org/pdf/2501.12948"
        assert result["hf_url"] == "https://huggingface.co/papers/2501.12948"

    def test_filters_hidden_authors(self):
        from distillate.huggingface import _parse_paper
        entry = _make_api_entry()
        result = _parse_paper(entry)
        assert "Alice" in result["authors"]
        assert "Bob" in result["authors"]
        assert "Hidden Author" not in result["authors"]

    def test_handles_missing_fields(self):
        from distillate.huggingface import _parse_paper
        result = _parse_paper({"paper": {"id": "1234.5678"}})
        assert result["arxiv_id"] == "1234.5678"
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["github_repo"] is None


# ---------------------------------------------------------------------------
# Tests: trending_papers
# ---------------------------------------------------------------------------

class TestTrendingPapers:
    @patch("distillate.huggingface.requests.get")
    def test_returns_parsed_papers(self, mock_get):
        from distillate.huggingface import trending_papers
        mock_resp = MagicMock()
        mock_resp.json.return_value = [_make_api_entry(), _make_api_entry(title="Paper 2")]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = trending_papers(limit=5)
        assert len(result) == 2
        assert result[0]["title"] == "Test Paper"
        assert result[1]["title"] == "Paper 2"
        mock_get.assert_called_once()
        assert "trending" in str(mock_get.call_args)

    @patch("distillate.huggingface.requests.get")
    def test_returns_empty_on_error(self, mock_get):
        from distillate.huggingface import trending_papers
        mock_get.side_effect = ConnectionError("network down")
        result = trending_papers()
        assert result == []

    @patch("distillate.huggingface.requests.get")
    def test_returns_empty_on_http_error(self, mock_get):
        from distillate.huggingface import trending_papers
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500")
        mock_get.return_value = mock_resp
        result = trending_papers()
        assert result == []


# ---------------------------------------------------------------------------
# Tests: trending_papers_for_week
# ---------------------------------------------------------------------------

class TestTrendingPapersForWeek:
    @patch("distillate.huggingface.requests.get")
    def test_passes_week_param(self, mock_get):
        from distillate.huggingface import trending_papers_for_week
        mock_resp = MagicMock()
        mock_resp.json.return_value = [_make_api_entry()]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = trending_papers_for_week("2026-W08", limit=3)
        assert len(result) == 1
        call_params = mock_get.call_args[1]["params"]
        assert call_params["week"] == "2026-W08"
        assert call_params["limit"] == 3

    @patch("distillate.huggingface.requests.get")
    def test_defaults_to_current_week(self, mock_get):
        from distillate.huggingface import trending_papers_for_week
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        trending_papers_for_week()
        call_params = mock_get.call_args[1]["params"]
        assert "W" in call_params["week"]  # auto-generated ISO week

    @patch("distillate.huggingface.requests.get")
    def test_returns_empty_on_error(self, mock_get):
        from distillate.huggingface import trending_papers_for_week
        mock_get.side_effect = ConnectionError("timeout")
        result = trending_papers_for_week()
        assert result == []


# ---------------------------------------------------------------------------
# Tests: lookup_paper
# ---------------------------------------------------------------------------

class TestLookupPaper:
    @patch("distillate.huggingface.requests.get")
    def test_returns_enrichment_data(self, mock_get):
        from distillate.huggingface import lookup_paper
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_lookup_response()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = lookup_paper("2501.12948")
        assert result["title"] == "Test Paper"
        assert result["authors"] == ["Alice"]
        assert result["abstract"] == "Abstract."
        assert result["github_repo"] == "https://github.com/org/repo"
        assert result["github_stars"] == 5000
        assert result["upvotes"] == 441
        assert "RL" in result["ai_keywords"]

    @patch("distillate.huggingface.requests.get")
    def test_returns_none_on_404(self, mock_get):
        from distillate.huggingface import lookup_paper
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        assert lookup_paper("9999.99999") is None

    def test_returns_none_for_empty_id(self):
        from distillate.huggingface import lookup_paper
        assert lookup_paper("") is None

    @patch("distillate.huggingface.requests.get")
    def test_returns_none_on_error(self, mock_get):
        from distillate.huggingface import lookup_paper
        mock_get.side_effect = ConnectionError("down")
        assert lookup_paper("2501.12948") is None

    @patch("distillate.huggingface.requests.get")
    def test_handles_missing_optional_fields(self, mock_get):
        from distillate.huggingface import lookup_paper
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "2501.12948", "upvotes": 10}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = lookup_paper("2501.12948")
        assert result["title"] == ""
        assert result["authors"] == []
        assert result["abstract"] == ""
        assert result["github_repo"] is None
        assert result["github_stars"] is None
        assert result["upvotes"] == 10
        assert result["ai_keywords"] == []
