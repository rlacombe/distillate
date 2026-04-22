"""Typed Lab API for the REPL sandbox.

Thin Python wrappers around existing tool functions, providing a clean
object-oriented interface for Nicolas's lab REPL.  Every read method
calls ``state.reload()`` first and returns **deep copies** so that
mutations inside the sandbox never corrupt live State.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

log = logging.getLogger(__name__)


def _deep(obj: Any) -> Any:
    """Return a deep copy to isolate sandbox mutations from State."""
    return copy.deepcopy(obj)


# ---------------------------------------------------------------------------
# Fluent collection wrappers
# ---------------------------------------------------------------------------

class RunCollection:
    """Iterable collection of run dicts with query helpers."""

    def __init__(self, runs: list[dict]):
        self._runs = runs

    def peak_metric(self, metric: str | None = None) -> "dict | float | None":
        """Best achieved value per metric across all runs.

        With no argument returns ``{metric_name: best_value}`` for every
        metric that appears in any run's ``results`` dict.  With a metric
        name returns the single best float for that metric, or ``None``.
        """
        bests: dict[str, float] = {}
        for run in self._runs:
            results = run.get("results") or {}
            for k, v in results.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if k not in bests or fv > bests[k]:
                    bests[k] = fv
        if metric is not None:
            return bests.get(metric)
        return bests if bests else None

    def __len__(self) -> int:
        return len(self._runs)

    def __iter__(self):
        return iter(self._runs)

    def __repr__(self) -> str:
        return f"RunCollection({len(self._runs)} runs)"


class ExperimentCollection:
    """Iterable collection of experiment dicts with query helpers."""

    def __init__(self, experiments: list[dict]):
        self._experiments = experiments

    @property
    def runs(self) -> RunCollection:
        """Aggregate all runs across every experiment in the collection."""
        all_runs: list[dict] = []
        for exp in self._experiments:
            all_runs.extend(exp.get("runs") or [])
        return RunCollection(all_runs)

    def __len__(self) -> int:
        return len(self._experiments)

    def __iter__(self):
        return iter(self._experiments)

    def __repr__(self) -> str:
        return f"ExperimentCollection({len(self._experiments)} experiments)"


# ---------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------

class PapersAPI:
    """Query the paper library."""

    def __init__(self, state):
        self._state = state

    def _reload(self):
        self._state.reload()

    def search(self, query: str, status: str | None = None) -> list[dict]:
        """Search papers by title, citekey, index, or tag."""
        self._reload()
        from distillate.tools import search_papers
        result = search_papers(state=self._state, query=query, status=status)
        return _deep(result.get("results", []))

    def get(self, identifier: str) -> dict:
        """Get full paper details by citekey, index, or title."""
        self._reload()
        from distillate.tools import get_paper_details
        result = get_paper_details(state=self._state, identifier=identifier)
        return _deep(result)

    def recent(self, count: int = 10) -> list[dict]:
        """Most recently read papers."""
        self._reload()
        from distillate.tools import get_recent_reads
        result = get_recent_reads(state=self._state, count=count)
        return _deep(result.get("papers", []))

    def queue(self) -> list[dict]:
        """Papers in the reading queue."""
        self._reload()
        from distillate.tools import get_queue
        result = get_queue(state=self._state)
        return _deep(result.get("queue", []))

    def stats(self, period_days: int = 30) -> dict:
        """Reading statistics for the given period."""
        self._reload()
        from distillate.tools import get_reading_stats
        result = get_reading_stats(state=self._state, period_days=period_days)
        return _deep(result)

    def highlights(self, identifier: str) -> str:
        """Get highlights text for a paper."""
        self._reload()
        from distillate.tools import get_paper_details
        result = get_paper_details(state=self._state, identifier=identifier)
        return str(result.get("highlights", ""))

    def by_tag(self, tag: str) -> list[dict]:
        """Search papers by tag."""
        return self.search(query=tag)


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

class ExperimentsAPI:
    """Query experiments (projects + runs)."""

    def __init__(self, state):
        self._state = state

    def _reload(self):
        self._state.reload()

    def list(self) -> ExperimentCollection:
        """List all experiments."""
        self._reload()
        from distillate.experiment_tools import list_experiments
        result = list_experiments(state=self._state)
        return ExperimentCollection(_deep(result.get("experiments", [])))

    def recent(self, n: int = 5) -> ExperimentCollection:
        """N most recently active experiments, sorted by latest run activity."""
        self._reload()
        from distillate.experiment_tools import list_experiments
        result = list_experiments(state=self._state)
        experiments = _deep(result.get("experiments", []))

        def _last_activity(exp: dict) -> str:
            runs = exp.get("runs") or []
            timestamps = [r.get("completed_at") or r.get("started_at") or "" for r in runs]
            return max(timestamps, default=exp.get("created_at") or "")

        experiments.sort(key=_last_activity, reverse=True)
        return ExperimentCollection(experiments[:n])

    def get(self, identifier: str) -> dict:
        """Get project details by name or ID."""
        self._reload()
        from distillate.experiment_tools import get_experiment_details
        result = get_experiment_details(state=self._state, identifier=identifier)
        return _deep(result)

    def runs(self, project: str) -> list[dict]:
        """Get all runs for a project."""
        detail = self.get(project)
        return _deep(detail.get("runs", []))

    def run_details(self, project: str, run: str) -> dict:
        """Get full details for a specific run."""
        self._reload()
        from distillate.experiment_tools import get_run_details_tool
        result = get_run_details_tool(state=self._state, project=project, run=run)
        return _deep(result)

    def active(self) -> list[dict]:
        """List projects with active sessions."""
        self._reload()
        from distillate.experiment_tools import experiment_status_tool
        result = experiment_status_tool(state=self._state)
        return _deep(result.get("experiments", []))

    def status(self, project: str = "") -> dict:
        """Get experiment status, optionally filtered to one project."""
        self._reload()
        from distillate.experiment_tools import experiment_status_tool
        result = experiment_status_tool(state=self._state, project=project)
        return _deep(result)


# ---------------------------------------------------------------------------
# Notebook
# ---------------------------------------------------------------------------

class NotebookAPI:
    """Query the lab notebook."""

    def __init__(self, state):
        self._state = state

    def _reload(self):
        self._state.reload()

    def recent(self, n: int = 20) -> list[str]:
        """Last *n* notebook entries."""
        self._reload()
        from distillate.experiment_tools import read_lab_notebook_tool
        result = read_lab_notebook_tool(state=self._state, n=n)
        return _deep(result.get("entries", []))

    def by_date(self, date: str) -> list[str]:
        """Notebook entries for a specific date (YYYY-MM-DD)."""
        self._reload()
        from distillate.experiment_tools import read_lab_notebook_tool
        result = read_lab_notebook_tool(state=self._state, date=date)
        return _deep(result.get("entries", []))

    def by_project(self, project: str) -> list[str]:
        """Notebook entries for a specific project."""
        self._reload()
        from distillate.experiment_tools import read_lab_notebook_tool
        result = read_lab_notebook_tool(state=self._state, project=project)
        return _deep(result.get("entries", []))

    def digest(self, days: int = 7) -> str:
        """Weekly digest summary."""
        self._reload()
        from distillate.experiment_tools import notebook_digest_tool
        result = notebook_digest_tool(state=self._state, days=days)
        return str(result.get("digest", ""))


# ---------------------------------------------------------------------------
# Projects (lighter than ExperimentsAPI — just metadata)
# ---------------------------------------------------------------------------

class ProjectsAPI:
    """Query project metadata."""

    def __init__(self, state):
        self._state = state

    def _reload(self):
        self._state.reload()

    def list(self) -> list[dict]:
        """List all projects (same as experiments.list)."""
        self._reload()
        from distillate.experiment_tools import list_experiments
        result = list_experiments(state=self._state)
        return _deep(result.get("experiments", []))

    def get(self, identifier: str) -> dict:
        """Get project details."""
        self._reload()
        from distillate.experiment_tools import get_experiment_details
        result = get_experiment_details(state=self._state, identifier=identifier)
        return _deep(result)

    def papers(self, identifier: str) -> list[dict]:
        """Get papers linked to a project."""
        detail = self.get(identifier)
        return _deep(detail.get("linked_paper_details", []))


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class LabAPI:
    """Top-level lab API injected into the REPL sandbox as ``lab``."""

    def __init__(self, state):
        self.papers = PapersAPI(state)
        self.experiments = ExperimentsAPI(state)
        self.notebook = NotebookAPI(state)
        self.projects = ProjectsAPI(state)

    def __repr__(self) -> str:
        return (
            "LabAPI(papers, experiments, notebook, projects)\n"
            "  lab.papers.search(query)             — search papers\n"
            "  lab.papers.get(key)                  — full paper details\n"
            "  lab.papers.recent(count=10)          — recent reads\n"
            "  lab.papers.queue()                   — reading queue\n"
            "  lab.papers.stats()                   — reading statistics\n"
            "  lab.papers.highlights(key)           — paper highlights\n"
            "  lab.experiments.list()               — ExperimentCollection of all\n"
            "  lab.experiments.recent(n=5)          — N most recently active\n"
            "  lab.experiments.get(id)              — project details dict\n"
            "  lab.experiments.runs(id)             — runs for a project\n"
            "  lab.experiments.run_details(p,r)     — single run detail\n"
            "  lab.experiments.active()             — active experiments\n"
            "  lab.notebook.recent(n=20)            — recent notebook entries\n"
            "  lab.notebook.digest(days=7)          — weekly digest\n"
            "  lab.projects.list()                  — all projects (metadata only)\n"
            "  lab.projects.get(id)                 — project metadata\n"
            "  lab.projects.papers(id)              — linked papers\n"
            "\nFluent example:\n"
            "  lab.experiments.recent(n=5).runs.peak_metric()\n"
        )
