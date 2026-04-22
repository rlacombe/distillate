# Covers: distillate/routes/papers.py — GET/POST/DELETE /papers/{key}/annotations
# Annotation endpoints: fetch from Zotero/PDF, create, delete, round-trip, migration.

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


def _seed_paper_with_pdf(client, tmp_path, monkeypatch, att_key="ATT1"):
    """Seed a paper AND a minimal PDF at the Obsidian Inbox path so that
    _pdf_search_result() resolves. Returns (paper_key, pdf_path)."""
    import pymupdf
    _seed_paper(att_key=att_key)
    inbox = tmp_path / "vault" / "papers" / "Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    pdf_path = inbox / "foo2025.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Some body text for highlighting.", fontsize=12)
    doc.save(str(pdf_path))
    doc.close()
    monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: inbox)
    monkeypatch.setattr(
        "distillate.obsidian._pdf_dir",
        lambda: tmp_path / "vault" / "papers" / "Saved" / "pdf",
    )
    return "PAPER1", pdf_path


# ---------------------------------------------------------------------------
# /papers/{key}/annotations — GET
# ---------------------------------------------------------------------------


class TestPaperAnnotationsEndpoint:

    def test_404_when_paper_unknown(self, client):
        resp = client.get("/papers/NOPE/annotations")
        assert resp.status_code == 404

    def test_returns_empty_when_no_attachment_key(self, client):
        _seed_paper(att_key="")
        resp = client.get("/papers/PAPER1/annotations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["annotations"] == []

    def test_passes_through_raw_annotations(self, client, monkeypatch):
        """The endpoint must forward page_index + rects so the reader can
        convert coordinates on the client side."""
        _seed_paper()
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")

        fake_anns = [
            {
                "text": "An important claim.",
                "page_index": 0,
                "page_label": "1",
                "rects": [[72.0, 700.0, 540.0, 712.0]],
                "color": "#ffd400",
            },
            {
                "text": "A key result.",
                "page_index": 4,
                "page_label": "5",
                "rects": [[90.0, 400.0, 520.0, 412.0]],
                "color": "#ff6666",
            },
        ]
        with patch("distillate.zotero_client.get_raw_annotations") as mock_raw:
            mock_raw.return_value = fake_anns
            resp = client.get("/papers/PAPER1/annotations")

        assert resp.status_code == 200
        data = resp.json()
        assert data["annotations"] == fake_anns
        assert data["annotations"][0]["page_index"] == 0
        assert data["annotations"][0]["rects"] == [[72.0, 700.0, 540.0, 712.0]]

    @pytest.mark.slow
    def test_merges_pdf_and_zotero_with_dedup(self, client, tmp_path, monkeypatch):
        """GET should return the union of PDF-native highlights and Zotero
        highlights, deduplicated by normalized (text, page_index)."""
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        from distillate import highlight_io
        from pathlib import Path as _P
        pdf_path = _P(tmp_path) / "vault" / "papers" / "Inbox" / "foo2025.pdf"
        pdf_id = highlight_io.add_highlight(
            pdf_path, 0, [[72.0, 700.0, 300.0, 712.0]],
            text="common sentence", color="#ffd400",
        )
        assert pdf_id

        zotero_anns = [
            {
                "text": "common sentence",  # duplicate of PDF one
                "page_index": 0,
                "page_label": "1",
                "rects": [[72.0, 700.0, 300.0, 712.0]],
                "color": "#ffd400",
            },
            {
                "text": "unique to zotero",
                "page_index": 1,
                "page_label": "2",
                "rects": [[72.0, 500.0, 300.0, 512.0]],
                "color": "#66ff66",
            },
        ]
        with patch("distillate.zotero_client.get_raw_annotations") as mock_raw:
            mock_raw.return_value = zotero_anns
            resp = client.get("/papers/PAPER1/annotations")

        assert resp.status_code == 200
        anns = resp.json()["annotations"]
        texts = [a["text"] for a in anns]
        assert texts.count("common sentence") == 1
        assert "unique to zotero" in texts
        assert len(anns) == 2


# ---------------------------------------------------------------------------
# POST /papers/{key}/annotations — PDF-native highlights + Zotero side-effect
# ---------------------------------------------------------------------------


class TestCreatePaperAnnotation:

    _SAMPLE = {
        "text": "An important sentence.",
        "page_index": 0,
        "page_label": "1",
        "rects": [[72.0, 700.0, 300.0, 712.0]],
        "color": "#ffd400",
    }

    def test_404_when_paper_unknown(self, client):
        resp = client.post(
            "/papers/NOPE/annotations",
            json={"highlights": [self._SAMPLE]},
        )
        assert resp.status_code == 404

    def test_rejects_missing_highlights(self, client):
        _seed_paper()
        resp = client.post("/papers/PAPER1/annotations", json={})
        assert resp.status_code == 400
        assert resp.json()["reason"] == "missing_highlights"

    def test_rejects_invalid_highlight(self, client):
        _seed_paper()
        resp = client.post(
            "/papers/PAPER1/annotations",
            json={"highlights": [{"text": "missing rects"}]},
        )
        assert resp.status_code == 400
        assert resp.json()["reason"] == "invalid_highlight"

    def test_rejects_null_rect_values(self, client):
        """Rects with null entries (from JSON.stringify(NaN)) would
        otherwise TypeError deep inside PyMuPDF and surface as the
        unhelpful 'no ids' error. Reject them at the boundary."""
        _seed_paper()
        resp = client.post(
            "/papers/PAPER1/annotations",
            json={"highlights": [{
                "text": "bad",
                "page_index": 0,
                "rects": [[None, None, None, None]],
            }]},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["reason"] == "invalid_rect_values"
        assert "NaN" in (data.get("hint") or "") or "null" in (data.get("hint") or "")

    def test_rejects_short_rect(self, client):
        _seed_paper()
        resp = client.post(
            "/papers/PAPER1/annotations",
            json={"highlights": [{
                "text": "short",
                "page_index": 0,
                "rects": [[1.0, 2.0, 3.0]],  # 3 elements instead of 4
            }]},
        )
        assert resp.status_code == 400
        assert resp.json()["reason"] == "invalid_rect_values"

    def test_rejects_when_no_local_pdf(self, client, tmp_path, monkeypatch):
        """Without a cached PDF the highlight has nowhere to live."""
        _seed_paper()
        monkeypatch.setattr("distillate.obsidian._pdf_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._inbox_dir", lambda: None)
        monkeypatch.setattr("distillate.obsidian._papers_dir", lambda: None)
        resp = client.post(
            "/papers/PAPER1/annotations",
            json={"highlights": [self._SAMPLE]},
        )
        assert resp.status_code == 400
        assert resp.json()["reason"] == "no_local_pdf"

    @pytest.mark.slow
    def test_saves_to_pdf(self, client, tmp_path, monkeypatch):
        """Highlight is written as a native /Highlight annotation into the
        local PDF. The response carries the annot id (/NM)."""
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        with patch("distillate.zotero_client.add_user_highlights", return_value=[]):
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlights": [self._SAMPLE]},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["saved_to_pdf"] is True
        assert data["annot_ids"] and data["annot_ids"][0].startswith("distillate-")

    @pytest.mark.slow
    def test_single_highlight_shorthand(self, client, tmp_path, monkeypatch):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        with patch("distillate.zotero_client.add_user_highlights") as mock_add:
            mock_add.return_value = ["NEWKEY1"]
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlight": self._SAMPLE},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved_to_pdf"] is True
        assert data["synced_to_zotero"] is True
        assert data["zotero_keys"] == ["NEWKEY1"]

    @pytest.mark.slow
    def test_zotero_failure_still_saves_to_pdf(self, client, tmp_path, monkeypatch):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        with patch("distillate.zotero_client.add_user_highlights") as mock_add, \
             patch("distillate.zotero_client.get_pdf_attachment") as mock_get:
            mock_add.return_value = []
            mock_get.return_value = None
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlights": [self._SAMPLE]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["saved_to_pdf"] is True
        assert data["synced_to_zotero"] is False

    @pytest.mark.slow
    def test_zotero_failure_carries_status_for_warning_toast(
        self, client, tmp_path, monkeypatch,
    ):
        """User-decided UX: when PDF write succeeds but Zotero sync fails,
        the response must distinguish 'partial success' from 'full success'
        so the client can render a yellow warning toast instead of green.

        Contract: response includes ``zotero_status`` field with one of
        ``synced | failed | not_configured | not_attempted``.
        """
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        with patch("distillate.zotero_client.add_user_highlights") as mock_add, \
             patch("distillate.zotero_client.get_pdf_attachment") as mock_get:
            mock_add.return_value = []
            mock_get.return_value = None
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlights": [self._SAMPLE]},
            )
        data = resp.json()
        assert data["zotero_status"] == "failed"

    @pytest.mark.slow
    def test_zotero_success_carries_synced_status(
        self, client, tmp_path, monkeypatch,
    ):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        with patch("distillate.zotero_client.add_user_highlights") as mock_add:
            mock_add.return_value = ["NEWKEY1"]
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlights": [self._SAMPLE]},
            )
        data = resp.json()
        assert data["zotero_status"] == "synced"

    @pytest.mark.slow
    def test_no_zotero_creds_carries_not_configured_status(
        self, client, tmp_path, monkeypatch,
    ):
        """When Zotero creds aren't configured, status should reflect
        that (not 'failed') so the toast doesn't alarm the user about a
        feature they haven't set up."""
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")
        resp = client.post(
            "/papers/PAPER1/annotations",
            json={"highlights": [self._SAMPLE]},
        )
        data = resp.json()
        assert data["zotero_status"] == "not_configured"

    @pytest.mark.slow
    def test_no_attachment_key_carries_not_attempted_status(
        self, client, tmp_path, monkeypatch,
    ):
        """A paper with no Zotero attachment can't sync — status is
        'not_attempted' (distinct from 'failed')."""
        _seed_paper_with_pdf(client, tmp_path, monkeypatch, att_key="")
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        resp = client.post(
            "/papers/PAPER1/annotations",
            json={"highlights": [self._SAMPLE]},
        )
        data = resp.json()
        assert data["zotero_status"] == "not_attempted"

    @pytest.mark.slow
    def test_refreshes_stale_zotero_key(self, client, tmp_path, monkeypatch):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch, att_key="STALE")
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")
        with patch("distillate.zotero_client.add_user_highlights") as mock_add, \
             patch("distillate.zotero_client.get_pdf_attachment") as mock_get:
            mock_add.side_effect = [[], ["NEWKEY1"]]
            mock_get.return_value = {"key": "FRESH", "data": {}}
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlights": [self._SAMPLE]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["synced_to_zotero"] is True
        assert data["zotero_keys"] == ["NEWKEY1"]
        assert mock_add.call_count == 2


