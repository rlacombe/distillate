# Covers: distillate/routes/canvas.py (PATCH/DELETE/dir/compile-status/detect/resolve-citations)
"""HTTP route tests for canvas mutation, detection, and citation resolution.

Endpoints covered:
    PATCH  /workspaces/{ws}/canvases/{cv}                  — rename
    DELETE /workspaces/{ws}/canvases/{cv}                  — remove from state
    GET    /workspaces/{ws}/canvases/{cv}/dir              — sandbox info
    POST   /workspaces/{ws}/canvases/{cv}/compile-status   — compile freshness
    GET    /workspaces/{ws}/canvases/detect                — find existing files
    POST   /workspaces/{ws}/canvases/{cv}/resolve-citations — bibtex generator

Run: uv run pytest tests/test_canvas_mutate_detect.py -v
"""

import importlib.util
from pathlib import Path

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


@pytest.fixture
def project_root(tmp_path):
    """A throwaway project root directory the create endpoint can scaffold into."""
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _refresh_server_state():
    """Force the server's _context._state to reload from disk."""
    from distillate.routes import _context
    if _context._state is not None:
        _context._state.reload()


def _seed_workspace(ws_id="ws1", name="Test Project", root_path="", repos=None):
    """Seed a workspace into the isolated state. Returns the workspace dict."""
    from distillate.state import State
    state = State()
    state.add_workspace(
        ws_id, name=name, root_path=root_path,
        repos=repos or [],
    )
    state.save()
    _refresh_server_state()
    return state.get_workspace(ws_id)


def _seed_canvas(ws_id, canvas_dir, entry="main.tex", title="Main paper",
                 canvas_type="latex"):
    """Seed a canvas record (and its on-disk file)."""
    from distillate.state import State
    state = State()
    Path(canvas_dir).mkdir(parents=True, exist_ok=True)
    (Path(canvas_dir) / entry).write_text("% scaffolded\n", encoding="utf-8")
    cv = state.add_workspace_canvas(
        ws_id, title=title, canvas_type=canvas_type,
        directory=str(canvas_dir), entry=entry,
    )
    state.save()
    _refresh_server_state()
    return cv


def _seed_paper_into_state(citekey, title="A title", authors=None,
                           doi=None, arxiv_id=None):
    """Add a single paper to the documents store so the bib resolver finds it."""
    from distillate.state import State
    state = State()
    key = f"PAP_{citekey}"
    state._data["documents"][key] = {
        "zotero_item_key": key,
        "title": title,
        "authors": authors or ["Last, First"],
        "metadata": {
            "citekey": citekey,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "publication_date": "2024-01-01",
        },
    }
    state.save()
    _refresh_server_state()


# ───────────────────────────────────────────────────────────────────────────
# PATCH /workspaces/{ws}/canvases/{cv} — rename
# ───────────────────────────────────────────────────────────────────────────


