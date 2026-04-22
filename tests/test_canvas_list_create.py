# Covers: distillate/routes/canvas.py (GET /canvases list, POST /canvases create + import)
"""HTTP route tests for canvas list and create endpoints.

Endpoints covered:
    GET    /workspaces/{ws}/canvases                       — list
    POST   /workspaces/{ws}/canvases                       — create (scaffold) or import

Run: uv run pytest tests/test_canvas_list_create.py -v
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


# ───────────────────────────────────────────────────────────────────────────
# GET /workspaces/{ws}/canvases — list canvases
# ───────────────────────────────────────────────────────────────────────────


class TestList:
    def test_returns_empty_array_for_workspace_with_no_canvases(self, client):
        _seed_workspace()
        r = client.get("/workspaces/ws1/canvases")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["canvases"] == []

    def test_returns_canvases_in_creation_order(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        _seed_canvas("ws1", project_root / "canvases" / "a", title="A")
        _seed_canvas("ws1", project_root / "canvases" / "b", title="B",
                     entry="b.md", canvas_type="markdown")
        r = client.get("/workspaces/ws1/canvases")
        body = r.json()
        assert body["ok"] is True
        titles = [c["title"] for c in body["canvases"]]
        assert titles == ["A", "B"]
        ids = [c["id"] for c in body["canvases"]]
        assert ids == ["cv_001", "cv_002"]

    def test_unknown_workspace_returns_project_not_found(self, client):
        # No workspace seeded → ws1 doesn't exist.
        r = client.get("/workspaces/ws1/canvases")
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Project not found."

    def test_terminal_key_shaped_id_returns_project_not_found(self, client):
        # B1 regression at the route level: a session terminal key like
        # "ws_endlessbench_sess001" must NOT match the actual workspace id
        # "endlessbench". Bug history: layout.js sent the terminal key as
        # the workspace_id and got "Project not found"; this test pins the
        # backend's behavior so the contract is defended on both sides.
        _seed_workspace(ws_id="endlessbench")
        r = client.get("/workspaces/ws_endlessbench_sess001/canvases")
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Project not found."

    def test_migrates_legacy_singular_writeup_field(self, client, tmp_path):
        # Legacy state shape: ws["writeup"] = {dir, title} (singular).
        # First touch via the list route should migrate it into ws["canvases"].
        from distillate.state import State
        state = State()
        state.add_workspace("ws1", name="X")
        ws = state.workspaces["ws1"]
        legacy_dir = tmp_path / "legacy"
        legacy_dir.mkdir()
        (legacy_dir / "main.tex").write_text(r"\documentclass{article}", encoding="utf-8")
        ws["writeup"] = {"dir": str(legacy_dir), "title": "Legacy paper"}
        state.save()
        _refresh_server_state()

        r = client.get("/workspaces/ws1/canvases")
        body = r.json()
        assert body["ok"] is True
        assert len(body["canvases"]) == 1
        # Migration hardcodes title to "Canvas" — the legacy title field
        # is intentionally not preserved (state.py:_ensure_canvases_migrated).
        assert body["canvases"][0]["title"] == "Canvas"
        assert body["canvases"][0]["type"] == "latex"
        assert body["canvases"][0]["dir"] == str(legacy_dir)

    def test_migrates_legacy_plural_writeups_dict(self, client, tmp_path):
        from distillate.state import State
        state = State()
        state.add_workspace("ws1", name="X")
        ws = state.workspaces["ws1"]
        d1 = tmp_path / "wu1"; d1.mkdir(); (d1 / "a.md").write_text("# A", encoding="utf-8")
        d2 = tmp_path / "wu2"; d2.mkdir(); (d2 / "b.tex").write_text(r"\documentclass{article}", encoding="utf-8")
        ws["writeups"] = {
            "wu_001": {"id": "wu_001", "title": "A", "dir": str(d1), "entry": "a.md", "type": "markdown"},
            "wu_002": {"id": "wu_002", "title": "B", "dir": str(d2), "entry": "b.tex", "type": "latex"},
        }
        state.save()
        _refresh_server_state()

        r = client.get("/workspaces/ws1/canvases")
        body = r.json()
        assert body["ok"] is True
        titles = sorted(c["title"] for c in body["canvases"])
        assert titles == ["A", "B"]


# ───────────────────────────────────────────────────────────────────────────
# POST /workspaces/{ws}/canvases — create (scaffold)
# ───────────────────────────────────────────────────────────────────────────


class TestCreateScaffold:
    def test_latex_scaffolds_main_tex_with_template(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"title": "Main paper", "type": "latex"},
        )
        body = r.json()
        assert body["ok"] is True
        cv = body["canvas"]
        assert cv["type"] == "latex"
        assert cv["entry"] == "main.tex"

        scaffold = Path(cv["dir"]) / "main.tex"
        assert scaffold.is_file()
        text = scaffold.read_text(encoding="utf-8")
        assert r"\documentclass" in text
        assert r"\begin{document}" in text
        # Aux files / figures dir
        assert (Path(cv["dir"]) / ".gitignore").is_file()
        assert (Path(cv["dir"]) / "figures").is_dir()

    def test_markdown_scaffolds_md_file_with_h1(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"title": "Notes draft", "type": "markdown"},
        )
        body = r.json()
        assert body["ok"] is True
        cv = body["canvas"]
        assert cv["type"] == "markdown"
        assert cv["entry"].endswith(".md")
        text = (Path(cv["dir"]) / cv["entry"]).read_text(encoding="utf-8")
        assert text.startswith("# Notes draft")

    def test_plain_scaffolds_empty_txt_file(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"title": "Scratch", "type": "plain"},
        )
        body = r.json()
        assert body["ok"] is True
        assert body["canvas"]["type"] == "plain"
        assert body["canvas"]["entry"].endswith(".txt")

    def test_no_root_path_returns_root_path_required(self, client):
        # Workspace with no root_path AND no repos → can't infer where to scaffold.
        _seed_workspace(root_path="")
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"title": "P", "type": "latex"},
        )
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "root_path_required"
        assert "Link a repo" in body["message"]

    def test_repo_only_falls_back_to_repo_path(self, client, tmp_path):
        # No explicit root_path, but one linked repo → use the repo's path.
        repo = tmp_path / "myrepo"
        repo.mkdir()
        _seed_workspace(repos=[{"path": str(repo), "name": "myrepo"}])
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"title": "P", "type": "latex"},
        )
        body = r.json()
        assert body["ok"] is True
        # Scaffold lives under the repo path.
        assert str(repo) in body["canvas"]["dir"]

    def test_dedups_slug_when_directory_exists(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        # First create takes "my-paper"
        r1 = client.post("/workspaces/ws1/canvases", json={"title": "My paper", "type": "latex"})
        assert r1.json()["ok"] is True
        first_dir = r1.json()["canvas"]["dir"]
        # Second create with same title → "my-paper-2"
        r2 = client.post("/workspaces/ws1/canvases", json={"title": "My paper", "type": "latex"})
        assert r2.json()["ok"] is True
        second_dir = r2.json()["canvas"]["dir"]
        assert first_dir != second_dir
        assert second_dir.endswith("my-paper-2") or second_dir.endswith("my-paper-2/")

    def test_unknown_workspace_returns_project_not_found(self, client):
        r = client.post(
            "/workspaces/ghost/canvases",
            json={"title": "P", "type": "latex"},
        )
        body = r.json()
        assert body["ok"] is False
        assert body["error"] == "Project not found."

    def test_invalid_type_returns_unknown_type_error(self, client, project_root):
        _seed_workspace(root_path=str(project_root))
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"title": "P", "type": "binary"},
        )
        body = r.json()
        assert body["ok"] is False
        assert "Unknown type" in body["error"]


# ───────────────────────────────────────────────────────────────────────────
# POST /workspaces/{ws}/canvases — import existing file
# ───────────────────────────────────────────────────────────────────────────


class TestCreateImport:
    def test_existing_file_registers_without_scaffolding(self, client, tmp_path):
        target = tmp_path / "existing" / "draft.tex"
        target.parent.mkdir()
        target.write_text(r"\documentclass{article}\begin{document}hi\end{document}",
                          encoding="utf-8")
        original_text = target.read_text(encoding="utf-8")

        _seed_workspace()
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"import_path": str(target)},
        )
        body = r.json()
        assert body["ok"] is True
        assert body.get("imported") is True
        # Original file content untouched (no scaffold overwrite).
        assert target.read_text(encoding="utf-8") == original_text

    def test_nonexistent_path_returns_file_not_found(self, client):
        _seed_workspace()
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"import_path": "/nonexistent/does/not/exist.tex"},
        )
        body = r.json()
        assert body["ok"] is False
        assert "File not found" in body["error"]

    def test_directory_path_returns_not_a_regular_file(self, client, tmp_path):
        a_dir = tmp_path / "a-dir"
        a_dir.mkdir()
        _seed_workspace()
        r = client.post(
            "/workspaces/ws1/canvases",
            json={"import_path": str(a_dir)},
        )
        body = r.json()
        assert body["ok"] is False
        assert "Not a regular file" in body["error"]

    def test_dedups_when_path_already_registered(self, client, tmp_path):
        target = tmp_path / "draft.md"
        target.write_text("# hi", encoding="utf-8")
        _seed_workspace()
        # First import creates the canvas
        r1 = client.post("/workspaces/ws1/canvases", json={"import_path": str(target)})
        body1 = r1.json()
        assert body1["ok"] is True
        cv_id_1 = body1["canvas"]["id"]
        # Second import of same path returns the SAME canvas (reused)
        r2 = client.post("/workspaces/ws1/canvases", json={"import_path": str(target)})
        body2 = r2.json()
        assert body2["ok"] is True
        assert body2["canvas"]["id"] == cv_id_1
        assert body2.get("reused") is True

    def test_infers_type_from_extension_when_unspecified(self, client, tmp_path):
        for name, expected in [("foo.md", "markdown"), ("foo.tex", "latex"), ("foo.txt", "plain")]:
            target = tmp_path / name
            target.write_text("x", encoding="utf-8")
            ws_id = f"ws-{name}"
            _seed_workspace(ws_id=ws_id)
            r = client.post(
                f"/workspaces/{ws_id}/canvases",
                json={"import_path": str(target)},
            )
            body = r.json()
            assert body["ok"] is True
            assert body["canvas"]["type"] == expected, f"{name} → expected {expected}"
