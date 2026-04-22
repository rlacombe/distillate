# Covers: distillate/state.py, distillate/routes/canvas.py
"""Tests for the canvas state helpers and routes (plural API)."""
from pathlib import Path

import pytest


# ---- State helpers ------------------------------------------------------

def test_add_and_get_workspace_canvas(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))

    canvas_dir = isolate_state / "canvases" / "main"
    canvas_dir.mkdir(parents=True)
    cv = state.add_workspace_canvas(
        "ws1", title="Main paper", canvas_type="latex",
        directory=str(canvas_dir), entry="main.tex",
    )
    assert cv is not None
    assert cv["id"] == "cv_001"
    assert cv["title"] == "Main paper"
    assert cv["type"] == "latex"
    assert cv["dir"] == str(canvas_dir.resolve())
    assert cv["entry"] == "main.tex"

    state.save()
    state2 = State()
    looked_up = state2.get_workspace_canvas("ws1", "cv_001")
    assert looked_up is not None
    assert looked_up["title"] == "Main paper"
    assert looked_up["type"] == "latex"


def test_unique_ids_for_multiple_canvases(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))

    a = state.add_workspace_canvas("ws1", "A", "latex",
                                    directory=str(isolate_state / "a"),
                                    entry="main.tex")
    b = state.add_workspace_canvas("ws1", "B", "markdown",
                                    directory=str(isolate_state / "b"),
                                    entry="b.md")
    c = state.add_workspace_canvas("ws1", "C", "plain",
                                    directory=str(isolate_state / "c"),
                                    entry="c.txt")
    assert {a["id"], b["id"], c["id"]} == {"cv_001", "cv_002", "cv_003"}

    listed = state.list_workspace_canvases("ws1")
    assert [cv["id"] for cv in listed] == ["cv_001", "cv_002", "cv_003"]
    assert [cv["type"] for cv in listed] == ["latex", "markdown", "plain"]


def test_remove_workspace_canvas_leaves_files(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))
    d = isolate_state / "canvases" / "main"
    d.mkdir(parents=True)
    (d / "main.tex").write_text("content")
    cv = state.add_workspace_canvas("ws1", "Main", "latex",
                                    directory=str(d), entry="main.tex")

    assert state.remove_workspace_canvas("ws1", cv["id"]) is True
    assert state.get_workspace_canvas("ws1", cv["id"]) is None
    # Files on disk are untouched.
    assert (d / "main.tex").exists()


def test_update_workspace_canvas_rename(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))
    cv = state.add_workspace_canvas("ws1", "Old", "latex",
                                    directory=str(isolate_state / "w"),
                                    entry="main.tex")

    updated = state.update_workspace_canvas("ws1", cv["id"], title="New Name")
    assert updated is not None
    assert updated["title"] == "New Name"
    assert updated["id"] == cv["id"]

    # id and created_at cannot be overwritten.
    updated = state.update_workspace_canvas(
        "ws1", cv["id"], id="hacked", created_at="1970-01-01"
    )
    assert updated["id"] == cv["id"]
    assert updated["created_at"] != "1970-01-01"


def test_set_workspace_canvas_session(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))
    cv = state.add_workspace_canvas("ws1", "Main", "latex",
                                    directory=str(isolate_state / "w"),
                                    entry="main.tex")

    assert state.set_workspace_canvas_session("ws1", cv["id"], "coding_001") is True
    again = state.get_workspace_canvas("ws1", cv["id"])
    assert again["session_id"] == "coding_001"


def test_find_workspace_canvas_by_path(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))
    d = isolate_state / "paper"
    d.mkdir()
    (d / "main.tex").write_text("")
    state.add_workspace_canvas("ws1", "Main", "latex", directory=str(d), entry="main.tex")

    found = state.find_workspace_canvas_by_path("ws1", str(d / "main.tex"))
    assert found is not None
    assert found["title"] == "Main"

    missing = state.find_workspace_canvas_by_path("ws1", "/nonexistent/path.tex")
    assert missing is None


