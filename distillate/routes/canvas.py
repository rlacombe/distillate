"""Canvas endpoints — plural collection of editable documents per project.

A "canvas" is any file the user is working on with an agent: a LaTeX paper,
a Markdown draft, a Python script, a README, anything editable. Each canvas
carries a type inferred from its extension that drives the renderer's type
registry (source editor + optional preview + optional compile step).

Canvases live inside the project's ``root_path`` (or the common parent of
its linked repos). LaTeX canvases get scaffolded at
``<root>/canvases/<slug>/main.tex``; other types land alongside the file
the user picked or imported.

This module replaces the legacy ``writeup.py`` router. Existing workspaces
whose state still carries ``writeups`` are migrated on first access via
``state._ensure_canvases_migrated``.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from distillate.routes import _context
from distillate.state import acquire_lock, release_lock

log = logging.getLogger(__name__)

router = APIRouter()


# ---- Scaffolding templates ---------------------------------------------

_DEFAULT_MAIN_TEX = r"""\documentclass{article}
\usepackage[utf8]{inputenc}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{hyperref}

\title{}
\author{}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
\end{abstract}

\section{Introduction}

\section{Methods}

\section{Results}

\section{Discussion}

\bibliographystyle{plain}
\bibliography{references}

\end{document}
"""

_DEFAULT_GITIGNORE = """build/
references.bib
*.aux
*.log
*.out
*.synctex.gz
*.toc
*.bbl
*.blg
"""

_DEFAULT_MARKDOWN = "# {title}\n\n"


# ---- Helpers ------------------------------------------------------------

def _infer_type(entry: str) -> str:
    """Map a filename to a canvas type. Keep in sync with state._infer_canvas_type."""
    name = (entry or "").lower()
    if name.endswith(".tex"):
        return "latex"
    if name.endswith(".md") or name.endswith(".markdown") or name.endswith(".mdx"):
        return "markdown"
    return "plain"


def _slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (title or "").strip().lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "canvas"


def _unique_slug(parent: Path, base: str) -> str:
    """Return ``base``, ``base-2``, ``base-3``… first one whose folder does not exist."""
    candidate = base
    n = 2
    while (parent / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _infer_project_root(ws: dict) -> Path:
    """Pick the best "project folder" for canvases on a workspace.

    Preference order:
      1. Explicit ``ws.root_path``.
      2. Common parent directory of linked repos.
         - Single repo → the repo's own path.
         - Multiple sibling repos → the shared parent.
      3. Empty path (caller must error out).
    """
    root = (ws.get("root_path") or "").strip()
    if root:
        return Path(root).expanduser().resolve()
    repos = ws.get("repos") or []
    repo_paths = [
        Path(r["path"]).expanduser().resolve()
        for r in repos if r.get("path")
    ]
    if not repo_paths:
        return Path("")
    if len(repo_paths) == 1:
        return repo_paths[0]
    try:
        return Path(os.path.commonpath([str(p) for p in repo_paths]))
    except ValueError:
        return repo_paths[0]


# ---- List / create / patch / delete -------------------------------------

@router.get("/workspaces/{workspace_id}/canvases")
async def list_canvases(workspace_id: str):
    """Return all canvases on a workspace (migrates legacy writeups on first call)."""
    _state = _context._state
    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"ok": False, "error": "Project not found."})
    canvases = _state.list_workspace_canvases(workspace_id)
    return JSONResponse({"ok": True, "canvases": canvases})


@router.post("/workspaces/{workspace_id}/canvases")
async def create_canvas(workspace_id: str, request: Request):
    """Create a canvas — scaffold a new file or register an existing one.

    Body::

        {
          "title": "Main paper",
          "type": "latex" | "markdown" | "plain",  # optional; inferred otherwise
          "import_path": "/abs/path/to/file.tex",  # OPT: register existing file
        }

    LaTeX canvases need a parent project folder. We first try ``ws.root_path``,
    then fall back to the common parent of linked repos.
    """
    _state = _context._state
    body = await request.json() if await request.body() else {}
    title = (body.get("title") or "").strip() or "Untitled"

    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"ok": False, "error": "Project not found."})

    import_path = (body.get("import_path") or "").strip()

    # --- Import path: register an existing file without scaffolding. -----
    if import_path:
        target = Path(import_path).expanduser().resolve()
        if not target.exists():
            return JSONResponse({"ok": False, "error": f"File not found: {target}"})
        if not target.is_file():
            return JSONResponse({"ok": False, "error": f"Not a regular file: {target}"})

        # Dedup: if a canvas already exists for this exact path, return it.
        existing = _state.find_workspace_canvas_by_path(workspace_id, str(target))
        if existing:
            return JSONResponse({"ok": True, "canvas": existing, "imported": True, "reused": True})

        canvas_type = body.get("type") or _infer_type(target.name)
        title = title if title != "Untitled" else (
            target.stem.replace("-", " ").replace("_", " ").title()
        )

        acquire_lock()
        try:
            _state.reload()
            cv = _state.add_workspace_canvas(
                workspace_id, title=title, canvas_type=canvas_type,
                directory=str(target.parent), entry=target.name,
            )
            _state.save()
        finally:
            release_lock()
        if cv is None:
            return JSONResponse({"ok": False, "error": "Failed to record canvas."})
        return JSONResponse({"ok": True, "canvas": cv, "imported": True})

    # --- Scaffold path: create a fresh file. -----------------------------
    canvas_type = (body.get("type") or "latex").lower()
    _WRITE_TYPES = {"latex", "markdown", "plain"}
    _WORK_ITEM_TYPES = {"code", "survey", "data"}
    if canvas_type not in _WRITE_TYPES | _WORK_ITEM_TYPES:
        return JSONResponse({"ok": False, "error": f"Unknown type: {canvas_type}"})

    base_slug = _slugify(title)
    project_root = _infer_project_root(ws)
    if not str(project_root) or str(project_root) == ".":
        return JSONResponse({
            "ok": False,
            "error": "root_path_required",
            "message": "Canvases need a project folder. Link a repo to this "
                       "project or set its root folder first.",
        })

    if canvas_type == "latex":
        parent = project_root / "canvases"
        parent.mkdir(parents=True, exist_ok=True)
        slug = _unique_slug(parent, base_slug)
        canvas_dir = parent / slug
        canvas_dir.mkdir(parents=True, exist_ok=False)
        (canvas_dir / "figures").mkdir(exist_ok=True)
        entry = "main.tex"
        (canvas_dir / entry).write_text(_DEFAULT_MAIN_TEX, encoding="utf-8")
        gi = canvas_dir / ".gitignore"
        if not gi.exists():
            gi.write_text(_DEFAULT_GITIGNORE, encoding="utf-8")
    elif canvas_type == "markdown":
        parent = project_root / "canvases"
        parent.mkdir(parents=True, exist_ok=True)
        slug = _unique_slug(parent, base_slug)
        canvas_dir = parent / slug
        canvas_dir.mkdir(parents=True, exist_ok=False)
        entry = f"{slug}.md"
        (canvas_dir / entry).write_text(
            _DEFAULT_MARKDOWN.format(title=title), encoding="utf-8"
        )
    elif canvas_type == "plain":
        parent = project_root / "canvases"
        parent.mkdir(parents=True, exist_ok=True)
        slug = _unique_slug(parent, base_slug)
        canvas_dir = parent / slug
        canvas_dir.mkdir(parents=True, exist_ok=False)
        entry = f"{slug}.txt"
        (canvas_dir / entry).write_text("", encoding="utf-8")
    elif canvas_type == "code":
        parent = project_root / "sessions"
        parent.mkdir(parents=True, exist_ok=True)
        slug = _unique_slug(parent, base_slug)
        canvas_dir = parent / slug
        canvas_dir.mkdir(parents=True, exist_ok=False)
        entry = "main.py"
        (canvas_dir / entry).write_text("", encoding="utf-8")
    elif canvas_type == "survey":
        parent = project_root / "sessions"
        parent.mkdir(parents=True, exist_ok=True)
        slug = _unique_slug(parent, base_slug)
        canvas_dir = parent / slug
        canvas_dir.mkdir(parents=True, exist_ok=False)
        entry = f"{base_slug}.md"
        (canvas_dir / entry).write_text(f"# {title}\n\n", encoding="utf-8")
    else:  # data
        parent = project_root / "sessions"
        parent.mkdir(parents=True, exist_ok=True)
        slug = _unique_slug(parent, base_slug)
        canvas_dir = parent / slug
        canvas_dir.mkdir(parents=True, exist_ok=False)
        entry = "analysis.py"
        (canvas_dir / entry).write_text("", encoding="utf-8")

    acquire_lock()
    try:
        _state.reload()
        cv = _state.add_workspace_canvas(
            workspace_id, title=title, canvas_type=canvas_type,
            directory=str(canvas_dir), entry=entry,
        )
        _state.save()
    finally:
        release_lock()

    if cv is None:
        return JSONResponse({"ok": False, "error": "Failed to record canvas."})
    return JSONResponse({"ok": True, "canvas": cv})


@router.delete("/workspaces/{workspace_id}/canvases/{canvas_id}")
async def delete_canvas(workspace_id: str, canvas_id: str):
    """Remove a canvas from state. Files on disk are left in place."""
    _state = _context._state
    acquire_lock()
    try:
        _state.reload()
        ok = _state.remove_workspace_canvas(workspace_id, canvas_id)
        if ok:
            _state.save()
    finally:
        release_lock()
    if not ok:
        return JSONResponse({"ok": False, "error": "Canvas not found."})
    return JSONResponse({"ok": True})


@router.patch("/workspaces/{workspace_id}/canvases/{canvas_id}")
async def patch_canvas(workspace_id: str, canvas_id: str, request: Request):
    """Rename a canvas. Body: ``{"title": "New title"}``."""
    _state = _context._state
    body = await request.json() if await request.body() else {}
    allowed = {}
    if "title" in body:
        allowed["title"] = (body["title"] or "").strip() or "Untitled"
    if "status" in body:
        s = body["status"]
        if s in ("active", "review", "done", "archived"):
            allowed["status"] = s
            if s == "done":
                from datetime import datetime, timezone
                allowed["completed_at"] = datetime.now(timezone.utc).isoformat()
    if not allowed:
        return JSONResponse({"ok": False, "error": "No editable fields provided."})
    acquire_lock()
    try:
        _state.reload()
        cv = _state.update_workspace_canvas(workspace_id, canvas_id, **allowed)
        if cv:
            _state.save()
    finally:
        release_lock()
    if not cv:
        return JSONResponse({"ok": False, "error": "Canvas not found."})
    return JSONResponse({"ok": True, "canvas": cv})


@router.get("/workspaces/{workspace_id}/canvases/{canvas_id}/dir")
async def get_canvas_dir(workspace_id: str, canvas_id: str):
    """Return ``{dir, entry, type}`` for the Electron main process to sandbox I/O."""
    _state = _context._state
    cv = _state.get_workspace_canvas(workspace_id, canvas_id)
    if not cv:
        return JSONResponse({"ok": False, "error": "Canvas not found."})
    return JSONResponse({
        "ok": True,
        "dir": cv["dir"],
        "entry": cv.get("entry", ""),
        "type": cv.get("type", "plain"),
        "exists": Path(cv["dir"]).exists(),
    })


@router.post("/workspaces/{workspace_id}/canvases/{canvas_id}/compile-status")
async def post_compile_status(workspace_id: str, canvas_id: str, request: Request):
    """Record the last compile result on a canvas so cards can show freshness."""
    _state = _context._state
    body = await request.json() if await request.body() else {}
    last_compile = {
        "ok": bool(body.get("ok", False)),
        "at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": int(body.get("duration_ms", 0) or 0),
        "error_count": int(body.get("error_count", 0) or 0),
    }
    acquire_lock()
    try:
        _state.reload()
        cv = _state.update_workspace_canvas(
            workspace_id, canvas_id, last_compile=last_compile
        )
        if cv:
            _state.save()
    finally:
        release_lock()
    if not cv:
        return JSONResponse({"ok": False, "error": "Canvas not found."})
    return JSONResponse({"ok": True, "last_compile": last_compile})


# ---- Detection: find existing editable files in a project folder --------

_TITLE_RE = re.compile(r"\\title\s*\{([^}]*)\}")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_DOCUMENTCLASS_RE = re.compile(r"\\documentclass\b")
_IGNORED_DIRS = {
    "build", "_build", "dist", "node_modules", ".git", ".venv", "venv",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "canvases",
    "writeups",  # legacy
}
_DETECT_EXTS = {".tex", ".md", ".markdown", ".mdx"}


def _guess_title(path: Path, canvas_type: str) -> str:
    """Peek at the head of a file for a title-like token."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:16384]
    except OSError:
        return ""
    if canvas_type == "latex":
        m = _TITLE_RE.search(head)
        if m:
            cleaned = re.sub(r"\\[a-zA-Z]+\s*\{([^}]*)\}", r"\1", m.group(1))
            return cleaned.strip()
    elif canvas_type == "markdown":
        m = _H1_RE.search(head)
        if m:
            return m.group(1).strip()
    return ""


