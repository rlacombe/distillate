# Covers: distillate/routes/papers.py — read-position, mark-read, detail,
#         pipeline/highlight coexistence, POST /papers/import (PDF drag-drop upload)

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


def _minimal_pdf_bytes() -> bytes:
    """Return a minimal but valid PDF byte string for upload tests."""
    import pymupdf
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Dropped Paper"})
    return doc.tobytes()


# ---------------------------------------------------------------------------
# /papers/{key}/read-position (GET + POST)
# ---------------------------------------------------------------------------


class TestReadPositionEndpoint:

    def test_get_defaults_to_page_1(self, client):
        _seed_paper()
        resp = client.get("/papers/PAPER1/read-position")
        assert resp.status_code == 200
        assert resp.json()["page"] == 1

    def test_post_persists_page(self, client):
        _seed_paper()
        resp = client.post(
            "/papers/PAPER1/read-position",
            json={"page": 7},
        )
        assert resp.status_code == 200
        assert resp.json()["page"] == 7

        from distillate.state import State
        reloaded = State()
        doc = reloaded.get_document("PAPER1")
        assert doc["last_read_page"] == 7
        assert doc["last_read_at"]  # ISO timestamp was set

    def test_get_returns_persisted_page(self, client):
        _seed_paper()
        client.post("/papers/PAPER1/read-position", json={"page": 12})
        resp = client.get("/papers/PAPER1/read-position")
        assert resp.json()["page"] == 12

    def test_post_clamps_page_below_one(self, client):
        _seed_paper()
        resp = client.post("/papers/PAPER1/read-position", json={"page": 0})
        assert resp.status_code == 200
        from distillate.state import State
        assert State().get_document("PAPER1")["last_read_page"] == 1

    def test_post_rejects_unknown_paper(self, client):
        resp = client.post(
            "/papers/NOPE/read-position",
            json={"page": 3},
        )
        assert resp.status_code == 404
        assert resp.json()["reason"] == "not_found"


# ---------------------------------------------------------------------------
# /papers/{key} — last_read_page is exposed on the detail response so the
# "Resume reading (p. N)" button label works.
# ---------------------------------------------------------------------------


class TestPaperDetailExposesReadPosition:

    def test_detail_includes_last_read_page(self, client):
        _seed_paper()
        client.post("/papers/PAPER1/read-position", json={"page": 9})
        resp = client.get("/papers/PAPER1")
        assert resp.status_code == 200
        paper = resp.json()["paper"]
        assert paper["last_read_page"] == 9
        assert paper["last_read_at"]


# ---------------------------------------------------------------------------
# POST /papers/{key}/mark-read
# ---------------------------------------------------------------------------


class TestMarkRead:

    def test_404_when_paper_unknown(self, client):
        resp = client.post("/papers/NOPE/mark-read")
        assert resp.status_code == 404

    def test_marks_unread_paper_as_processed(self, client):
        _seed_paper()
        with patch("distillate.zotero_client.get_highlight_annotations", return_value={}), \
             patch("distillate.zotero_client.add_tag"):
            resp = client.post("/papers/PAPER1/mark-read")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        from distillate.state import State
        doc = State().get_document("PAPER1")
        assert doc["status"] == "processed"
        assert doc["processed_at"]

    def test_already_read_is_idempotent(self, client):
        _seed_paper()
        with patch("distillate.zotero_client.get_highlight_annotations", return_value={}), \
             patch("distillate.zotero_client.add_tag"):
            client.post("/papers/PAPER1/mark-read")
        resp = client.post("/papers/PAPER1/mark-read")
        assert resp.status_code == 200
        assert resp.json()["already"] is True

    def test_computes_engagement_from_highlights(self, client, monkeypatch):
        _seed_paper()
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "fake")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "12345")

        fake_highlights = {1: ["highlight one", "highlight two"], 2: ["highlight three"]}
        with patch("distillate.zotero_client.get_highlight_annotations") as mock_hl, \
             patch("distillate.zotero_client.add_tag"):
            mock_hl.return_value = fake_highlights
            resp = client.post("/papers/PAPER1/mark-read")

        assert resp.status_code == 200
        data = resp.json()
        assert data["engagement"] > 0
        assert data["highlight_count"] == 3