def test_infer_canvas_type():
    from distillate.state import State

    assert State._infer_canvas_type("paper.tex") == "latex"
    assert State._infer_canvas_type("README.md") == "markdown"
    assert State._infer_canvas_type("notes.markdown") == "markdown"
    assert State._infer_canvas_type("guide.mdx") == "markdown"
    assert State._infer_canvas_type("script.py") == "plain"
    assert State._infer_canvas_type("config.yaml") == "plain"


# ---- Legacy migration ---------------------------------------------------

def test_legacy_plural_writeups_migrate_to_canvases(isolate_state):
    """A workspace that still carries the old plural ``writeups`` dict
    should be lazily lifted into the new ``canvases`` dict on first list."""
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Legacy", root_path=str(isolate_state))
    state.workspaces["ws1"]["writeups"] = {
        "wu_001": {
            "id": "wu_001",
            "title": "Main paper",
            "format": "latex",
            "dir": str(isolate_state / "canvases" / "main"),
            "entry": "main.tex",
            "session_id": "",
            "last_compile": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    }

    canvases = state.list_workspace_canvases("ws1")
    assert len(canvases) == 1
    migrated = canvases[0]
    assert migrated["id"] == "cv_001"  # wu_ prefix rewritten
    assert migrated["type"] == "latex"  # inferred from entry
    assert migrated["title"] == "Main paper"


def test_legacy_singular_writeup_migrates(isolate_state):
    """A workspace with only the singular ``writeup`` field should also
    promote to a canvas on first list."""
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Legacy", root_path=str(isolate_state))
    legacy_file = isolate_state / "WRITEUP.tex"
    legacy_file.write_text("\\documentclass{article}")
    state.set_workspace_writeup("ws1", "latex", str(legacy_file))

    canvases = state.list_workspace_canvases("ws1")
    assert len(canvases) == 1
    assert canvases[0]["type"] == "latex"


def test_add_coding_session_with_canvas_id(isolate_state):
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))
    session = state.add_coding_session(
        "ws1", "coding_001", repo_path="/tmp/fake",
        tmux_name="test-001", canvas_id="cv_042",
    )
    assert session["canvas_id"] == "cv_042"
    legacy = state.add_coding_session(
        "ws1", "coding_002", repo_path="/tmp/other",
    )
    assert legacy["canvas_id"] == ""


# ---- Routes module ------------------------------------------------------

def test_extract_cite_keys_handles_variants():
    from distillate.routes.canvas import _extract_cite_keys

    tex = r"""
    See \cite{smith2023} and \citep[p.~5]{jones2024}.
    Multiple in one: \citet{a2020, b2021}.
    Starred: \cite*{c2022}.
    """
    keys = _extract_cite_keys(tex)
    assert set(keys) == {"smith2023", "jones2024", "a2020", "b2021", "c2022"}


def test_doc_to_bibtex_builds_article_entry():
    from distillate.routes.canvas import _doc_to_bibtex

    doc = {
        "title": "On Attention",
        "authors": ["Smith, Jane", "Doe, John"],
        "metadata": {
            "citekey": "smith2023attention",
            "publication_date": "2023-07-01",
            "venue": "NeurIPS",
            "doi": "10.1234/neurips.2023",
            "arxiv_id": "2307.12345",
        },
    }
    bib = _doc_to_bibtex(doc)
    assert bib.startswith("@article{smith2023attention,")
    assert "title = {{On Attention}}" in bib
    assert "author = {Smith, Jane and Doe, John}" in bib
    assert "journal = {NeurIPS}" in bib
    assert "eprint = {2307.12345}" in bib


def test_infer_type_from_extension():
    from distillate.routes.canvas import _infer_type

    assert _infer_type("paper.tex") == "latex"
    assert _infer_type("README.md") == "markdown"
    assert _infer_type("notes.markdown") == "markdown"
    assert _infer_type("guide.mdx") == "markdown"
    assert _infer_type("hello.py") == "plain"
    assert _infer_type("no-extension") == "plain"


def test_slugify_and_unique_slug(tmp_path):
    from distillate.routes.canvas import _slugify, _unique_slug

    assert _slugify("Main Paper") == "main-paper"
    assert _slugify("NeurIPS 2026!") == "neurips-2026"
    assert _slugify("") == "canvas"

    assert _unique_slug(tmp_path, "main") == "main"
    (tmp_path / "main").mkdir()
    assert _unique_slug(tmp_path, "main") == "main-2"
    (tmp_path / "main-2").mkdir()
    assert _unique_slug(tmp_path, "main") == "main-3"


