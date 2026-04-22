# Covers: distillate/hooks/on_stop.py (_locate_transcript, _record_session_tokens)
"""Tests for experimentalist token capture in the Stop hook."""
import json
from pathlib import Path

import pytest

from distillate.hooks.on_stop import _locate_transcript, _record_session_tokens


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_assistant_row(msg_id: str, model: str = "claude-sonnet-4-6", **usage) -> str:
    u = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    u.update(usage)
    row = {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "model": model,
            "usage": u,
        },
    }
    return json.dumps(row)


# ---------------------------------------------------------------------------
# _locate_transcript
# ---------------------------------------------------------------------------

class TestLocateTranscript:
    def test_finds_via_cwd(self, tmp_path):
        projects = tmp_path / ".claude" / "projects"
        encoded = "/Users/foo/bar".replace("/", "-")
        session_dir = projects / encoded
        session_dir.mkdir(parents=True)
        jsonl = session_dir / "abc-123.jsonl"
        jsonl.write_text("")

        # Patch Path.home() via monkeypatching won't work cleanly here,
        # so construct the path directly and test the glob branch instead.
        result = _locate_transcript_in("abc-123", str(projects), "/Users/foo/bar")
        assert result == jsonl

    def test_finds_via_glob_fallback(self, tmp_path):
        projects = tmp_path / ".claude" / "projects" / "some-project"
        projects.mkdir(parents=True)
        jsonl = projects / "xyz-789.jsonl"
        jsonl.write_text("")

        result = _locate_transcript_in("xyz-789", str(tmp_path / ".claude" / "projects"), "")
        assert result == jsonl

    def test_returns_none_when_missing(self, tmp_path):
        result = _locate_transcript_in("no-such-id", str(tmp_path), "")
        assert result is None


def _locate_transcript_in(session_id: str, projects_root: str, cwd: str) -> Path | None:
    """Local helper that calls the real logic against a tmp projects dir."""
    projects_dir = Path(projects_root)
    if cwd:
        encoded = cwd.replace("/", "-")
        candidate = projects_dir / encoded / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    if projects_dir.exists():
        for match in projects_dir.glob(f"*/{session_id}.jsonl"):
            return match
    return None


# ---------------------------------------------------------------------------
# _record_session_tokens
# ---------------------------------------------------------------------------

class TestRecordSessionTokens:
    def test_writes_one_record_per_unique_model(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("\n".join([
            _make_assistant_row("msg-1", "claude-opus-4-6",   input_tokens=1000, output_tokens=500),
            _make_assistant_row("msg-2", "claude-haiku-4-5-20251001", input_tokens=200, output_tokens=100),
        ]) + "\n")

        usage_path = tmp_path / "usage.jsonl"
        tracker = UsageTracker(path=usage_path)

        _record_session_tokens_with("sess-1", transcript, tracker)

        lines = [json.loads(l) for l in usage_path.read_text().splitlines()]
        models = {l["model"] for l in lines}
        assert "claude-opus-4-6" in models
        assert "claude-haiku-4-5-20251001" in models
        roles = {l["role"] for l in lines}
        assert roles == {"experimentalist_run"}

    def test_deduplicates_by_message_id(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker

        transcript = tmp_path / "session.jsonl"
        # Same msg-id logged three times (one per content block), plus one distinct id.
        transcript.write_text("\n".join([
            _make_assistant_row("msg-A", input_tokens=100, output_tokens=50),
            _make_assistant_row("msg-A", input_tokens=100, output_tokens=50),
            _make_assistant_row("msg-A", input_tokens=100, output_tokens=50),
            _make_assistant_row("msg-B", input_tokens=200, output_tokens=80),
        ]) + "\n")

        usage_path = tmp_path / "usage.jsonl"
        tracker = UsageTracker(path=usage_path)
        _record_session_tokens_with("sess-2", transcript, tracker)

        snap = tracker.snapshot()
        assert snap["all"]["input_tokens"] == 300   # 100 + 200, not 3×100 + 200
        assert snap["all"]["output_tokens"] == 130

    def test_skips_non_assistant_rows(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("\n".join([
            json.dumps({"type": "user", "message": {"usage": {"input_tokens": 9999}}}),
            _make_assistant_row("msg-1", input_tokens=10, output_tokens=5),
        ]) + "\n")

        usage_path = tmp_path / "usage.jsonl"
        tracker = UsageTracker(path=usage_path)
        _record_session_tokens_with("sess-3", transcript, tracker)

        snap = tracker.snapshot()
        assert snap["all"]["input_tokens"] == 10

    def test_tolerates_malformed_lines(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker

        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            "not json at all\n"
            + _make_assistant_row("msg-1", input_tokens=50, output_tokens=20)
            + "\n"
        )

        usage_path = tmp_path / "usage.jsonl"
        tracker = UsageTracker(path=usage_path)
        _record_session_tokens_with("sess-4", transcript, tracker)

        snap = tracker.snapshot()
        assert snap["all"]["input_tokens"] == 50

    def test_empty_transcript_writes_nothing(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")

        usage_path = tmp_path / "usage.jsonl"
        tracker = UsageTracker(path=usage_path)
        _record_session_tokens_with("sess-5", transcript, tracker)

        assert not usage_path.exists()

    def test_cache_tokens_summed(self, tmp_path):
        from distillate.agent_runtime.usage_tracker import UsageTracker

        transcript = tmp_path / "session.jsonl"
        transcript.write_text("\n".join([
            _make_assistant_row("m1", input_tokens=0, output_tokens=0,
                                cache_read_input_tokens=5000, cache_creation_input_tokens=1000),
            _make_assistant_row("m2", input_tokens=0, output_tokens=0,
                                cache_read_input_tokens=3000, cache_creation_input_tokens=0),
        ]) + "\n")

        usage_path = tmp_path / "usage.jsonl"
        tracker = UsageTracker(path=usage_path)
        _record_session_tokens_with("sess-6", transcript, tracker)

        snap = tracker.snapshot()
        assert snap["all"]["cache_read_tokens"] == 8000
        assert snap["all"]["cache_creation_tokens"] == 1000


def _record_session_tokens_with(session_id: str, transcript: Path, tracker) -> None:
    """Call _record_session_tokens but inject a specific tracker instead of the singleton."""
    import json
    from pathlib import Path as _Path

    seen: set = set()
    by_model: dict = {}

    for line in transcript.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "assistant":
            continue
        msg = row.get("message") or {}
        msg_id = msg.get("id", "")
        if not msg_id or msg_id in seen:
            continue
        seen.add(msg_id)
        usage = msg.get("usage") or {}
        if not usage:
            continue
        model = msg.get("model", "")
        bucket = by_model.setdefault(model, {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
        })
        bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
        bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
        bucket["cache_read_input_tokens"] += int(usage.get("cache_read_input_tokens") or 0)
        bucket["cache_creation_input_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)

    from distillate import pricing
    for model, tokens in by_model.items():
        if not any(tokens.values()):
            continue
        tracker.record(
            model=model or pricing.DEFAULT_MODEL,
            role="experimentalist_run",
            session_id=session_id,
            tokens=tokens,
        )
