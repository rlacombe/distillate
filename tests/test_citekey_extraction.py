# Covers: distillate/zotero_client.py

import pytest


# ---------------------------------------------------------------------------
# Citekey extraction from Zotero
# ---------------------------------------------------------------------------

class TestCitekeyExtraction:
    def test_citekey_from_extra_field(self, monkeypatch):
        """Better BibTeX citekey is parsed from the 'extra' field."""
        from distillate import zotero_client

        item = {
            "data": {
                "itemType": "journalArticle",
                "title": "Attention Is All You Need",
                "creators": [{"creatorType": "author", "lastName": "Vaswani", "firstName": "A."}],
                "date": "2017-06-12",
                "extra": "Citation Key: vaswani2017attention\nsome other data",
                "DOI": "",
                "publicationTitle": "",
                "url": "",
                "abstractNote": "",
                "tags": [],
            }
        }
        meta = zotero_client.extract_metadata(item)
        assert meta["citekey"] == "vaswani2017attention"

    def test_citekey_fallback_generation(self, monkeypatch):
        """When no Better BibTeX citekey, a fallback is generated."""
        from distillate import zotero_client

        item = {
            "data": {
                "itemType": "journalArticle",
                "title": "The Great Paper on AI",
                "creators": [{"creatorType": "author", "lastName": "Smith", "firstName": "J."}],
                "date": "2025-03-01",
                "extra": "",
                "DOI": "",
                "publicationTitle": "",
                "url": "",
                "abstractNote": "",
                "tags": [],
            }
        }
        meta = zotero_client.extract_metadata(item)
        assert meta["citekey"] == "smith_great_2025"

    def test_citekey_fallback_no_date(self):
        """Fallback citekey works without a date."""
        from distillate.zotero_client import _generate_citekey

        result = _generate_citekey(["Doe, J."], "Some Paper", "")
        assert result == "doe_some"

    def test_citekey_fallback_no_authors(self):
        """Fallback citekey works without authors."""
        from distillate.zotero_client import _generate_citekey

        result = _generate_citekey([], "Neural Networks", "2024")
        assert result == "unknown_neural_2024"

    def test_citekey_skips_stop_words(self):
        """Fallback citekey skips stop words in title."""
        from distillate.zotero_client import _generate_citekey

        result = _generate_citekey(["Author, A."], "A Study of the Effects", "2023")
        assert result == "author_study_2023"
