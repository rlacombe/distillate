# Covers: distillate/experiment_tools/workspace_tools.py
"""Lock in the JSONL-driven session wrap-up extraction.

The wrap-up flow reads new assistant turns straight from the Claude Code
session JSONL log. These tests pin that behaviour: a fresh assistant turn
with text + ``stop_reason`` must round-trip cleanly, thinking-only and
tool-use-only turns must be skipped, and the ``stop_hook_summary`` JSONL
marker must short-circuit the wait when Claude finished without text.
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path

from unittest.mock import patch

from distillate.experiment_tools.workspace_tools import (
    _inject_wrapup_prompt,
    _jsonl_line_count,
    _resolve_claude_jsonl,
    _wait_for_jsonl_reply,
)


def test_inject_wrapup_prompt_clears_input_first(tmp_path):
    """Bug regression: when the user has pending text in Claude Code's input
    box, the wrap-up prompt is appended and Enter fails to submit the
    multi-line result — the session stalls. Fix: send C-u first to clear
    the input. The test pins the call order: C-u → -l <prompt> → Enter."""
    calls = []

    def _fake_run(args, **kwargs):
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    with patch("subprocess.run", side_effect=_fake_run):
        ok = _inject_wrapup_prompt("test-tmux")
    assert ok is True

    # 3 calls in order: C-u (clear), -l <prompt> (type), Enter (submit).
    assert len(calls) == 3, f"expected 3 send-keys calls, got {len(calls)}: {calls}"
    assert calls[0][:5] == ["tmux", "send-keys", "-t", "test-tmux", "C-u"], (
        f"first call must be C-u to clear pending input, got: {calls[0]}"
    )
    assert calls[1][:5] == ["tmux", "send-keys", "-t", "test-tmux", "-l"], (
        f"second call must type prompt with -l (literal), got: {calls[1]}"
    )
    assert calls[2][:5] == ["tmux", "send-keys", "-t", "test-tmux", "Enter"], (
        f"third call must submit with Enter, got: {calls[2]}"
    )


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


def _append_jsonl(path: Path, messages: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


def _assistant(text: str, stop_reason: str | None = "end_turn") -> dict:
    inner: dict = {
        "model": "claude-test",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
    }
    if stop_reason is not None:
        inner["stop_reason"] = stop_reason
    return {"type": "assistant", "message": inner, "timestamp": "2026-04-10T20:00:00Z"}


def _tool_use_only() -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": "x", "name": "Bash", "input": {}}],
        },
        "timestamp": "2026-04-10T20:00:01Z",
    }


def _stop_hook_summary() -> dict:
    return {
        "type": "system",
        "subtype": "stop_hook_summary",
        "hookCount": 1,
        "hookInfos": [{"command": "http://127.0.0.1:8742/hooks/claude-code/stop"}],
        "hookErrors": [],
        "preventedContinuation": False,
        "timestamp": "2026-04-10T20:00:05Z",
    }


def test_wait_returns_text_of_new_assistant_turn(tmp_path: Path) -> None:
    """A new assistant message with text + stop_reason is returned verbatim."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("old message")])
    baseline = _jsonl_line_count(log)

    _append_jsonl(log, [_assistant("# Wrap Up\n\n- Did A\n- Did B\n- Did C")])

    result = _wait_for_jsonl_reply(log, baseline, timeout=2.0, poll=0.05)
    assert result == "# Wrap Up\n\n- Did A\n- Did B\n- Did C"


def test_wait_skips_tool_use_only_turns(tmp_path: Path) -> None:
    """An assistant turn with only tool_use blocks (no text) is ignored."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    _append_jsonl(log, [_tool_use_only(),
                        _assistant("# Wrap Up\n- Bullet")])

    result = _wait_for_jsonl_reply(log, baseline, timeout=2.0, poll=0.05)
    assert result == "# Wrap Up\n- Bullet"


def test_wait_skips_streaming_turns_without_stop_reason(tmp_path: Path) -> None:
    """A turn that is still streaming (no stop_reason) is not yet final."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    _append_jsonl(log, [_assistant("partial...", stop_reason=None),
                        _assistant("# Done\n- Final")])

    result = _wait_for_jsonl_reply(log, baseline, timeout=2.0, poll=0.05)
    assert result == "# Done\n- Final"


def test_wait_returns_none_when_no_new_turn(tmp_path: Path) -> None:
    """If nothing new lands before the timeout, return None (caller falls back)."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    result = _wait_for_jsonl_reply(log, baseline, timeout=0.5, poll=0.05)
    assert result is None


def test_wait_picks_up_late_arriving_message(tmp_path: Path) -> None:
    """A message that lands MID-WAIT is detected by polling, not just at start."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    def _delayed_writer() -> None:
        time.sleep(0.3)
        _append_jsonl(log, [_assistant("# Late\n- Bullet")])

    threading.Thread(target=_delayed_writer, daemon=True).start()

    result = _wait_for_jsonl_reply(log, baseline, timeout=3.0, poll=0.05)
    assert result == "# Late\n- Bullet"


