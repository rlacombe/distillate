"""Tests for engagement score computation, PageCount parsing, get_page_count, and title matching."""

import io
import json
import subprocess
import zipfile
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# _compute_engagement
# ---------------------------------------------------------------------------


class TestComputeEngagement:
    """Tests for main._compute_engagement()."""

    def test_no_highlights(self):
        from distillate.main import _compute_engagement
        assert _compute_engagement(None, 10) == 0
        assert _compute_engagement({}, 10) == 0

    def test_single_highlight_short_paper(self):
        from distillate.main import _compute_engagement
        # 1 highlight, 1 page highlighted, 5-page paper
        highlights = {1: ["some text"]}
        score = _compute_engagement(highlights, 5)
        # density: min(1/5, 1) = 0.2 → *0.3 = 0.06
        # coverage: 1/5 = 0.2 → *0.4 = 0.08
        # volume: min(1/20, 1) = 0.05 → *0.3 = 0.015
        # total = 0.06 + 0.08 + 0.015 = 0.155 → 16 (banker's rounding)
        assert score == 16

    def test_dense_highlights(self):
        from distillate.main import _compute_engagement
        # 25 highlights across 9 pages, 10-page paper
        highlights = {i: [f"h{j}" for j in range(3)] for i in range(1, 9)}
        highlights[9] = ["extra"]  # 9 pages, 25 highlights total
        score = _compute_engagement(highlights, 10)
        # density: min(25/10, 1) = 1.0 → *0.3 = 0.3
        # coverage: 9/10 = 0.9 → *0.4 = 0.36
        # volume: min(25/20, 1) = 1.0 → *0.3 = 0.3
        # total = 0.3 + 0.36 + 0.3 = 0.96 → 96
        assert score == 96

    def test_zero_page_count_uses_fallback(self):
        from distillate.main import _compute_engagement
        # page_count=0 should use max(0,1)=1
        highlights = {1: ["a", "b"]}
        score = _compute_engagement(highlights, 0)
        assert score > 0
        assert score <= 100

    def test_max_score_is_100(self):
        from distillate.main import _compute_engagement
        # Lots of highlights on many pages
        highlights = {i: [f"h{j}" for j in range(5)] for i in range(1, 21)}
        score = _compute_engagement(highlights, 20)
        assert score == 100

    def test_moderate_engagement(self):
        from distillate.main import _compute_engagement
        # 10 highlights across 5 pages in a 20-page paper
        highlights = {i: ["text", "more"] for i in range(1, 6)}
        score = _compute_engagement(highlights, 20)
        # density: min(10/20, 1) = 0.5 → *0.3 = 0.15
        # coverage: 5/20 = 0.25 → *0.4 = 0.1
        # volume: min(10/20, 1) = 0.5 → *0.3 = 0.15
        # total = 0.15 + 0.1 + 0.15 = 0.4 → 40
        assert score == 40

    def test_skimmed_paper(self):
        from distillate.main import _compute_engagement
        # 5 highlights across 3 pages in an 8-page paper (skimmed middle)
        highlights = {1: ["intro"], 2: ["method"], 8: ["conclusion", "result", "end"]}
        score = _compute_engagement(highlights, 8)
        # density: min(5/8, 1) = 0.625 → *0.3 = 0.1875
        # coverage: 3/8 = 0.375 → *0.4 = 0.15
        # volume: min(5/20, 1) = 0.25 → *0.3 = 0.075
        # total = 0.1875 + 0.15 + 0.075 = 0.4125 → 41
        assert score == 41


# ---------------------------------------------------------------------------
# PageCount in stat_document
# ---------------------------------------------------------------------------


class TestPageCountParsing:
    """Tests for PageCount parsing in stat_document."""

    def test_parses_page_count(self):
        from distillate.remarkable_client import stat_document

        output = (
            "ModifiedClient: 2026-02-07 08:30:00.000000000 +0000 UTC\n"
            "CurrentPage: 3\n"
            "PageCount: 42\n"
        )
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=output, stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "My Paper")

        assert info is not None
        assert info["page_count"] == 42

    def test_handles_missing_page_count(self):
        from distillate.remarkable_client import stat_document

        output = (
            "ModifiedClient: 2026-02-07 08:30:00.000000000 +0000 UTC\n"
            "CurrentPage: 3\n"
        )
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=output, stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "My Paper")

        assert info is not None
        assert "page_count" not in info

    def test_handles_non_numeric_page_count(self):
        from distillate.remarkable_client import stat_document

        output = (
            "ModifiedClient: 2026-02-07 08:30:00.000000000 +0000 UTC\n"
            "PageCount: unknown\n"
        )
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=output, stderr=""
        )
        with patch("distillate.remarkable_client._run", return_value=fake_result):
            info = stat_document("Papers", "My Paper")

        assert info is not None
        assert "page_count" not in info


# ---------------------------------------------------------------------------
# get_page_count from zip bundle
# ---------------------------------------------------------------------------


def _make_zip_with_pages(page_count: int) -> bytes:
    """Create a minimal zip bundle with a .content file listing N pages."""
    buf = io.BytesIO()
    content = {
        "cPages": {
            "pages": [{"id": f"page-{i}"} for i in range(page_count)]
        }
    }
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc-uuid.content", json.dumps(content))
    return buf.getvalue()