# ---------------------------------------------------------------------------
# Pipeline / user highlight coexistence
# ---------------------------------------------------------------------------


class TestPipelineCoexistsWithUserHighlights:
    """Regression guard for the interaction between the reMarkable
    pipeline's ``render_annotated_pdf_from_annotations`` and user
    highlights added via the in-app reader.

    The pipeline opens the existing PDF bytes (which already contain
    user annotations) and appends its own pipeline-tagged highlights.
    User annotations survive. This test pins that behaviour so if
    anyone changes the pipeline to regenerate a fresh PDF from scratch,
    the destructive change becomes visible in the suite."""

    @pytest.mark.slow
    def test_pipeline_preserves_user_highlights(self, tmp_path):
        import pymupdf
        from distillate import highlight_io, renderer

        doc = pymupdf.open()
        doc.new_page(width=612, height=792)
        path = tmp_path / "sample.pdf"
        doc.save(str(path))
        doc.close()

        highlight_io.add_highlight(
            path, 0, [[72.0, 700.0, 300.0, 712.0]],
            text="user made this", color="#ffd400",
        )
        assert len(highlight_io.read_highlights(path)) == 1

        pdf_bytes = path.read_bytes()
        renderer.render_annotated_pdf_from_annotations(
            pdf_bytes,
            [{
                "page_index": 0,
                "rects": [[72.0, 600.0, 300.0, 612.0]],
                "text": "pipeline made this",
                "color": "#ffd400",
            }],
            path,
        )

        remaining = highlight_io.read_highlights(path)
        user_texts = {h["text"] for h in remaining}
        assert "user made this" in user_texts, (
            "pipeline destroyed the user's highlight — pipeline now "
            "regenerates the PDF from scratch; needs preservation fix"
        )


# ---------------------------------------------------------------------------
# POST /papers/import — drag-drop PDF → Zotero import
# ---------------------------------------------------------------------------


class TestImportPaperUpload:

    def test_rejects_non_pdf(self, client):
        resp = client.post(
            "/papers/import",
            files={"file": ("note.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 400
        assert resp.json()["reason"] == "not_a_pdf"

    def test_rejects_empty_file(self, client):
        resp = client.post(
            "/papers/import",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        assert resp.status_code == 400
        assert resp.json()["reason"] == "empty_file"

    def test_creates_paper_and_returns_key(self, client):
        pdf_bytes = _minimal_pdf_bytes()
        with patch("distillate.zotero_client.create_paper") as mock_create, \
             patch("distillate.zotero_client.upload_pdf_attachment") as mock_upload:
            mock_create.return_value = "NEWPARENT"
            mock_upload.return_value = "NEWATT"
            resp = client.post(
                "/papers/import",
                files={"file": ("dropped.pdf", pdf_bytes, "application/pdf")},
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["paper_key"] == "NEWPARENT"
        assert data["title"] in ("Dropped Paper", "dropped")
        mock_create.assert_called_once()
        mock_upload.assert_called_once()
        upload_args = mock_upload.call_args[0]
        assert upload_args[0] == "NEWPARENT"
        assert upload_args[1] == "dropped.pdf"
        assert upload_args[2] == pdf_bytes

    def test_upload_failure_returns_502(self, client):
        pdf_bytes = _minimal_pdf_bytes()
        with patch("distillate.zotero_client.create_paper") as mock_create, \
             patch("distillate.zotero_client.upload_pdf_attachment") as mock_upload:
            mock_create.return_value = "NEWPARENT"
            mock_upload.return_value = None
            resp = client.post(
                "/papers/import",
                files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
            )
        assert resp.status_code == 502
        assert resp.json()["reason"] == "upload_failed"

    def test_parent_create_failure_returns_502(self, client):
        pdf_bytes = _minimal_pdf_bytes()
        with patch("distillate.zotero_client.create_paper") as mock_create:
            mock_create.return_value = None
            resp = client.post(
                "/papers/import",
                files={"file": ("x.pdf", pdf_bytes, "application/pdf")},
            )
        assert resp.status_code == 502
        assert resp.json()["reason"] == "parent_create_failed"
