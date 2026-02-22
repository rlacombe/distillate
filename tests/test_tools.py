"""Tests for distillate.tools — agent tool functions."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Mock State
# ---------------------------------------------------------------------------

class MockState:
    """Minimal State mock for tool testing."""

    def __init__(self, documents=None, promoted=None):
        self._documents = documents or {}
        self._promoted = promoted or []

    @property
    def documents(self):
        return self._documents

    @property
    def promoted_papers(self):
        return self._promoted

    def get_document(self, key):
        return self._documents.get(key)

    def index_of(self, key):
        for i, k in enumerate(self._documents, 1):
            if k == key:
                return i
        return 0

    def key_for_index(self, index):
        keys = list(self._documents.keys())
        if 1 <= index <= len(keys):
            return keys[index - 1]
        return None

    def documents_with_status(self, status):
        return [d for d in self._documents.values() if d["status"] == status]

    def documents_processed_since(self, since_iso):
        return sorted(
            [
                d for d in self._documents.values()
                if d["status"] == "processed" and (d.get("processed_at") or "") >= since_iso
            ],
            key=lambda d: d.get("processed_at", ""),
        )

    def find_by_citekey(self, ck):
        for d in self._documents.values():
            if d.get("metadata", {}).get("citekey") == ck:
                return d
        return None

    def reload(self):
        pass


def _make_doc(
    key="KEY1",
    title="Test Paper",
    citekey="author2024",
    status="processed",
    tags=None,
    engagement=75,
    highlight_count=10,
    page_count=20,
    citation_count=42,
    summary="A test summary.",
    uploaded_at=None,
    processed_at=None,
):
    now = datetime.now(timezone.utc)
    return {
        "zotero_item_key": key,
        "title": title,
        "authors": ["Test Author"],
        "status": status,
        "metadata": {
            "citekey": citekey,
            "tags": tags or ["ML", "NLP"],
            "doi": "10.1234/test",
            "url": "https://example.com",
            "journal": "Test Journal",
            "publication_date": "2024-01-15",
            "paper_type": "journalArticle",
            "citation_count": citation_count,
            "influential_citation_count": 5,
            "s2_url": "https://semanticscholar.org/paper/123",
            "abstract": "This is a test abstract.",
        },
        "summary": summary,
        "engagement": engagement,
        "highlight_count": highlight_count,
        "highlight_word_count": 500,
        "page_count": page_count,
        "uploaded_at": uploaded_at or (now - timedelta(days=10)).isoformat(),
        "processed_at": processed_at or (now - timedelta(days=2)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSearchPapers:
    def test_search_by_title(self):
        from distillate.tools import search_papers
        state = MockState({"K1": _make_doc(key="K1", title="Attention Is All You Need")})
        result = search_papers(state=state, query="Attention")
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Attention Is All You Need"

    def test_search_by_index(self):
        from distillate.tools import search_papers
        state = MockState({"K1": _make_doc(key="K1")})
        result = search_papers(state=state, query="1")
        assert result["total"] == 1
        assert result["results"][0]["index"] == 1

    def test_search_by_tag(self):
        from distillate.tools import search_papers
        state = MockState({
            "K1": _make_doc(key="K1", tags=["ML"]),
            "K2": _make_doc(key="K2", title="Other Paper", citekey="other2024", tags=["Biology"]),
        })
        result = search_papers(state=state, query="Biology")
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Other Paper"

    def test_search_with_status_filter(self):
        from distillate.tools import search_papers
        state = MockState({
            "K1": _make_doc(key="K1", status="processed"),
            "K2": _make_doc(key="K2", title="Queue Paper", citekey="q2024", status="on_remarkable"),
        })
        result = search_papers(state=state, query="Paper", status="on_remarkable")
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Queue Paper"

    def test_search_no_results(self):
        from distillate.tools import search_papers
        state = MockState({"K1": _make_doc(key="K1")})
        result = search_papers(state=state, query="nonexistent xyz")
        assert result["total"] == 0
        assert result["results"] == []


class TestGetPaperDetails:
    def test_found(self):
        from distillate.tools import get_paper_details
        state = MockState({"K1": _make_doc(key="K1")})
        result = get_paper_details(state=state, identifier="1")
        assert result["found"] is True
        assert result["paper"]["title"] == "Test Paper"
        assert result["paper"]["engagement"] == 75

    def test_not_found(self):
        from distillate.tools import get_paper_details
        state = MockState({})
        result = get_paper_details(state=state, identifier="nonexistent")
        assert result["found"] is False

    def test_reads_note_from_disk(self, tmp_path):
        from distillate.tools import get_paper_details
        state = MockState({"K1": _make_doc(key="K1", citekey="author2024")})

        # Create a fake note file
        saved_dir = tmp_path / "Saved"
        saved_dir.mkdir()
        note = saved_dir / "author2024.md"
        note.write_text(
            "# Test Paper\n\n## Highlights\n\n> Important finding\n\n"
            "## Summary\n\nA summary.",
            encoding="utf-8",
        )

        with patch("distillate.tools._read_note_content") as mock_read:
            mock_read.return_value = note.read_text()
            result = get_paper_details(state=state, identifier="1")

        assert result["found"] is True
        assert "Important finding" in result["highlights"]


class TestGetReadingStats:
    def test_basic_stats(self):
        from distillate.tools import get_reading_stats
        now = datetime.now(timezone.utc)
        state = MockState({
            "K1": _make_doc(
                key="K1", engagement=80, page_count=15,
                processed_at=(now - timedelta(days=5)).isoformat(),
            ),
            "K2": _make_doc(
                key="K2", title="P2", citekey="b2024", engagement=60,
                page_count=25,
                processed_at=(now - timedelta(days=3)).isoformat(),
            ),
            "K3": _make_doc(
                key="K3", title="P3", citekey="c2024",
                status="on_remarkable",
            ),
        })
        result = get_reading_stats(state=state, period_days=30)
        assert result["papers_read"] == 2
        assert result["total_pages"] == 40
        assert result["avg_engagement"] == 70
        assert result["queue_size"] == 1
        assert result["total_processed"] == 2

    def test_empty_library(self):
        from distillate.tools import get_reading_stats
        state = MockState({})
        result = get_reading_stats(state=state)
        assert result["papers_read"] == 0
        assert result["queue_size"] == 0


class TestGetQueue:
    def test_returns_queue_papers(self):
        from distillate.tools import get_queue
        state = MockState({
            "K1": _make_doc(key="K1", status="on_remarkable", citekey="q2024"),
            "K2": _make_doc(key="K2", title="Done", status="processed"),
        })
        result = get_queue(state=state)
        assert result["total"] == 1
        assert result["queue"][0]["title"] == "Test Paper"
        assert result["queue"][0]["days_in_queue"] >= 0

    def test_empty_queue(self):
        from distillate.tools import get_queue
        state = MockState({})
        result = get_queue(state=state)
        assert result["total"] == 0
        assert result["queue"] == []


class TestGetRecentReads:
    def test_returns_recent(self):
        from distillate.tools import get_recent_reads
        now = datetime.now(timezone.utc)
        state = MockState({
            "K1": _make_doc(
                key="K1", processed_at=(now - timedelta(days=1)).isoformat(),
            ),
            "K2": _make_doc(
                key="K2", title="P2", citekey="b2024",
                processed_at=(now - timedelta(days=5)).isoformat(),
            ),
        })
        result = get_recent_reads(state=state, count=10)
        assert len(result["papers"]) == 2
        # Most recent first
        assert result["papers"][0]["title"] == "Test Paper"

    def test_respects_count(self):
        from distillate.tools import get_recent_reads
        now = datetime.now(timezone.utc)
        docs = {}
        for i in range(5):
            key = f"K{i}"
            docs[key] = _make_doc(
                key=key, title=f"Paper {i}", citekey=f"p{i}",
                processed_at=(now - timedelta(days=i)).isoformat(),
            )
        state = MockState(docs)
        result = get_recent_reads(state=state, count=3)
        assert len(result["papers"]) == 3


class TestSynthesizeAcrossPapers:
    def test_calls_claude(self):
        from distillate.tools import synthesize_across_papers
        state = MockState({
            "K1": _make_doc(key="K1", title="Paper A"),
            "K2": _make_doc(key="K2", title="Paper B", citekey="b2024"),
        })

        with patch("distillate.summarizer._call_claude") as mock_claude:
            mock_claude.return_value = "Synthesis: A and B are related."
            result = synthesize_across_papers(
                state=state,
                paper_identifiers=["1", "2"],
                question="How are they related?",
            )

        assert "Synthesis" in result["synthesis"]
        assert len(result["papers_used"]) == 2
        mock_claude.assert_called_once()

    def test_no_matches(self):
        from distillate.tools import synthesize_across_papers
        state = MockState({})
        result = synthesize_across_papers(
            state=state,
            paper_identifiers=["nonexistent"],
            question="What?",
        )
        assert "error" in result


class TestToolSchemas:
    def test_all_schemas_valid(self):
        from distillate.tools import TOOL_SCHEMAS
        assert len(TOOL_SCHEMAS) == 11
        for schema in TOOL_SCHEMAS:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"

    def test_schema_names_match_functions(self):
        from distillate import tools
        from distillate.tools import TOOL_SCHEMAS
        for schema in TOOL_SCHEMAS:
            fn = getattr(tools, schema["name"], None)
            assert fn is not None, f"No function for tool '{schema['name']}'"