def _is_latex_document(path: Path) -> bool:
    """True iff the file contains a ``\\documentclass`` directive."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:8192]
    except OSError:
        return False
    return bool(_DOCUMENTCLASS_RE.search(head))


def _scan_for_canvases(root: Path, max_depth: int = 4, max_results: int = 40) -> list:
    """Walk ``root`` looking for editable documents (``.tex``, ``.md`` files)."""
    if not root.exists() or not root.is_dir():
        return []
    root = root.resolve()
    candidates = []
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for e in entries:
            name = e.name
            if name.startswith("."):
                continue
            if e.is_dir(follow_symlinks=False):
                if name in _IGNORED_DIRS:
                    continue
                stack.append((Path(e.path), depth + 1))
            elif e.is_file(follow_symlinks=False):
                ext = os.path.splitext(name)[1].lower()
                if ext not in _DETECT_EXTS:
                    continue
                p = Path(e.path)
                cv_type = _infer_type(name)
                # Skip .tex files that aren't standalone documents.
                if cv_type == "latex" and not _is_latex_document(p):
                    continue
                candidates.append((p, cv_type))

    def _score(item):
        p, _ = item
        name = p.name.lower()
        name_priority = {
            "main.tex": 0, "paper.tex": 1, "manuscript.tex": 2,
            "readme.md": 0, "index.md": 1,
        }.get(name, 5)
        try:
            rel_depth = len(p.relative_to(root).parts)
        except ValueError:
            rel_depth = 99
        return (name_priority, rel_depth, str(p))

    candidates.sort(key=_score)
    out = []
    seen_dirs = set()
    for p, cv_type in candidates[: max_results * 2]:
        parent = p.parent
        # Dedupe: one best-scored candidate per folder.
        key = (parent, cv_type)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        title = _guess_title(p, cv_type) or p.stem.replace("-", " ").replace("_", " ").title()
        try:
            size = p.stat().st_size
            mtime = p.stat().st_mtime
        except OSError:
            size, mtime = 0, 0
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        out.append({
            "path": str(p),
            "dir": str(parent),
            "entry": p.name,
            "rel": rel,
            "title": title,
            "type": cv_type,
            "size": size,
            "mtime": mtime,
        })
        if len(out) >= max_results:
            break
    return out


@router.get("/workspaces/{workspace_id}/canvases/detect")
async def detect_existing_canvases(workspace_id: str):
    """Scan the project folder for existing editable documents.

    Returns any ``.tex`` with ``\\documentclass`` or ``.md``/``.markdown``
    file found under the inferred project root, excluding already-registered
    canvas directories. Used by the New Canvas modal to suggest importing
    files an agent already drafted.
    """
    _state = _context._state
    ws = _state.get_workspace(workspace_id)
    if not ws:
        return JSONResponse({"ok": False, "error": "Project not found."})

    root = _infer_project_root(ws)
    if not str(root) or str(root) == ".":
        return JSONResponse({"ok": True, "root": "", "candidates": []})

    candidates = _scan_for_canvases(root)

    # Exclude files already registered as canvases on this workspace.
    registered_paths = set()
    for cv in _state.list_workspace_canvases(workspace_id):
        try:
            p = Path(cv.get("dir", "")) / cv.get("entry", "")
            registered_paths.add(str(p.resolve()))
        except OSError:
            continue
    candidates = [
        c for c in candidates
        if str(Path(c["path"]).resolve()) not in registered_paths
    ]
    return JSONResponse({
        "ok": True,
        "root": str(root),
        "candidates": candidates,
    })


# ---- Citation resolution (LaTeX only) -----------------------------------

_CITE_RE = re.compile(r"\\cite[tp]?\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _extract_cite_keys(tex: str) -> list:
    keys = []
    seen = set()
    for match in _CITE_RE.finditer(tex):
        for key in match.group(1).split(","):
            k = key.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
    return keys


def _doc_to_bibtex(doc: dict) -> str:
    meta = doc.get("metadata") or {}
    citekey = (meta.get("citekey") or "").strip()
    if not citekey:
        authors = doc.get("authors") or []
        first = authors[0].split(",")[0].split()[-1].lower() if authors else "ref"
        year = (meta.get("publication_date") or "")[:4] or "nd"
        citekey = f"{first}{year}"

    fields = {}
    title = doc.get("title") or ""
    if title:
        fields["title"] = f"{{{title}}}"
    authors = doc.get("authors") or []
    if authors:
        fields["author"] = " and ".join(authors)
    date = meta.get("publication_date") or ""
    if date:
        fields["year"] = date[:4]
    venue = meta.get("venue")
    if venue:
        fields["journal"] = venue
    doi = meta.get("doi")
    if doi:
        fields["doi"] = doi
    url = meta.get("url")
    if url:
        fields["url"] = url
    arxiv = meta.get("arxiv_id")
    if arxiv:
        fields["eprint"] = arxiv
        fields["archivePrefix"] = "arXiv"

    entry_type = "@article" if fields.get("journal") or fields.get("doi") else "@misc"
    lines = [f"{entry_type}{{{citekey},"]
    for k, v in fields.items():
        lines.append(f"  {k} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


@router.post("/workspaces/{workspace_id}/canvases/{canvas_id}/resolve-citations")
async def resolve_citations(workspace_id: str, canvas_id: str, request: Request):
    """Scan the canvas's entry file, resolve \\cite keys, write references.bib."""
    _state = _context._state
    cv = _state.get_workspace_canvas(workspace_id, canvas_id)
    if not cv:
        return JSONResponse({"ok": False, "error": "Canvas not found."})
    if cv.get("type") != "latex":
        return JSONResponse({"ok": True, "resolved": [], "missing": [], "skipped": True})

    body = await request.json() if await request.body() else {}
    keys = body.get("keys")

    base = Path(cv["dir"])
    entry = cv.get("entry", "main.tex")

    if not keys:
        keys = []
        main_tex = base / entry
        if main_tex.exists():
            try:
                keys = _extract_cite_keys(main_tex.read_text(encoding="utf-8"))
            except OSError:
                pass

    resolved = {}
    missing = []
    for key in keys:
        doc = _state.find_by_citekey(key)
        if doc is None and key.startswith("arxiv:"):
            arxiv_id = key.split(":", 1)[1]
            for d in _state.documents.values():
                if (d.get("metadata") or {}).get("arxiv_id") == arxiv_id:
                    doc = d
                    break
        if doc is None and "/" in key:
            doc = _state.find_by_doi(key)
        if doc is None:
            missing.append(key)
            continue
        resolved[key] = _doc_to_bibtex(doc)

    bib_path = base / "references.bib"
    banner = "% AUTO-GENERATED by Distillate from the Papers library.\n% Do not edit — changes will be overwritten on next compile.\n\n"
    body_text = "\n\n".join(resolved.values()) if resolved else ""
    try:
        base.mkdir(parents=True, exist_ok=True)
        bib_path.write_text(banner + body_text + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("Failed to write references.bib: %s", exc)

    return JSONResponse({
        "ok": True,
        "resolved": list(resolved.keys()),
        "missing": missing,
        "bib_path": str(bib_path),
    })
