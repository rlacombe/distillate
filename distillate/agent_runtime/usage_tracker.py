"""Usage tracker — JSONL event log + rolling aggregates.

One row per Nicolas turn, lab_repl sub-call, or experimentalist run stop.
The desktop billing pill and the ``/usage`` HTTP endpoint both read
aggregates from :meth:`UsageTracker.snapshot`.

Event row::

    {
      "ts":         "2026-04-15T21:04:22Z",
      "model":      "claude-sonnet-4-6",
      "role":       "nicolas_turn" | "lab_repl_subcall" | "experimentalist_run",
      "session_id": "abc-123",
      "tokens":     {"input_tokens": 1200, "output_tokens": 450,
                     "cache_read_input_tokens": 10200,
                     "cache_creation_input_tokens": 0},
      "cost_usd":   0.0483
    }

Append-only. For ``experimentalist_run`` rows the tokens are the *delta*
since the previous stop event for that session/model pair — not cumulative
session totals.  Use :meth:`repair_cumulative_entries` to back-fill any
log entries written before this invariant was enforced.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from distillate import config, pricing

log = logging.getLogger(__name__)

USAGE_PATH: Path = config.CONFIG_DIR / "usage.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_iso(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _empty_bucket() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": 0.0,
        "api_cost_usd": 0.0,
        "subscription_cost_usd": 0.0,
        "calls": 0,
    }


def _add_row(bucket: dict, tokens: dict, cost_usd: float, billing_source: str = "api") -> None:
    bucket["input_tokens"] += int(tokens.get("input_tokens") or 0)
    bucket["output_tokens"] += int(tokens.get("output_tokens") or 0)
    bucket["cache_read_tokens"] += int(tokens.get("cache_read_input_tokens") or 0)
    bucket["cache_creation_tokens"] += int(tokens.get("cache_creation_input_tokens") or 0)
    bucket["cost_usd"] += float(cost_usd or 0.0)
    if billing_source == "subscription":
        bucket["subscription_cost_usd"] += float(cost_usd or 0.0)
    else:
        bucket["api_cost_usd"] += float(cost_usd or 0.0)
    bucket["calls"] += 1


class UsageTracker:
    """Event log + in-memory session cutoffs for the ``reset_session`` semantics."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._session_cutoffs: dict[str, datetime] = {}

    # -- write path ---------------------------------------------------------

    def record(
        self,
        *,
        model: str,
        role: str,
        session_id: str,
        tokens: dict,
        cost_usd: float | None = None,
        billing_source: str = "api",
    ) -> None:
        if cost_usd is None:
            cost_usd = pricing.cost_for_usage(model, tokens)
        row = {
            "ts": _now_iso(),
            "model": model,
            "role": role,
            "session_id": session_id,
            "tokens": {
                "input_tokens": int(tokens.get("input_tokens") or 0),
                "output_tokens": int(tokens.get("output_tokens") or 0),
                "cache_read_input_tokens": int(tokens.get("cache_read_input_tokens") or 0),
                "cache_creation_input_tokens": int(tokens.get("cache_creation_input_tokens") or 0),
            },
            "cost_usd": round(float(cost_usd), 6),
            "billing_source": billing_source,
        }
        line = json.dumps(row) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)

    def reset_session(self, session_id: str) -> None:
        """Mark events for *session_id* before now as out-of-scope for
        snapshot's ``session`` bucket. Events remain in the file and still
        show up in ``today`` / ``week`` / ``all`` / ``by_model``.
        """
        self._session_cutoffs[session_id] = datetime.now(timezone.utc)

    def repair_cumulative_entries(self, *, backup: bool = True) -> dict[str, int]:
        """Convert any cumulative ``experimentalist_run`` entries to per-run deltas.

        Before the 2026-04-21 fix, on_stop.py re-read the full Claude Code
        transcript on every run-stop, recording the CUMULATIVE session total
        each time instead of the incremental delta.  This produced N entries
        with identical (or monotonically increasing) token counts for a single
        session, massively over-counting spend.

        This method detects those groups, collapses them to their true
        incremental values with costs recomputed at the correct model price,
        and rewrites the file in place (preserving a ``.bak-pre-repair``
        backup by default).

        Safe to call multiple times — idempotent once the entries are deltas.

        Returns ``{"rows_before": N, "rows_after": M}``.
        """
        from collections import defaultdict

        rows = list(self._iter_rows())
        rows_before = len(rows)

        non_exp = [r for r in rows if r.get("role") != "experimentalist_run"]
        exp = sorted(
            (r for r in rows if r.get("role") == "experimentalist_run"),
            key=lambda r: r.get("ts", ""),
        )

        if not exp:
            return {"rows_before": rows_before, "rows_after": rows_before}

        groups: dict[tuple, list] = defaultdict(list)
        for r in exp:
            groups[(r.get("session_id", ""), r.get("model", ""))].append(r)

        fixed_exp: list[dict] = []
        _ZERO = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        for (_, model), group in groups.items():
            prev = dict(_ZERO)
            for row in group:
                curr = row.get("tokens") or {}
                delta = {
                    k: max(0, int(curr.get(k) or 0) - prev[k])
                    for k in _ZERO
                }
                # Advance the cursor regardless of whether delta is zero
                for k in _ZERO:
                    prev[k] = int(curr.get(k) or 0)
                if not any(delta.values()):
                    continue  # Pure duplicate — drop it
                new_row = {**row, "tokens": delta,
                           "cost_usd": round(pricing.cost_for_usage(model, delta), 6)}
                fixed_exp.append(new_row)

        all_fixed = sorted(non_exp + fixed_exp, key=lambda r: r.get("ts", ""))
        rows_after = len(all_fixed)

        if backup and self.path.exists():
            bak = self.path.with_suffix(".jsonl.bak-pre-repair")
            bak.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")

        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as f:
                for row in all_fixed:
                    f.write(json.dumps(row) + "\n")

        return {"rows_before": rows_before, "rows_after": rows_after}

    # -- read path ----------------------------------------------------------

    def _iter_rows(self):
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate hand-edits / partial writes

    def snapshot(self, session_id: str | None = None) -> dict:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
        session_cutoff = self._session_cutoffs.get(session_id) if session_id else None

        buckets = {
            "session": _empty_bucket(),
            "today": _empty_bucket(),
            "week": _empty_bucket(),
            "month": _empty_bucket(),
            "all": _empty_bucket(),
        }
        by_model: dict[str, dict] = {}

        for row in self._iter_rows():
            ts = _parse_iso(row.get("ts", ""))
            if ts is None:
                continue
            tokens = row.get("tokens") or {}
            cost = row.get("cost_usd", 0.0)
            model = row.get("model", "")
            source = row.get("billing_source", "api")

            _add_row(buckets["all"], tokens, cost, source)
            if ts >= month_start:
                _add_row(buckets["month"], tokens, cost, source)
            if ts >= today_start:
                _add_row(buckets["today"], tokens, cost, source)
            if ts >= week_start:
                _add_row(buckets["week"], tokens, cost, source)

            if session_id and row.get("session_id") == session_id:
                if session_cutoff is None or ts >= session_cutoff:
                    _add_row(buckets["session"], tokens, cost, source)

            if model:
                by_model.setdefault(model, _empty_bucket())
                _add_row(by_model[model], tokens, cost, source)

        # Round cost for display stability.
        for b in buckets.values():
            b["cost_usd"] = round(b["cost_usd"], 6)
            b["api_cost_usd"] = round(b["api_cost_usd"], 6)
            b["subscription_cost_usd"] = round(b["subscription_cost_usd"], 6)
        for b in by_model.values():
            b["cost_usd"] = round(b["cost_usd"], 6)
            b["api_cost_usd"] = round(b["api_cost_usd"], 6)
            b["subscription_cost_usd"] = round(b["subscription_cost_usd"], 6)

        return {
            **buckets,
            "by_model": by_model,
            "current_model": _current_model(),
        }


# ---------------------------------------------------------------------------
# Module-level singleton (one writer per server process)
# ---------------------------------------------------------------------------

_tracker: UsageTracker | None = None


def get_tracker() -> UsageTracker:
    global _tracker
    if _tracker is None or _tracker.path != USAGE_PATH:
        _tracker = UsageTracker(path=USAGE_PATH)
    return _tracker


def _reset_singleton() -> None:
    """Test hook: force the next ``get_tracker()`` to re-read ``USAGE_PATH``."""
    global _tracker
    _tracker = None


def _current_model() -> str:
    """Return the user's currently selected Nicolas model (or the default)."""
    try:
        from distillate import preferences
        return preferences.get("nicolas_model", pricing.DEFAULT_MODEL)
    except Exception:
        return pricing.DEFAULT_MODEL