# ---------------------------------------------------------------------------
# DELETE /papers/{key}/annotations
# ---------------------------------------------------------------------------


class TestDeletePaperAnnotation:

    def test_404_when_paper_unknown(self, client):
        resp = client.request(
            "DELETE",
            "/papers/NOPE/annotations",
            json={"id": "distillate-abc"},
        )
        assert resp.status_code == 404

    def test_rejects_missing_target(self, client):
        _seed_paper()
        resp = client.request("DELETE", "/papers/PAPER1/annotations", json={})
        assert resp.status_code == 400
        assert resp.json()["reason"] == "missing_target"

    @pytest.mark.slow
    def test_deletes_pdf_highlight_by_id(self, client, tmp_path, monkeypatch):
        """End-to-end: create a highlight, delete it by returned id,
        verify the PDF no longer contains it."""
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        sample = {
            "text": "hello",
            "page_index": 0,
            "rects": [[72.0, 700.0, 300.0, 712.0]],
            "color": "#ffd400",
        }
        with patch("distillate.zotero_client.add_user_highlights", return_value=[]):
            resp = client.post(
                "/papers/PAPER1/annotations",
                json={"highlight": sample},
            )
        annot_id = resp.json()["annot_ids"][0]

        with patch("distillate.zotero_client.delete_user_highlight", return_value=0):
            resp = client.request(
                "DELETE",
                "/papers/PAPER1/annotations",
                json={"id": annot_id, "text": "hello", "page_index": 0},
            )
        assert resp.status_code == 200
        assert resp.json()["removed_pdf"] is True

        from distillate import highlight_io
        from pathlib import Path as _P
        pdf_path = _P(tmp_path) / "vault" / "papers" / "Inbox" / "foo2025.pdf"
        assert highlight_io.read_highlights(pdf_path) == []