class TestGetPageCount:
    """Tests for renderer.get_page_count()."""

    def test_counts_pages_from_content(self, tmp_path):
        from distillate.renderer import get_page_count

        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(_make_zip_with_pages(15))
        assert get_page_count(zip_path) == 15

    def test_returns_zero_for_no_content(self, tmp_path):
        from distillate.renderer import get_page_count

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("some_file.txt", "hello")
        zip_path = tmp_path / "no_content.zip"
        zip_path.write_bytes(buf.getvalue())
        assert get_page_count(zip_path) == 0

    def test_returns_zero_for_invalid_file(self, tmp_path):
        from distillate.renderer import get_page_count

        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(b"not a zip")
        assert get_page_count(zip_path) == 0

    def test_legacy_pages_key(self, tmp_path):
        from distillate.renderer import get_page_count

        buf = io.BytesIO()
        content = {"pages": ["uuid-1", "uuid-2", "uuid-3"]}
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("doc.content", json.dumps(content))
        zip_path = tmp_path / "legacy.zip"
        zip_path.write_bytes(buf.getvalue())
        assert get_page_count(zip_path) == 3


# ---------------------------------------------------------------------------
# Title matching fix (bidirectional)
# ---------------------------------------------------------------------------


class TestTitleMatching:
    """Tests for bidirectional title matching in _build_suggestion_body."""

    def _build_body(self, suggestion_text, unread):
        from distillate.digest import _build_suggestion_body

        state = MagicMock()
        state.documents_with_status.return_value = []
        state.documents.values.return_value = []
        state.documents_processed_since.return_value = []
        return _build_suggestion_body(suggestion_text, unread, state)

    def test_exact_title_match(self):
        unread = [{
            "title": "Attention Is All You Need",
            "metadata": {"url": "https://arxiv.org/abs/1706.03762", "tags": ["ml"]},
        }]
        body = self._build_body(
            "1. Attention Is All You Need — foundational transformer paper",
            unread,
        )
        assert "arxiv.org" in body

    def test_title_with_journal_suffix(self):
        """When Zotero title has '| Journal' but Claude omits it."""
        unread = [{
            "title": "A small polymerase with big potential | Science",
            "metadata": {"url": "https://doi.org/10.1126/science.xxx", "tags": ["biology"]},
        }]
        body = self._build_body(
            "1. A small polymerase with big potential — exciting enzyme work",
            unread,
        )
        # Should still match despite missing "| Science" suffix
        assert "doi.org" in body

    def test_no_match_for_unrelated(self):
        unread = [{
            "title": "Totally Different Paper",
            "metadata": {"url": "https://example.com", "tags": []},
        }]
        body = self._build_body(
            "1. Some Other Paper — interesting stuff",
            unread,
        )
        # URL should NOT appear since titles don't match
        assert "example.com" not in body

    def test_pending_picks_journal_suffix(self):
        """Title stripping at ingestion means matching works directly."""
        import re

        # After stripping, title no longer has "| Science"
        result = "1. A small polymerase with big potential — exciting work"
        title_to_key = {"a small polymerase with big potential": "KEY_A"}
        pending = []
        for line in result.strip().split("\n"):
            clean = line.strip().replace("**", "")
            if not clean:
                continue
            clean_lower = clean.lower()
            suggestion_title = re.sub(r"^\d+\.\s*", "", clean_lower).rstrip(" —-").split(" — ")[0].strip()
            for title_lower, key in title_to_key.items():
                if (title_lower in clean_lower or suggestion_title in title_lower) and key not in pending:
                    pending.append(key)
                    break

        assert "KEY_A" in pending


# ---------------------------------------------------------------------------
# Title stripping (| Journal suffix)
# ---------------------------------------------------------------------------


class TestTitleStripping:
    """Tests for journal suffix stripping in extract_metadata."""

    def test_strips_pipe_journal(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {"title": "A cool finding | Science", "creators": []}}
        meta = extract_metadata(item)
        assert meta["title"] == "A cool finding"

    def test_preserves_title_without_pipe(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {"title": "Normal Paper Title", "creators": []}}
        meta = extract_metadata(item)
        assert meta["title"] == "Normal Paper Title"

    def test_strips_only_last_pipe(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {"title": "A | B | Nature", "creators": []}}
        meta = extract_metadata(item)
        assert meta["title"] == "A | B"

    def test_strips_author_prefix_lastname(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {
            "title": "Dario Amodei — Machines of Loving Grace",
            "creators": [{"creatorType": "author", "firstName": "Dario", "lastName": "Amodei"}],
        }}
        meta = extract_metadata(item)
        assert meta["title"] == "Machines of Loving Grace"

    def test_strips_author_prefix_full_name(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {
            "title": "John Smith — A Great Paper",
            "creators": [{"creatorType": "author", "name": "John Smith"}],
        }}
        meta = extract_metadata(item)
        assert meta["title"] == "A Great Paper"

    def test_preserves_emdash_when_not_author(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {
            "title": "Methods — Results and Discussion",
            "creators": [{"creatorType": "author", "lastName": "Jones"}],
        }}
        meta = extract_metadata(item)
        assert meta["title"] == "Methods — Results and Discussion"

    def test_preserves_emdash_no_creators(self):
        from distillate.zotero_client import extract_metadata

        item = {"data": {
            "title": "Something — Other Thing",
            "creators": [],
        }}
        meta = extract_metadata(item)
        assert meta["title"] == "Something — Other Thing"


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
        assert "78% engaged" in html
        assert "12 highlights" in html
        assert "450 words" in html
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
