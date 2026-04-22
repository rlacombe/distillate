# Covers: distillate/routes/papers.py — annotation round-trip (POST→GET→DELETE)
#         and legacy local_highlights migration on first GET /annotations.

import importlib.util

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
# Round-trip: POST then GET
# ---------------------------------------------------------------------------


class TestAnnotationRoundTrip:
    """End-to-end: POST an annotation, then GET it back, verify fields
    survive the round-trip. Closes the coverage gap where we had unit
    tests for each endpoint but no test that they compose correctly."""

    _SAMPLE = {
        "text": "An important sentence.",
        "page_index": 0,
        "page_label": "1",
        "rects": [[72.0, 700.0, 300.0, 712.0]],
        "color": "#ffd400",
    }

    @pytest.mark.slow
    def test_save_then_get_returns_the_highlight(
        self, client, tmp_path, monkeypatch,
    ):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")

        post = client.post(
            "/papers/PAPER1/annotations",
            json={"highlight": self._SAMPLE},
        )
        assert post.status_code == 200, post.text
        annot_id = post.json()["annot_ids"][0]

        get = client.get("/papers/PAPER1/annotations")
        assert get.status_code == 200
        anns = get.json()["annotations"]
        assert len(anns) == 1
        assert anns[0]["id"] == annot_id
        assert anns[0]["text"] == "An important sentence."
        assert anns[0]["page_index"] == 0
        # Rects survive the bottom-left ↔ top-left flip within ~1pt.
        r = anns[0]["rects"][0]
        assert abs(r[0] - 72.0) < 1
        assert abs(r[1] - 700.0) < 1
        assert abs(r[2] - 300.0) < 1
        assert abs(r[3] - 712.0) < 1

    @pytest.mark.slow
    def test_save_delete_then_get_returns_empty(
        self, client, tmp_path, monkeypatch,
    ):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")

        post = client.post(
            "/papers/PAPER1/annotations",
            json={"highlight": self._SAMPLE},
        )
        annot_id = post.json()["annot_ids"][0]

        delete = client.request(
            "DELETE",
            "/papers/PAPER1/annotations",
            json={"id": annot_id, "text": self._SAMPLE["text"], "page_index": 0},
        )
        assert delete.status_code == 200
        assert delete.json()["removed_pdf"] is True

        get = client.get("/papers/PAPER1/annotations")
        assert get.json()["annotations"] == []

    @pytest.mark.slow
    def test_overlapping_saves_merge(self, client, tmp_path, monkeypatch):
        """Overlap-extends end-to-end via the HTTP API."""
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")

        client.post("/papers/PAPER1/annotations", json={"highlight": {
            "text": "first half",
            "page_index": 0,
            "rects": [[100.0, 700.0, 300.0, 712.0]],
            "color": "#ffd400",
        }})
        client.post("/papers/PAPER1/annotations", json={"highlight": {
            "text": "extending right",
            "page_index": 0,
            "rects": [[200.0, 700.0, 400.0, 712.0]],
            "color": "#ffd400",
        }})
        anns = client.get("/papers/PAPER1/annotations").json()["annotations"]
        assert len(anns) == 1  # merged


# ---------------------------------------------------------------------------
# Legacy local_highlights migration
# ---------------------------------------------------------------------------


class TestLocalHighlightsMigrationEndToEnd:
    """Legacy local_highlights → PDF migration on first paper open.
    Verifies GET /annotations triggers migration AND returns the
    migrated highlights in the same call."""

    @pytest.mark.slow
    def test_first_get_migrates_and_returns(
        self, client, tmp_path, monkeypatch,
    ):
        _seed_paper_with_pdf(client, tmp_path, monkeypatch)
        monkeypatch.setattr("distillate.config.ZOTERO_API_KEY", "")
        monkeypatch.setattr("distillate.config.ZOTERO_USER_ID", "")

        # Inject a legacy local_highlights entry into the paper's state.
        from distillate.state import State
        state = State()
        doc = state.get_document("PAPER1")
        doc["local_highlights"] = [
            {
                "text": "legacy note",
                "page_index": 0,
                "rects": [[72.0, 700.0, 300.0, 712.0]],
                "color": "#ffd400",
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        state.save()

        get = client.get("/papers/PAPER1/annotations")
        assert get.status_code == 200
        anns = get.json()["annotations"]
        assert len(anns) == 1
        assert anns[0]["text"] == "legacy note"

        # State has been cleared (migration ran).
        state2 = State()
        doc2 = state2.get_document("PAPER1")
        assert doc2.get("local_highlights") in (None, [])
