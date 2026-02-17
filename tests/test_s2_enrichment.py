"""Tests for Semantic Scholar metadata enrichment."""

import pytest


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)


class TestEnrichMetadata:
    def test_fills_empty_publication_date(self):
        """S2 fills publication_date when Zotero lacks it."""
        from distillate.semantic_scholar import enrich_metadata

        meta = {"publication_date": "", "journal": "Nature", "citation_count": 0}
        s2_data = {
            "citation_count": 42,
            "influential_citation_count": 5,
            "s2_url": "https://s2.org/paper/123",
            "publication_date": "2024-06-15",
            "venue": "NeurIPS",
            "year": 2024,
        }
        result = enrich_metadata(meta, s2_data)
        assert result["publication_date"] == "2024-06-15"

    def test_does_not_overwrite_existing_date(self):
        """S2 does not overwrite existing Zotero publication_date."""
        from distillate.semantic_scholar import enrich_metadata

        meta = {"publication_date": "2025-01-01", "journal": "", "citation_count": 0}
        s2_data = {
            "citation_count": 10,
            "influential_citation_count": 1,
            "s2_url": "https://s2.org/paper/456",
            "publication_date": "2024-12-01",
            "venue": "ICML",
            "year": 2024,
        }
        result = enrich_metadata(meta, s2_data)
        assert result["publication_date"] == "2025-01-01"

    def test_fills_empty_journal_from_venue(self):
        """S2 fills journal when Zotero lacks it."""
        from distillate.semantic_scholar import enrich_metadata

        meta = {"publication_date": "2025", "journal": "", "citation_count": 0}
        s2_data = {
            "citation_count": 100,
            "influential_citation_count": 10,
            "s2_url": "https://s2.org/paper/789",
            "publication_date": "2025-03-01",
            "venue": "Nature Methods",
            "year": 2025,
        }
        result = enrich_metadata(meta, s2_data)
        assert result["journal"] == "Nature Methods"

    def test_does_not_overwrite_existing_journal(self):
        """S2 does not overwrite existing Zotero journal."""
        from distillate.semantic_scholar import enrich_metadata

        meta = {"publication_date": "", "journal": "Science", "citation_count": 0}
        s2_data = {
            "citation_count": 50,
            "influential_citation_count": 3,
            "s2_url": "https://s2.org/paper/abc",
            "publication_date": "2024-01-01",
            "venue": "AAAS Science",
            "year": 2024,
        }
        result = enrich_metadata(meta, s2_data)
        assert result["journal"] == "Science"

    def test_always_updates_citation_fields(self):
        """Citation fields are always updated (S2 authoritative)."""
        from distillate.semantic_scholar import enrich_metadata

        meta = {
            "publication_date": "2025",
            "journal": "Nature",
            "citation_count": 10,
            "influential_citation_count": 1,
            "s2_url": "old-url",
        }
        s2_data = {
            "citation_count": 42,
            "influential_citation_count": 5,
            "s2_url": "https://s2.org/paper/new",
            "publication_date": "",
            "venue": "",
            "year": 0,
        }
        result = enrich_metadata(meta, s2_data)
        assert result["citation_count"] == 42
        assert result["influential_citation_count"] == 5
        assert result["s2_url"] == "https://s2.org/paper/new"


class TestLookupPaperFields:
    def test_returns_new_fields(self, monkeypatch):
        """lookup_paper returns publication_date, venue, year."""
        import distillate.semantic_scholar as s2

        def fake_fetch(paper_id):
            return {
                "citationCount": 100,
                "influentialCitationCount": 10,
                "url": "https://s2.org/paper/123",
                "publicationDate": "2024-06-15",
                "venue": "NeurIPS",
                "year": 2024,
            }

        monkeypatch.setattr(s2, "_fetch_by_id", fake_fetch)
        result = s2.lookup_paper(doi="10.1234/test")

        assert result["publication_date"] == "2024-06-15"
        assert result["venue"] == "NeurIPS"
        assert result["year"] == 2024

    def test_missing_fields_default_empty(self, monkeypatch):
        """Missing S2 fields default to empty/zero."""
        import distillate.semantic_scholar as s2

        def fake_fetch(paper_id):
            return {
                "citationCount": 5,
                "influentialCitationCount": 0,
                "url": "https://s2.org/paper/456",
            }

        monkeypatch.setattr(s2, "_fetch_by_id", fake_fetch)
        result = s2.lookup_paper(doi="10.1234/test")

        assert result["publication_date"] == ""
        assert result["venue"] == ""
        assert result["year"] == 0


class TestCitekeyRegeneration:
    def test_citekey_regenerated_after_s2_date_fill(self):
        """Citekey is regenerated when S2 fills a missing date."""
        from distillate.zotero_client import _generate_citekey

        # Before S2: no date
        key1 = _generate_citekey(["Liu"], "Embeddings from language models", "")
        assert key1 == "liu_embeddings"

        # After S2 fills date
        key2 = _generate_citekey(["Liu"], "Embeddings from language models", "2024-06-15")
        assert key2 == "liu_embeddings_2024"
