"""Experimentalist context-management tools: isolated REPL, literature search, scratchpad.

Three tools that keep an experimentalist agent's context window clean across long runs
by routing computation (REPL), literature retrieval (search), and working notes
(note) through isolated channels — only compact summaries re-enter the main context.
"""

import json
import logging
import os
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMAS = [
    {
        "name": "distillate_repl",
        "description": (
            "Run Python code in an isolated subprocess — only compact output enters "
            "your context. Pre-injected: pandas as pd, numpy as np, json, pathlib.Path, "
            "DISTILLATE_DIR=Path('.distillate'), RUNS_FILE=DISTILLATE_DIR/'runs.jsonl'. "
            "Use to explore datasets, query past runs, compute statistics. "
            "Stateless: no state persists between calls. "
            "If results matter, write them to .distillate/scratchpad.md via distillate_note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Hard timeout in seconds (default 30)",
                },
                "max_output_lines": {
                    "type": "integer",
                    "description": "Maximum stdout lines to return (default 50)",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "distillate_search",
        "description": (
            "Search ML literature via a specialist model. The full search context — "
            "retrieved papers, intermediate reasoning — never touches your window. "
            "Only the synthesized answer and citations come back. "
            "Use to check if a technique exists, find baselines, or understand a "
            "surprising result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Research question or topic to search for",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum citations to return (default 5)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "distillate_note",
        "description": (
            "Append a timestamped note to .distillate/scratchpad.md. "
            "Persists across runs — read it at the start of each run to orient yourself. "
            "Sections: hypothesis | findings | questions | blockers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Note content to append",
                },
                "section": {
                    "type": "string",
                    "description": "Section: hypothesis | findings | questions | blockers (default: findings)",
                },
            },
            "required": ["content"],
        },
    },
]

# ---------------------------------------------------------------------------
# Pre-injected preamble for the REPL subprocess
# ---------------------------------------------------------------------------

_REPL_PRELUDE = textwrap.dedent("""\
    import json
    import math
    import os
    import sys
    from pathlib import Path

    try:
        import numpy as np
    except ImportError:
        np = None
    try:
        import pandas as pd
    except ImportError:
        pd = None

    DISTILLATE_DIR = Path(".distillate")
    RUNS_FILE = DISTILLATE_DIR / "runs.jsonl"

""")

_VALID_SECTIONS = frozenset({"hypothesis", "findings", "questions", "blockers"})


# ---------------------------------------------------------------------------
# distillate_repl
# ---------------------------------------------------------------------------

