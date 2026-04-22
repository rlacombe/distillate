# Covers: distillate/routes/papers.py — GET /papers/{key}/pdf
# PDF serving: local cache, Obsidian Saved/Inbox, recursive glob, Zotero download.

import importlib.util
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A FastAPI TestClient wired to an isolated state + config dir."""
    monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
    monkeypatch.setattr("distillate.config.CONFIG_DIR", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir(parents=True, exist_ok=True)

    from starlette.testclient import TestClient
    from distillate.server import _create_app
    app = _create_app()
    return TestClient(app)


def _seed_paper(key="PAPER1", att_key="ATT1", citekey="foo2025"):
    """Seed a single-paper state so the routes have something to look up."""
    from distillate.state import State
    state = State()
    state._data["documents"] = {
        key: {
            "zotero_item_key": key,
            "zotero_attachment_key": att_key,
            "title": "Test Paper",
            "status": "on_remarkable",
            "authors": ["Alice"],
            "metadata": {"citekey": citekey, "url": "https://example.com/paper.pdf"},
        },
    }
    state.save()
    return state


# ---------------------------------------------------------------------------
# /papers/{key}/pdf — cached + remote paths
# ---------------------------------------------------------------------------


class TestPaperPdfEndpoint:

    def test_404_when_paper_unknown(self, client):
        resp = client.get("/papers/NOPE/pdf")
        assert resp.status_code == 404
        assert resp.json()["reason"] == "not_found"

    def test_serves_pdf_from_pdf_cache(self, client, tmp_path):
        """If CONFIG_DIR/pdf_cache/{att_key}.pdf exists, return it directly
        without hitting Zotero."""
        _seed_paper(att_key="ATT1")
        cache = tmp_path / "cfg" / "pdf_cache"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "ATT1.pdf").write_bytes(b"%PDF-CACHED")

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            resp = client.get("/papers/PAPER1/pdf")
            mock_fetch.assert_not_called()

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == b"%PDF-CACHED"

    def test_serves_pdf_from_obsidian_saved(self, client, tmp_path, monkeypatch):
        """If Obsidian's Saved/pdf/{citekey}.pdf exists, prefer it over a
        remote fetch (it's the annotated version produced by the pipeline)."""
        _seed_paper(citekey="foo2025")
        pdf_dir = tmp_path / "vault" / "papers" / "Saved" / "pdf"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        (pdf_dir / "foo2025.pdf").write_bytes(b"%PDF-ANNOTATED")

        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: pdf_dir)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            resp = client.get("/papers/PAPER1/pdf")
            mock_fetch.assert_not_called()

        assert resp.status_code == 200
        assert resp.content == b"%PDF-ANNOTATED"

    def test_serves_unread_pdf_from_obsidian_inbox(self, client, tmp_path, monkeypatch):
        """Unread papers have their PDFs in {vault}/Inbox/{citekey}.pdf —
        this is the primary hit path for the desktop reader since the
        Saved/pdf/ folder only exists post-processing."""
        _seed_paper(citekey="foo2025")
        inbox = tmp_path / "vault" / "papers" / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "foo2025.pdf").write_bytes(b"%PDF-INBOX")

        monkeypatch.setattr(
            "distillate.obsidian._pdf_dir",
            lambda: tmp_path / "vault" / "papers" / "Saved" / "pdf",
        )
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: inbox)

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            resp = client.get("/papers/PAPER1/pdf")
            mock_fetch.assert_not_called()

        assert resp.status_code == 200
        assert resp.content == b"%PDF-INBOX"

    def test_recursive_glob_finds_pdf_in_custom_layout(
        self, client, tmp_path, monkeypatch,
    ):
        """Some users stash PDFs directly under {vault}/{papers_folder}/
        (no Saved/pdf or Inbox subfolder). The recursive glob fallback
        should find them anywhere under the papers folder."""
        _seed_paper(citekey="foo2025")
        papers = tmp_path / "vault" / "Distillate"
        weird = papers / "CustomFolder" / "deep"
        weird.mkdir(parents=True, exist_ok=True)
        (weird / "foo2025.pdf").write_bytes(b"%PDF-DEEP")

        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._papers_dir", lambda: papers)

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            resp = client.get("/papers/PAPER1/pdf")
            mock_fetch.assert_not_called()

        assert resp.status_code == 200
        assert resp.content == b"%PDF-DEEP"

    def test_missing_pdf_error_includes_diag(self, client, tmp_path, monkeypatch):
        """The no_local_pdf_and_zotero_unconfigured response should carry
        a diag block with vault_path, papers_dir, and stems — so the user
        can see what the server actually checked."""
        _seed_paper(citekey="foo2025")
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._papers_dir", lambda: None)
        monkeypatch.setattr("distillate.config.OBSIDIAN_VAULT_PATH", "/tmp/my_vault")
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")

        resp = client.get("/papers/PAPER1/pdf")
        assert resp.status_code == 404
        body = resp.json()
        assert body["reason"] == "no_local_pdf_and_zotero_unconfigured"
        assert "diag" in body
        assert body["diag"]["vault_path"] == "/tmp/my_vault"
        assert "foo2025" in body["diag"]["stems"]

    def test_inbox_fallback_uses_sanitized_title_when_no_citekey(
        self, client, tmp_path, monkeypatch,
    ):
        """Records that predate citekey population save PDFs as
        {sanitized_title}.pdf in Inbox — verify we pick them up."""
        _seed_paper(citekey="")  # no citekey
        inbox = tmp_path / "vault" / "papers" / "Inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        from distillate.obsidian import _sanitize_note_name
        sanitized = _sanitize_note_name("Test Paper")
        (inbox / f"{sanitized}.pdf").write_bytes(b"%PDF-LEGACY")

        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: inbox)

        resp = client.get("/papers/PAPER1/pdf")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-LEGACY"

    def test_downloads_from_zotero_when_uncached(self, client, tmp_path, monkeypatch):
        """With no local cache, the endpoint calls _fetch_pdf_bytes and then
        writes the bytes into the pdf_cache directory for next time."""
        _seed_paper(att_key="ATT2")
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            mock_fetch.return_value = (b"%PDF-FETCHED", "ATT2")
            resp = client.get("/papers/PAPER1/pdf")

        assert resp.status_code == 200
        assert resp.content == b"%PDF-FETCHED"
        cache_file = tmp_path / "cfg" / "pdf_cache" / "ATT2.pdf"
        assert cache_file.exists()
        assert cache_file.read_bytes() == b"%PDF-FETCHED"

    def test_404_when_no_pdf_available(self, client, monkeypatch):
        _seed_paper()
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            mock_fetch.return_value = (None, "ATT1")
            resp = client.get("/papers/PAPER1/pdf")
        assert resp.status_code == 404
        assert resp.json()["reason"] == "no_pdf_available"

    def test_updates_attachment_key_if_fresh_one_found(self, client, monkeypatch):
        """When _fetch_pdf_bytes returns a newer attachment key (because
        check_fresh_attachment found one), persist it on the document."""
        _seed_paper(att_key="OLD_ATT")
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            mock_fetch.return_value = (b"%PDF-FRESH", "NEW_ATT")
            resp = client.get("/papers/PAPER1/pdf")

        assert resp.status_code == 200

        from distillate.state import State
        reloaded = State()
        assert reloaded.documents["PAPER1"]["zotero_attachment_key"] == "NEW_ATT"

    def test_short_circuits_when_zotero_unconfigured(self, client, monkeypatch):
        """If no local PDF exists AND Zotero credentials are missing, the
        endpoint must return a clean reason code (not a raw exception)."""
        _seed_paper()
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")

        with patch("distillate.pipeline._fetch_pdf_bytes") as mock_fetch:
            resp = client.get("/papers/PAPER1/pdf")
            mock_fetch.assert_not_called()

        assert resp.status_code == 404
        body = resp.json()
        assert body["reason"] == "no_local_pdf_and_zotero_unconfigured"
        assert "detail" not in body
        assert "zotero.org" not in resp.text

    def test_fetch_failure_returns_reason_without_leaking_url(
        self, client, monkeypatch,
    ):
        """An HTTPError from the Zotero client must be caught and returned
        as a reason code, never a URL-laden exception string."""
        _seed_paper()
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")

        import requests

        def _boom(*a, **kw):
            raise requests.exceptions.HTTPError(
                "404 Client Error: Not Found for url: "
                "https://api.zotero.org/users//items/EYST5ZN5/children"
            )

        with patch("distillate.pipeline._fetch_pdf_bytes", side_effect=_boom):
            resp = client.get("/papers/PAPER1/pdf")

        assert resp.status_code == 502
        body = resp.json()
        assert body["reason"] == "fetch_failed"
        assert "detail" not in body
        assert "zotero.org" not in resp.text