def test_infer_project_root_explicit(tmp_path):
    from distillate.routes.canvas import _infer_project_root

    ws = {"root_path": str(tmp_path), "repos": []}
    assert _infer_project_root(ws) == tmp_path.resolve()


def test_infer_project_root_single_repo_fallback(tmp_path):
    from distillate.routes.canvas import _infer_project_root

    repo = tmp_path / "bench"
    repo.mkdir()
    ws = {"root_path": "", "repos": [{"path": str(repo), "name": "bench"}]}
    assert _infer_project_root(ws) == repo.resolve()


def test_infer_project_root_multi_repo_common_parent(tmp_path):
    from distillate.routes.canvas import _infer_project_root

    a = tmp_path / "Distillate" / "distillate-dev"
    b = tmp_path / "Distillate" / "distillate-cloud"
    c = tmp_path / "Distillate" / "distillate-public"
    for p in (a, b, c):
        p.mkdir(parents=True)
    ws = {
        "root_path": "",
        "repos": [{"path": str(a)}, {"path": str(b)}, {"path": str(c)}],
    }
    assert _infer_project_root(ws) == (tmp_path / "Distillate").resolve()


def test_scan_for_canvases_finds_tex_and_md(tmp_path):
    from distillate.routes.canvas import _scan_for_canvases

    # A standalone LaTeX paper
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "main.tex").write_text(
        r"\documentclass{article}\title{Paper}\begin{document}Hi\end{document}"
    )
    # A README
    (tmp_path / "README.md").write_text("# My Project\n\nHello.")
    # A nested markdown doc
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n\nContent.")
    # A non-document .tex file (should be excluded — no \documentclass)
    (tmp_path / "paper" / "appendix.tex").write_text("Just text")
    # An ignored build/ folder
    (tmp_path / "paper" / "build").mkdir()
    (tmp_path / "paper" / "build" / "main.tex").write_text(
        r"\documentclass{article}\begin{document}x\end{document}"
    )

    results = _scan_for_canvases(tmp_path)
    rels = {r["rel"] for r in results}
    types = {r["rel"]: r["type"] for r in results}

    assert "paper/main.tex" in rels
    assert types["paper/main.tex"] == "latex"
    assert "README.md" in rels
    assert types["README.md"] == "markdown"
    assert "docs/guide.md" in rels
    assert "paper/appendix.tex" not in rels
    assert not any("build" in r for r in rels)


def test_scan_extracts_titles(tmp_path):
    from distillate.routes.canvas import _scan_for_canvases

    (tmp_path / "paper.tex").write_text(
        r"\documentclass{article}\title{Great Paper}\begin{document}x\end{document}"
    )
    (tmp_path / "notes.md").write_text("# Big Idea\n\nContent here.")

    results = _scan_for_canvases(tmp_path)
    titles = {r["entry"]: r["title"] for r in results}
    assert titles["paper.tex"] == "Great Paper"
    assert titles["notes.md"] == "Big Idea"


def test_import_existing_canvas_via_state_helper(isolate_state):
    """The state helper accepts an arbitrary directory — import = register
    an existing path without scaffolding."""
    from distillate.state import State

    state = State()
    state.add_workspace("ws1", name="Test", root_path=str(isolate_state))

    existing = isolate_state / "drafts" / "paper"
    existing.mkdir(parents=True)
    (existing / "paper.tex").write_text(
        r"\documentclass{article}\begin{document}Hi\end{document}"
    )

    cv = state.add_workspace_canvas(
        "ws1", title="Imported paper", canvas_type="latex",
        directory=str(existing), entry="paper.tex",
    )
    assert cv is not None
    assert cv["dir"] == str(existing.resolve())
    assert cv["entry"] == "paper.tex"
    assert cv["type"] == "latex"
    # Files on disk are the originals — not scaffolded.
    assert (existing / "paper.tex").read_text().startswith(r"\documentclass")