class TestPatch:
    def test_renames_canvas_title(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a", title="Old")
        r = client.patch(
            f"/workspaces/ws1/canvases/{cv['id']}",
            json={"title": "New"},
        )
        body = r.json()
        assert body["ok"] is True
        assert body["canvas"]["title"] == "New"

    def test_ignores_disallowed_fields(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        r = client.patch(
            f"/workspaces/ws1/canvases/{cv['id']}",
            json={"dir": "/etc", "type": "binary", "id": "cv_evil"},
        )
        body = r.json()
        # No editable fields in the body → error.
        assert body["ok"] is False
        assert "No editable fields" in body["error"]
        # Confirm the canvas wasn't mutated.
        from distillate.state import State
        state = State()
        cv_after = state.get_workspace_canvas("ws1", cv["id"])
        assert cv_after["dir"] == cv["dir"]
        assert cv_after["type"] == cv["type"]
        assert cv_after["id"] == cv["id"]

    def test_unknown_canvas_returns_canvas_not_found(self, client):
        _seed_workspace()
        r = client.patch(
            "/workspaces/ws1/canvases/cv_ghost",
            json={"title": "Ghost"},
        )
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Canvas not found."

    def test_empty_title_falls_back_to_untitled(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a", title="Real")
        r = client.patch(
            f"/workspaces/ws1/canvases/{cv['id']}",
            json={"title": "  "},
        )
        body = r.json()
        assert body["ok"] is True
        assert body["canvas"]["title"] == "Untitled"


# ───────────────────────────────────────────────────────────────────────────
# DELETE /workspaces/{ws}/canvases/{cv} — remove from state
# ───────────────────────────────────────────────────────────────────────────


class TestDelete:
    def test_removes_from_state_but_leaves_files_on_disk(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        on_disk = Path(cv["dir"]) / cv["entry"]
        assert on_disk.is_file()

        r = client.delete(f"/workspaces/ws1/canvases/{cv['id']}")
        assert r.json()["ok"] is True

        # State is empty now.
        from distillate.state import State
        state = State()
        assert state.list_workspace_canvases("ws1") == []
        # File untouched.
        assert on_disk.is_file()

    def test_unknown_canvas_returns_canvas_not_found(self, client):
        _seed_workspace()
        r = client.delete("/workspaces/ws1/canvases/cv_ghost")
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Canvas not found."


# ───────────────────────────────────────────────────────────────────────────
# GET /workspaces/{ws}/canvases/{cv}/dir — sandbox info for IPC layer
# ───────────────────────────────────────────────────────────────────────────


class TestGetDir:
    def test_returns_dir_entry_type_exists(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a", entry="main.tex")
        r = client.get(f"/workspaces/ws1/canvases/{cv['id']}/dir")
        body = r.json()
        assert body["ok"] is True
        assert body["dir"] == cv["dir"]
        assert body["entry"] == "main.tex"
        assert body["type"] == "latex"
        assert body["exists"] is True

    def test_reports_exists_false_when_directory_missing(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "transient")
        # Remove the directory after seeding.
        import shutil
        shutil.rmtree(cv["dir"])
        r = client.get(f"/workspaces/ws1/canvases/{cv['id']}/dir")
        body = r.json()
        assert body["ok"] is True
        assert body["exists"] is False

    def test_unknown_canvas_returns_canvas_not_found(self, client):
        _seed_workspace()
        r = client.get("/workspaces/ws1/canvases/cv_ghost/dir")
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Canvas not found."


# ───────────────────────────────────────────────────────────────────────────
# POST /workspaces/{ws}/canvases/{cv}/compile-status — compile freshness
# ───────────────────────────────────────────────────────────────────────────


class TestCompileStatus:
    def test_records_freshness_on_canvas(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        r = client.post(
            f"/workspaces/ws1/canvases/{cv['id']}/compile-status",
            json={"ok": True, "duration_ms": 1234, "error_count": 0},
        )
        body = r.json()
        assert body["ok"] is True
        last = body["last_compile"]
        assert last["ok"] is True
        assert last["duration_ms"] == 1234
        assert last["error_count"] == 0
        assert last["at"]  # ISO timestamp present

    def test_unknown_canvas_returns_canvas_not_found(self, client):
        _seed_workspace()
        r = client.post(
            "/workspaces/ws1/canvases/cv_ghost/compile-status",
            json={"ok": False},
        )
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Canvas not found."


# ───────────────────────────────────────────────────────────────────────────
# GET /workspaces/{ws}/canvases/detect — find existing files in project
# ───────────────────────────────────────────────────────────────────────────


class TestDetect:
    def test_finds_tex_with_documentclass(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        (project_root / "paper").mkdir()
        (project_root / "paper" / "main.tex").write_text(
            r"\documentclass{article}" + "\nhi\n", encoding="utf-8")

        r = client.get("/workspaces/ws1/canvases/detect")
        body = r.json()
        assert body["ok"] is True
        rels = [c["rel"] for c in body["candidates"]]
        assert "paper/main.tex" in rels

    def test_skips_tex_without_documentclass(self, client, project_root):
        # A .tex include file (no documentclass) is NOT a standalone document.
        _seed_workspace(root_path=str(project_root))
        (project_root / "include.tex").write_text("\\section{stub}\n", encoding="utf-8")

        r = client.get("/workspaces/ws1/canvases/detect")
        body = r.json()
        rels = [c["rel"] for c in body["candidates"]]
        assert "include.tex" not in rels

    def test_finds_markdown_files(self, client, project_root):
        # Detect dedupes to one best-scored candidate per (folder, type).
        # Put each markdown variant in its own subfolder so both surface.
        _seed_workspace(root_path=str(project_root))
        (project_root / "a").mkdir()
        (project_root / "a" / "README.md").write_text("# Hello\n", encoding="utf-8")
        (project_root / "b").mkdir()
        (project_root / "b" / "notes.markdown").write_text("# Notes\n", encoding="utf-8")

        r = client.get("/workspaces/ws1/canvases/detect")
        body = r.json()
        rels = [c["rel"] for c in body["candidates"]]
        assert "a/README.md" in rels
        assert "b/notes.markdown" in rels

    def test_excludes_already_registered_canvases(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        # Scaffold a canvas at canvases/a/main.tex (and write the file)
        cv_dir = project_root / "canvases" / "a"
        cv_dir.mkdir(parents=True)
        (cv_dir / "main.tex").write_text(r"\documentclass{article}", encoding="utf-8")
        from distillate.state import State
        s = State()
        s.add_workspace_canvas("ws1", title="A", canvas_type="latex",
                               directory=str(cv_dir), entry="main.tex")
        s.save()
        _refresh_server_state()

        # Also drop an unregistered draft — should still appear.
        (project_root / "draft.tex").write_text(r"\documentclass{article}", encoding="utf-8")

        r = client.get("/workspaces/ws1/canvases/detect")
        body = r.json()
        rels = [c["rel"] for c in body["candidates"]]
        assert "draft.tex" in rels
        assert "canvases/a/main.tex" not in rels  # registered → excluded

    def test_skips_ignored_dirs(self, client, project_root):
        # node_modules / venv / build / .git etc. — never recursed into.
        _seed_workspace(root_path=str(project_root))
        for d in ("node_modules", "venv", "build", ".git"):
            (project_root / d).mkdir()
            (project_root / d / "junk.md").write_text("# junk", encoding="utf-8")

        r = client.get("/workspaces/ws1/canvases/detect")
        rels = [c["rel"] for c in r.json()["candidates"]]
        for d in ("node_modules", "venv", "build", ".git"):
            assert not any(rel.startswith(d + "/") for rel in rels), \
                f"{d} should be skipped, got {rels}"

    def test_returns_empty_when_no_root_path(self, client):
        # Workspace with no root_path AND no repos.
        _seed_workspace(root_path="")
        r = client.get("/workspaces/ws1/canvases/detect")
        body = r.json()
        assert body["ok"] is True
        assert body["candidates"] == []


# ───────────────────────────────────────────────────────────────────────────
# POST /workspaces/{ws}/canvases/{cv}/resolve-citations — bibtex generator
# ───────────────────────────────────────────────────────────────────────────


class TestResolveCitations:
    def test_writes_references_bib_with_resolved_entries(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        # Replace the scaffolded body with one that cites a known key.
        tex = Path(cv["dir"]) / cv["entry"]
        tex.write_text(r"\documentclass{article}\begin{document}\cite{kingma2014}\end{document}",
                       encoding="utf-8")
        _seed_paper_into_state("kingma2014", title="Adam optimizer")

        r = client.post(
            f"/workspaces/ws1/canvases/{cv['id']}/resolve-citations",
            json={},
        )
        body = r.json()
        assert body["ok"] is True
        assert "kingma2014" in body["resolved"]
        assert body["missing"] == []
        bib = (Path(cv["dir"]) / "references.bib").read_text(encoding="utf-8")
        assert "kingma2014" in bib
        assert "Adam optimizer" in bib

    def test_reports_missing_keys(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        tex = Path(cv["dir"]) / cv["entry"]
        tex.write_text(r"\documentclass{article}\begin{document}\cite{nonexistent2099}\end{document}",
                       encoding="utf-8")

        r = client.post(
            f"/workspaces/ws1/canvases/{cv['id']}/resolve-citations",
            json={},
        )
        body = r.json()
        assert body["ok"] is True
        assert body["resolved"] == []
        assert body["missing"] == ["nonexistent2099"]

    def test_handles_arxiv_prefix_keys(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        tex = Path(cv["dir"]) / cv["entry"]
        tex.write_text(r"\documentclass{article}\begin{document}\cite{arxiv:2401.12345}\end{document}",
                       encoding="utf-8")
        _seed_paper_into_state("foo", arxiv_id="2401.12345")

        r = client.post(
            f"/workspaces/ws1/canvases/{cv['id']}/resolve-citations",
            json={},
        )
        body = r.json()
        assert body["ok"] is True
        assert "arxiv:2401.12345" in body["resolved"]

    def test_handles_doi_keys(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a")
        tex = Path(cv["dir"]) / cv["entry"]
        tex.write_text(r"\documentclass{article}\begin{document}\cite{10.1234/foo}\end{document}",
                       encoding="utf-8")
        _seed_paper_into_state("foo", doi="10.1234/foo")

        r = client.post(
            f"/workspaces/ws1/canvases/{cv['id']}/resolve-citations",
            json={},
        )
        body = r.json()
        assert body["ok"] is True
        assert "10.1234/foo" in body["resolved"]

    def test_skips_when_canvas_is_not_latex(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        cv = _seed_canvas("ws1", project_root / "canvases" / "a",
                          entry="notes.md", title="Notes",
                          canvas_type="markdown")
        r = client.post(
            f"/workspaces/ws1/canvases/{cv['id']}/resolve-citations",
            json={},
        )
        body = r.json()
        assert body["ok"] is True
        assert body.get("skipped") is True

    def test_unknown_canvas_returns_canvas_not_found(self, client):
        _seed_workspace()
        r = client.post(
            "/workspaces/ws1/canvases/cv_ghost/resolve-citations",
            json={},
        )
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Canvas not found."
