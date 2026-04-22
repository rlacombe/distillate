# Covers: distillate/tools.py — synthesis, add_paper_to_zotero, delete_paper, schemas

"""Tests for distillate.tools — paper management and schema validation."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from distillate.state import State


@pytest.fixture(autouse=True)
def _legacy_remarkable_mode(monkeypatch):
    """These fixtures use status='on_remarkable'; force that reading mode."""
    monkeypatch.setattr("distillate.config.READING_SOURCE", "remarkable")


# ---------------------------------------------------------------------------
# Shared MockState (minimal copy for this file)
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


class TestAddPaperToZotero:
    def test_adds_paper_with_arxiv_id(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({})
        state.find_by_title = lambda t: None
        state.find_by_doi = lambda d: None

        hf_data = {
            "authors": ["Alice Smith", "Bob Jones"],
            "abstract": "An abstract.",
            "ai_keywords": ["ML"],
            "github_repo": "https://github.com/org/repo",
        }
        mock_arxiv = type("R", (), {"ok": False})()
        with patch("distillate.huggingface.lookup_paper", return_value=hf_data), \
             patch("requests.get", return_value=mock_arxiv), \
             patch("distillate.semantic_scholar.lookup_paper", return_value=None), \
             patch("distillate.zotero_client.create_paper", return_value="NEW1") as mock_create:
            result = add_paper_to_zotero(
                state=state, title="Test Paper", arxiv_id="2401.12345",
            )

        assert result["success"] is True
        assert result["item_key"] == "NEW1"
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["authors"] == ["Alice Smith", "Bob Jones"]
        assert call_kwargs["url"] == "https://arxiv.org/abs/2401.12345"
        assert call_kwargs["tags"] == ["ML"]

    def test_adds_paper_with_doi(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({})
        state.find_by_title = lambda t: None
        state.find_by_doi = lambda d: None

        with patch("distillate.semantic_scholar.lookup_paper", return_value=None), \
             patch("distillate.zotero_client.create_paper", return_value="NEW2") as mock_create:
            result = add_paper_to_zotero(
                state=state, title="DOI Paper",
                authors=["Alice"], doi="10.1234/test",
            )

        assert result["success"] is True
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["doi"] == "10.1234/test"
        assert call_kwargs["url"] == "https://doi.org/10.1234/test"

    def test_duplicate_detection_by_title(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({"K1": _make_doc(key="K1", title="Existing Paper")})
        state.find_by_title = lambda t: _make_doc(key="K1", title="Existing Paper")
        state.find_by_doi = lambda d: None

        with patch("distillate.semantic_scholar.lookup_paper", return_value=None):
            result = add_paper_to_zotero(
                state=state, title="Existing Paper",
            )
        assert result["success"] is False
        assert "already" in result["error"]

    def test_extracts_arxiv_from_url(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({})
        state.find_by_title = lambda t: None
        state.find_by_doi = lambda d: None

        hf_data = {
            "title": "Auto Title",
            "authors": ["Eve"],
            "abstract": "Abstract.",
            "ai_keywords": ["RL"],
        }
        mock_arxiv = type("R", (), {"ok": False})()
        with patch("distillate.huggingface.lookup_paper", return_value=hf_data) as mock_hf, \
             patch("requests.get", return_value=mock_arxiv), \
             patch("distillate.semantic_scholar.lookup_paper", return_value=None), \
             patch("distillate.zotero_client.create_paper", return_value="NEW3") as mock_create:
            result = add_paper_to_zotero(
                state=state, url="https://arxiv.org/abs/2401.99999",
            )

        assert result["success"] is True
        # Should have extracted arXiv ID and enriched from HF
        mock_hf.assert_called_once_with("2401.99999")
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["title"] == "Auto Title"
        assert call_kwargs["authors"] == ["Eve"]
        assert call_kwargs["url"] == "https://arxiv.org/abs/2401.99999"

    def test_falls_back_to_arxiv_api(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({})
        state.find_by_title = lambda t: None
        state.find_by_doi = lambda d: None

        arxiv_xml = """<?xml version="1.0"?>
        <feed><entry>
            <title>Mem0: Building Production-Ready AI Agents</title>
            <name>Alice</name><name>Bob</name>
            <summary>A memory system for agents.</summary>
        </entry></feed>"""
        mock_resp = type("R", (), {"ok": True, "text": arxiv_xml})()

        with patch("distillate.huggingface.lookup_paper", return_value=None), \
             patch("requests.get", return_value=mock_resp), \
             patch("distillate.semantic_scholar.lookup_paper", return_value=None), \
             patch("distillate.zotero_client.create_paper", return_value="NEW4") as mock_create:
            result = add_paper_to_zotero(
                state=state, arxiv_id="2504.19413",
            )

        assert result["success"] is True
        call_kwargs = mock_create.call_args[1]
        assert "Mem0" in call_kwargs["title"]
        assert call_kwargs["authors"] == ["Alice", "Bob"]

    def test_missing_title_returns_error(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({})
        result = add_paper_to_zotero(state=state, doi="10.1234/test")
        assert result["success"] is False
        assert "title" in result["error"].lower()

    def test_zotero_create_failure(self):
        from distillate.tools import add_paper_to_zotero
        state = MockState({})
        state.find_by_title = lambda t: None
        state.find_by_doi = lambda d: None

        with patch("distillate.semantic_scholar.lookup_paper", return_value=None), \
             patch("distillate.zotero_client.create_paper", return_value=None):
            result = add_paper_to_zotero(
                state=state, title="Failing Paper", authors=["Author"],
            )
        assert result["success"] is False
        assert "Failed" in result["error"]


class TestToolSchemas:
    def test_all_schemas_valid(self):
        from distillate.tools import TOOL_SCHEMAS
        assert len(TOOL_SCHEMAS) >= 14
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


@pytest.fixture()
def real_state(tmp_path, monkeypatch):
    """Provide a real State backed by a temp directory."""
    import distillate.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(state_mod, "LOCK_PATH", tmp_path / "state.lock")
    return State()


def _add_test_doc(state, key="KEY1", title="Test Paper"):
    """Helper to add a minimal document to state for testing."""
    state.add_document(
        zotero_item_key=key,
        zotero_attachment_key=f"{key}_att",
        zotero_attachment_md5="abc123",
        remarkable_doc_name=title,
        title=title,
        authors=["Author"],
        status="tracked",
    )


class TestDeletePaper:
    def test_delete_not_found(self, real_state):
        from distillate.tools import delete_paper
        result = delete_paper(state=real_state, identifier="nonexistent", confirm=True)
        assert "error" in result

    def test_delete_requires_confirm(self, real_state):
        from distillate.tools import delete_paper
        _add_test_doc(real_state)
        real_state.save()

        result = delete_paper(state=real_state, identifier="Test Paper", confirm=False)
        assert "action_required" in result
        assert real_state.has_document("KEY1")

    def test_delete_success(self, real_state, monkeypatch):
        from distillate.tools import delete_paper
        _add_test_doc(real_state)
        real_state.save()

        deleted_keys = []
        monkeypatch.setattr(
            "distillate.zotero_client.delete_item",
            lambda key: deleted_keys.append(key),
        )

        result = delete_paper(state=real_state, identifier="Test Paper", confirm=True)
        assert result["success"] is True
        assert result["deleted"] == "Test Paper"
        assert not real_state.has_document("KEY1")
        assert deleted_keys == ["KEY1"]