def test_wait_returns_text_via_jsonl_polling(tmp_path: Path) -> None:
    """Text detection is purely JSONL-based (no tmux status dependency)."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    def _delayed_writer() -> None:
        time.sleep(0.1)
        _append_jsonl(log, [_assistant("# Quick\n- bullet")])

    threading.Thread(target=_delayed_writer, daemon=True).start()

    result = _wait_for_jsonl_reply(log, baseline, timeout=5.0, poll=0.05, tmux_name="t1")
    assert result == "# Quick\n- bullet"


def test_wait_stop_hook_returns_none_fast(tmp_path: Path) -> None:
    """When stop_hook_summary lands without a text end_turn, return None
    immediately rather than waiting out the full timeout."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    def _delayed_hook() -> None:
        time.sleep(0.2)
        _append_jsonl(log, [_stop_hook_summary()])

    threading.Thread(target=_delayed_hook, daemon=True).start()

    start = time.monotonic()
    result = _wait_for_jsonl_reply(log, baseline, timeout=5.0, poll=0.05, tmux_name="t1")
    elapsed = time.monotonic() - start
    assert result is None
    assert elapsed < 1.0, f"should return fast on stop_hook_summary, took {elapsed:.2f}s"


def test_wait_stop_hook_returns_text_if_present(tmp_path: Path) -> None:
    """If both text end_turn AND stop_hook_summary are present, return text."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    _append_jsonl(log, [
        _assistant("# Summary\n- Done"),
        _stop_hook_summary(),
    ])

    result = _wait_for_jsonl_reply(log, baseline, timeout=2.0, poll=0.05)
    assert result == "# Summary\n- Done"


def test_wait_survives_brief_idle_between_thinking_and_text_turn(
    tmp_path: Path,
) -> None:
    """Regression: in extended-thinking mode Claude writes a thinking-only
    end_turn first, then streams the real text turn ~2s later. The wait
    must NOT return None at the thinking-only end_turn."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    thinking_only = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [{"type": "thinking", "thinking": "reasoning..."}],
        },
        "timestamp": "2026-04-10T20:00:01Z",
    }
    _append_jsonl(log, [thinking_only])

    def _delayed_text_writer() -> None:
        time.sleep(0.3)
        _append_jsonl(log, [_assistant("# Real Summary\n- Line one")])

    threading.Thread(target=_delayed_text_writer, daemon=True).start()

    result = _wait_for_jsonl_reply(log, baseline, timeout=3.0, poll=0.05)
    assert result == "# Real Summary\n- Line one"


def test_wait_joins_multiple_text_blocks(tmp_path: Path) -> None:
    """An assistant turn with multiple text blocks gets them concatenated."""
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [_assistant("baseline")])
    baseline = _jsonl_line_count(log)

    multi = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "stop_reason": "end_turn",
            "content": [
                {"type": "text", "text": "# Title"},
                {"type": "tool_use", "id": "y", "name": "Read", "input": {}},
                {"type": "text", "text": "- Bullet one\n- Bullet two"},
            ],
        },
        "timestamp": "2026-04-10T20:00:02Z",
    }
    _append_jsonl(log, [multi])

    result = _wait_for_jsonl_reply(log, baseline, timeout=2.0, poll=0.05)
    assert result == "# Title\n- Bullet one\n- Bullet two"


def test_jsonl_line_count_handles_missing(tmp_path: Path) -> None:
    """Counting a missing file returns 0, not an exception."""
    assert _jsonl_line_count(tmp_path / "no-such.jsonl") == 0


def _make_fake_claude_home(tmp_path: Path, repo_path: Path) -> tuple[Path, Path]:
    """Build a fake ~/.claude/projects/<encoded-repo>/ inside *tmp_path*.

    Returns ``(fake_home, project_dir)``. The encoded directory name uses
    the same convention as Claude Code: ``Path(repo).resolve()`` with
    ``/`` rewritten to ``-``.
    """
    fake_home = tmp_path / "home"
    encoded = str(repo_path.resolve()).replace("/", "-")
    proj_dir = fake_home / ".claude" / "projects" / encoded
    proj_dir.mkdir(parents=True)
    return fake_home, proj_dir


def test_resolve_claude_jsonl_prefers_named_session(tmp_path: Path, monkeypatch) -> None:
    """When the stored session id matches a file, that file wins regardless of mtime."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_home, proj_dir = _make_fake_claude_home(tmp_path, repo)

    older = proj_dir / "abc.jsonl"
    newer = proj_dir / "def.jsonl"
    older.write_text("{}\n")
    newer.write_text("{}\n")
    import os
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    # Patch the module-private Path alias used by _resolve_claude_jsonl.
    from distillate.experiment_tools import workspace_tools as wt
    monkeypatch.setattr(wt._Path, "home", classmethod(lambda cls: fake_home))

    resolved = _resolve_claude_jsonl(str(repo), "abc")
    assert resolved == older  # named match wins over the newer mtime


def test_resolve_claude_jsonl_falls_back_to_latest(tmp_path: Path, monkeypatch) -> None:
    """When no named match, the most recently modified jsonl is used."""
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_home, proj_dir = _make_fake_claude_home(tmp_path, repo)

    older = proj_dir / "abc.jsonl"
    newer = proj_dir / "def.jsonl"
    older.write_text("{}\n")
    newer.write_text("{}\n")
    import os
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    from distillate.experiment_tools import workspace_tools as wt
    monkeypatch.setattr(wt._Path, "home", classmethod(lambda cls: fake_home))

    resolved = _resolve_claude_jsonl(str(repo), "")  # no session id
    assert resolved == newer
