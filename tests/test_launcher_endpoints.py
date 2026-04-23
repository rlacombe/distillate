# Covers: distillate/server.py — scaffold endpoint and REST API endpoints

import importlib.util
import json

import pytest


# ---------------------------------------------------------------------------
# Scaffold endpoint (server.py POST /experiments/scaffold)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)
class TestScaffoldEndpoint:
    """Test the scaffold_from_template endpoint logic via the server app."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        """Create a test client with isolated state and template dirs."""
        monkeypatch.setattr("distillate.launcher.CONFIG_DIR", tmp_path)
        monkeypatch.setenv("DISTILLATE_STATE_FILE", str(tmp_path / "state.json"))
        monkeypatch.setenv("EXPERIMENTS_ROOT", str(tmp_path / "experiments"))
        monkeypatch.setattr("distillate.config.EXPERIMENTS_ROOT", str(tmp_path / "experiments"))

        from distillate.server import _create_app
        from starlette.testclient import TestClient

        app = _create_app()
        return TestClient(app)

    def _make_template(self, tmp_path, name="tiny-matmul"):
        tmpl = tmp_path / "templates" / name
        tmpl.mkdir(parents=True, exist_ok=True)
        (tmpl / "PROMPT.md").write_text("# Test prompt\n")
        (tmpl / "evaluate.py").write_text("print('ok')\n")
        return tmpl

    def test_scaffold_endpoint(self, tmp_path, client):
        """POST with valid template registers project and returns ok."""
        resp = client.post("/experiments/scaffold", json={"template": "demo", "name": "Addition Grokking"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["experiment_id"] == "addition-grokking"
        assert "path" in data

    def test_scaffold_already_exists(self, tmp_path, client):
        """POST when project already registered returns already_exists: true."""
        # First call scaffolds
        resp1 = client.post("/experiments/scaffold", json={"template": "demo", "name": "Addition Grokking"})
        assert resp1.json()["ok"] is True
        # Second call returns existing
        resp2 = client.post("/experiments/scaffold", json={"template": "demo", "name": "Addition Grokking"})
        data = resp2.json()
        assert data["ok"] is True
        assert data["already_exists"] is True

    def test_scaffold_missing_template(self, tmp_path, client):
        """POST with bogus template name returns 404."""
        (tmp_path / "templates").mkdir(parents=True, exist_ok=True)
        resp = client.post("/experiments/scaffold", json={"template": "nonexistent", "name": "Nope"})
        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    def test_scaffold_no_template_param(self, client):
        """POST with empty body returns 400."""
        resp = client.post("/experiments/scaffold", json={})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_scaffold_builtin_template_on_fresh_config(self, client):
        """POST succeeds on a fresh config dir because demo is a built-in template."""
        resp = client.post("/experiments/scaffold", json={"template": "demo", "name": "Addition Grokking"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["experiment_id"] == "addition-grokking"


# ---------------------------------------------------------------------------
# Server endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not importlib.util.find_spec("fastapi"),
    reason="fastapi not installed (desktop-only dependency)",
)
class TestServerEndpoints:
    """Tests for the desktop-app REST endpoints in server.py."""

    @pytest.fixture(autouse=True)
    def _setup_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr("distillate.state.STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr("distillate.state.LOCK_PATH", tmp_path / "state.lock")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("ZOTERO_LIBRARY_ID", "12345")
        monkeypatch.setenv("ZOTERO_API_KEY", "fake")
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
        self.tmp_path = tmp_path

    def _make_client(self):
        from starlette.testclient import TestClient
        from distillate.server import _create_app
        app = _create_app()
        return TestClient(app)

    def test_papers_empty(self):
        client = self._make_client()
        resp = client.get("/papers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["papers"] == []
        assert data["total"] == 0

    def test_papers_returns_documents(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "ABC123": {
                "title": "Test Paper",
                "status": "processed",
                "authors": ["Alice", "Bob", "Charlie", "Dave"],
                "summary": "A great paper about testing.",
                "engagement": 75,
                "metadata": {
                    "citekey": "alice2025test",
                    "tags": ["ml", "testing"],
                    "citation_count": 42,
                    "publication_date": "2025-01-15",
                },
                "uploaded_at": "2025-01-10T00:00:00Z",
                "processed_at": "2025-01-12T00:00:00Z",
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/papers")
        data = resp.json()
        assert data["total"] == 1
        paper = data["papers"][0]
        assert paper["key"] == "ABC123"
        assert paper["title"] == "Test Paper"
        assert paper["citekey"] == "alice2025test"
        assert paper["authors"] == ["Alice", "Bob", "Charlie"]  # truncated to 3
        assert paper["engagement"] == 75
        assert paper["citation_count"] == 42

    def test_papers_status_filter(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "A1": {"title": "Read", "status": "processed", "metadata": {}},
            "A2": {"title": "Queued", "status": "on_remarkable", "metadata": {}},
        }
        state.save()

        client = self._make_client()
        resp = client.get("/papers?status=processed")
        data = resp.json()
        assert data["total"] == 1
        assert data["papers"][0]["key"] == "A1"

    def test_experiments_list_empty(self):
        client = self._make_client()
        resp = client.get("/experiments/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["experiments"] == []

    def test_experiments_list_with_projects(self):
        from distillate.state import State
        state = State()
        state.add_experiment("tiny-gene", "Tiny Gene Code", str(self.tmp_path))
        state._data["experiments"]["tiny-gene"]["runs"] = {
            "run-1": {
                "id": "run-1",
                "name": "baseline",
                "status": "completed",
                "decision": "best",
                "results": {"accuracy": 0.95},
                "started_at": "2025-01-01T00:00:00Z",
                "duration_minutes": 10,
                "tags": ["baseline"],
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/list")
        data = resp.json()
        assert len(data["experiments"]) == 1
        proj = data["experiments"][0]
        assert proj["id"] == "tiny-gene"
        assert proj["name"] == "Tiny Gene Code"
        assert proj["run_count"] == 1
        assert len(proj["runs"]) == 1
        assert proj["runs"][0]["name"] == "baseline"
        assert "github_url" not in proj  # no github_url when not set

    def test_experiments_list_includes_github_url(self):
        from distillate.state import State
        state = State()
        state.add_experiment("xp-gh", "With GitHub", str(self.tmp_path))
        state.update_experiment("xp-gh", github_url="https://github.com/user/distillate-xp-test")
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/list")
        data = resp.json()
        proj = data["experiments"][0]
        assert proj["github_url"] == "https://github.com/user/distillate-xp-test"

    def test_prompt_not_found(self):
        client = self._make_client()
        resp = client.get("/experiments/nonexistent/prompt")
        assert resp.status_code == 404

    def test_prompt_no_file(self):
        from distillate.state import State
        state = State()
        state.add_experiment("xp-noprompt", "No Prompt", str(self.tmp_path))
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/xp-noprompt/prompt")
        data = resp.json()
        assert data["ok"] is False
        assert data["reason"] == "no_prompt"

    def test_prompt_get(self):
        from distillate.state import State
        state = State()
        proj_dir = self.tmp_path / "xp-prompt"
        proj_dir.mkdir()
        (proj_dir / "PROMPT.md").write_text("# My Experiment\n\nOptimize accuracy.", encoding="utf-8")
        state.add_experiment("xp-prompt", "With Prompt", str(proj_dir))
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/xp-prompt/prompt")
        data = resp.json()
        assert data["ok"] is True
        assert "# My Experiment" in data["content"]
        assert "Optimize accuracy." in data["content"]

    def test_prompt_put(self):
        from distillate.state import State
        state = State()
        proj_dir = self.tmp_path / "xp-prompt-put"
        proj_dir.mkdir()
        state.add_experiment("xp-prompt-put", "Put Prompt", str(proj_dir))
        state.save()

        client = self._make_client()
        resp = client.put(
            "/experiments/xp-prompt-put/prompt",
            json={"content": "# Updated Prompt\n\nNew instructions."},
        )
        data = resp.json()
        assert data["ok"] is True

        # Verify the file was written
        content = (proj_dir / "PROMPT.md").read_text(encoding="utf-8")
        assert "# Updated Prompt" in content

        # Verify GET returns the updated content
        resp2 = client.get("/experiments/xp-prompt-put/prompt")
        assert resp2.json()["content"] == "# Updated Prompt\n\nNew instructions."

    def test_notebook_not_found(self):
        client = self._make_client()
        resp = client.get("/experiments/nonexistent/notebook")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "not_found"

    def test_notebook_returns_html(self):
        from distillate.state import State
        state = State()
        state.add_experiment("proj1", "My Project", str(self.tmp_path))
        state._data["experiments"]["proj1"]["runs"] = {
            "r1": {
                "id": "r1", "name": "run1", "status": "completed",
                "started_at": "2025-01-01T00:00:00Z",
                "results": {"accuracy": 0.9},
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/experiments/proj1/notebook")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<html" in resp.text

    def test_paper_detail_not_found(self):
        client = self._make_client()
        resp = client.get("/papers/NONEXIST")
        assert resp.status_code == 404
        assert resp.json()["reason"] == "not_found"

    def test_paper_detail_returns_full_data(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "XYZ789": {
                "title": "Attention Is All You Need",
                "status": "processed",
                "authors": ["Vaswani", "Shazeer", "Parmar", "Uszkoreit"],
                "summary": "This paper introduces the Transformer architecture.",
                "engagement": 95,
                "metadata": {
                    "citekey": "vaswani2017attention",
                    "tags": ["transformers", "attention", "nlp"],
                    "citation_count": 100000,
                    "publication_date": "2017-06-12",
                    "venue": "NeurIPS",
                    "doi": "10.5555/3295222.3295349",
                    "arxiv_id": "1706.03762",
                },
                "uploaded_at": "2025-01-01T00:00:00Z",
                "processed_at": "2025-01-05T00:00:00Z",
                "promoted_at": "2025-01-03T00:00:00Z",
            },
        }
        state.save()

        client = self._make_client()
        resp = client.get("/papers/XYZ789")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        paper = data["paper"]
        assert paper["key"] == "XYZ789"
        assert paper["title"] == "Attention Is All You Need"
        # Full authors list (not truncated)
        assert len(paper["authors"]) == 4
        # Full summary (not truncated)
        assert paper["summary"] == "This paper introduces the Transformer architecture."
        assert paper["venue"] == "NeurIPS"
        assert paper["doi"] == "10.5555/3295222.3295349"
        assert paper["arxiv_id"] == "1706.03762"
        assert paper["promoted_at"] == "2025-01-03T00:00:00Z"

    def test_papers_list_includes_promoted_flag(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "K1": {"title": "A", "status": "processed", "metadata": {}},
            "K2": {"title": "B", "status": "processed", "metadata": {}},
        }
        state._data["promoted_papers"] = ["K1"]
        state.save()

        client = self._make_client()
        resp = client.get("/papers")
        papers = {p["key"]: p for p in resp.json()["papers"]}
        assert papers["K1"]["promoted"] is True
        assert papers["K2"]["promoted"] is False

    def test_promote_and_unpromote(self):
        from distillate.state import State
        state = State()
        state._data["documents"] = {
            "P1": {"title": "Paper One", "status": "on_remarkable", "metadata": {}},
        }
        state.save()

        client = self._make_client()

        # Promote
        resp = client.post("/papers/P1/promote")
        assert resp.status_code == 200
        assert resp.json()["promoted"] is True

        # Verify persisted
        state.reload()
        assert "P1" in state.promoted_papers

        # Unpromote
        resp = client.post("/papers/P1/unpromote")
        assert resp.status_code == 200
        assert resp.json()["promoted"] is False

        state.reload()
        assert "P1" not in state.promoted_papers

    def test_promote_not_found(self):
        client = self._make_client()
        assert client.post("/papers/NOPE/promote").status_code == 404
        assert client.post("/papers/NOPE/unpromote").status_code == 404

    def test_refresh_metadata_not_found(self):
        client = self._make_client()
        resp = client.post("/papers/NOPE/refresh-metadata")
        assert resp.status_code == 404
