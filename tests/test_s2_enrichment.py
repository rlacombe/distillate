# Covers: distillate/semantic_scholar.py
"""Tests for Semantic Scholar metadata enrichment."""


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


# ---------------------------------------------------------------------------
# Migrated from test_v032.py
# ---------------------------------------------------------------------------


class TestS2FieldsOfStudy:
    """enrich_metadata() should populate tags from S2 fieldsOfStudy."""

    def test_fills_empty_tags(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"tags": []}
        s2 = {
            "citation_count": 10, "influential_citation_count": 1,
            "s2_url": "", "fields_of_study": ["Computer Science", "Medicine"],
        }
        result = enrich_metadata(meta, s2)
        assert result["tags"] == ["Computer Science", "Medicine"]

    def test_merges_into_existing_tags(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"tags": ["existing-tag"]}
        s2 = {
            "citation_count": 10, "influential_citation_count": 1,
            "s2_url": "", "fields_of_study": ["Physics"],
        }
        result = enrich_metadata(meta, s2)
        assert "existing-tag" in result["tags"]
        assert "Physics" in result["tags"]

    def test_no_duplicate_merge(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"tags": ["Biology"]}
        s2 = {
            "citation_count": 5, "influential_citation_count": 0,
            "s2_url": "", "fields_of_study": ["Biology", "Medicine"],
        }
        result = enrich_metadata(meta, s2)
        assert result["tags"].count("Biology") == 1
        assert "Medicine" in result["tags"]


# ---------------------------------------------------------------------------
# Migrated from test_v070.py
# ---------------------------------------------------------------------------

from unittest.mock import patch


class TestS2TldrFallback:
    def test_tldr_extracted_from_lookup(self):
        from distillate.semantic_scholar import lookup_paper
        paper_data = {
            "citationCount": 10,
            "influentialCitationCount": 2,
            "url": "https://example.com",
            "tldr": {"text": "This paper does X."},
            "publicationDate": "2025-01-01",
            "venue": "NeurIPS",
            "year": 2025,
            "fieldsOfStudy": ["Computer Science"],
            "authors": [{"name": "Alice"}],
        }
        with patch("distillate.semantic_scholar._fetch_by_id", return_value=paper_data):
            result = lookup_paper(doi="10.48550/arXiv.2501.00001")
        assert result["tldr"] == "This paper does X."

    def test_tldr_stored_in_enrich_metadata(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"citation_count": 0}
        s2_data = {
            "citation_count": 5,
            "influential_citation_count": 1,
            "s2_url": "https://s2.com",
            "tldr": "Paper introduces Y.",
            "publication_date": "",
            "venue": "",
            "year": 0,
            "fields_of_study": [],
            "authors": [],
        }
        enrich_metadata(meta, s2_data)
        assert meta["s2_tldr"] == "Paper introduces Y."

    def test_existing_s2_tldr_not_overwritten(self):
        from distillate.semantic_scholar import enrich_metadata
        meta = {"citation_count": 0, "s2_tldr": "Existing TLDR."}
        s2_data = {
            "citation_count": 5,
            "influential_citation_count": 1,
            "s2_url": "https://s2.com",
            "tldr": "New TLDR.",
            "publication_date": "",
            "venue": "",
            "year": 0,
            "fields_of_study": [],
            "authors": [],
        }
        enrich_metadata(meta, s2_data)
        assert meta["s2_tldr"] == "Existing TLDR."