def distillate_repl_tool(*, state, code: str, timeout: int = 30,
                          max_output_lines: int = 50) -> dict:
    """Run Python code in an isolated subprocess with hard output truncation."""
    cwd = os.getcwd()
    full_code = _REPL_PRELUDE + code

    try:
        proc = subprocess.run(
            ["python3", "-c", full_code],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": f"Timed out after {timeout}s.",
            "exit_code": -1,
            "truncated": False,
        }

    stdout_lines = proc.stdout.splitlines()
    total_lines = len(stdout_lines)
    truncated = total_lines > max_output_lines
    if truncated:
        stdout_lines = stdout_lines[:max_output_lines]
        stdout_lines.append(
            f"[... truncated, {total_lines} total lines. Narrow your query.]"
        )

    stderr_lines = proc.stderr.splitlines()
    if len(stderr_lines) > 20:
        stderr_lines = stderr_lines[:20] + ["[... stderr truncated]"]

    return {
        "stdout": "\n".join(stdout_lines),
        "stderr": "\n".join(stderr_lines),
        "exit_code": proc.returncode,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# distillate_search — Haiku sub-LLM with arXiv + KB tools
# ---------------------------------------------------------------------------

_SEARCH_SYSTEM = (
    "You are a research librarian. Answer the user's research question concisely, "
    "grounding your answer in papers you retrieve via the available search tools. "
    "After searching, respond with ONLY valid JSON in this exact structure:\n"
    '{"answer": "<2-3 sentence synthesis>", "citations": ['
    '{"title": "...", "authors": "...", "year": <int>, "relevance": "<one-line note>"}'
    "]}"
)

_SEARCH_TOOLS = [
    {
        "name": "search_arxiv",
        "description": (
            "Search arXiv for papers matching a query. Returns titles, authors, "
            "year, and abstract snippets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "description": "Max papers (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_kb",
        "description": (
            "Search the user's local paper library (read papers, highlights, summaries). "
            "Use this to ground answers in the user's existing reading."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
]


def distillate_search_tool(*, state, query: str, max_results: int = 5) -> dict:
    """Literature search via a Haiku sub-LLM. Returns answer + citations."""
    from distillate import config

    if not config.ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not configured"}

    try:
        import anthropic
    except ImportError:
        return {"error": "anthropic package not available"}

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": query}]

    for _ in range(5):
        resp = client.messages.create(
            model=config.CLAUDE_FAST_MODEL,
            max_tokens=2000,
            system=_SEARCH_SYSTEM,
            tools=_SEARCH_TOOLS,
            messages=messages,
        )

        if resp.stop_reason == "end_turn":
            text = "".join(
                b.text for b in resp.content if hasattr(b, "text")
            ).strip()
            try:
                result = json.loads(text)
                result["citations"] = result.get("citations", [])[:max_results]
                return result
            except json.JSONDecodeError:
                return {"answer": text, "citations": []}

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if not hasattr(block, "name"):
                continue
            if block.name == "search_arxiv":
                data = _search_arxiv(
                    block.input.get("query", query),
                    block.input.get("max_results", max_results),
                )
            elif block.name == "search_kb":
                data = _search_kb(state, block.input.get("query", query))
            else:
                data = {"error": f"unknown tool: {block.name}"}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(data, default=str),
            })

        messages = messages + [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": tool_results},
        ]

    return {"answer": "Search did not produce a result.", "citations": []}


def _search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    """Query the arXiv API and return structured paper records."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET

    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": min(max(max_results, 1), 10),
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"https://export.arxiv.org/api/query?{params}"

    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            xml_data = resp.read()
    except Exception as exc:
        log.warning("arXiv search failed: %s", exc)
        return [{"error": str(exc)}]

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_data)
    papers = []
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
        summary = (entry.findtext("atom:summary", "", ns) or "").strip()[:300]
        year_raw = (entry.findtext("atom:published", "", ns) or "")[:4]
        year = int(year_raw) if year_raw.isdigit() else None
        authors = [
            a.findtext("atom:name", "", ns)
            for a in entry.findall("atom:author", ns)
        ]
        author_str = ", ".join(a for a in authors[:3] if a)
        if len(authors) > 3:
            author_str += " et al."
        arxiv_id = (entry.findtext("atom:id", "", ns) or "").split("/abs/")[-1]
        papers.append({
            "title": title,
            "authors": author_str,
            "year": year,
            "summary": summary,
            "arxiv_id": arxiv_id,
        })
    return papers


def _search_kb(state, query: str) -> list[dict]:
    """Search the local paper library via the existing tools layer."""
    try:
        from distillate.tools import search_papers
        result = search_papers(state=state, query=query)
        papers = result.get("papers", [])
        return [
            {
                "title": p.get("title", ""),
                "authors": ", ".join(p.get("authors", [])),
                "year": p.get("year", ""),
                "summary": p.get("abstract", p.get("summary", ""))[:300],
                "source": "local_library",
            }
            for p in papers[:5]
        ]
    except Exception as exc:
        log.debug("KB search failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# distillate_note
# ---------------------------------------------------------------------------

def distillate_note_tool(*, state, content: str, section: str = "findings") -> dict:
    """Append a timestamped note to .distillate/scratchpad.md."""
    if section not in _VALID_SECTIONS:
        section = "findings"

    scratchpad = Path(os.getcwd()) / ".distillate" / "scratchpad.md"
    scratchpad.parent.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not scratchpad.exists():
        scratchpad.write_text(
            "# Experiment Scratchpad\n\nPersists across runs. "
            "Read at the start of each run to orient yourself.\n",
            encoding="utf-8",
        )

    entry = f"\n### [{section}] {ts}\n\n{content}\n"
    with scratchpad.open("a", encoding="utf-8") as fh:
        fh.write(entry)

    return {"success": True, "section": section, "timestamp": ts, "path": str(scratchpad)}
