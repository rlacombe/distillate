"""Shared test fixtures."""
import gc
import os
import pytest


@pytest.fixture(autouse=True)
def _cleanup_memory():
    """Force garbage collection between tests to prevent memory accumulation.

    Critical for long test suites where State objects and other large
    structures can accumulate if not explicitly freed. This prevents
    test memory usage from growing unbounded (38GB+ leak in 116 tests).
    """
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def isolate_secrets(monkeypatch):
    """Reset secrets cache between tests so tests don't share cached credentials."""
    import distillate.secrets as sec
    monkeypatch.setattr(sec, "_cache", {})


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Isolate the SQLite backend: fresh DB per test, connection reset.

    Separate from isolate_state so that tests with local isolate_state
    fixtures still get DB isolation.
    """
    import distillate.db as db_mod
    db_file = tmp_path / "state.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    db_mod.close()
    yield
    db_mod.close()


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    yield tmp_path


@pytest.fixture(autouse=True)
def isolate_kb_dir(tmp_path, monkeypatch):
    """Point KB directory at a temp directory so tests don't write to real KB.

    Critical: tests that call save_session_summary_tool or append_lab_book_tool
    write to _KB_DIR. Without this fixture, test data (ws-001 entries, etc.)
    persists in ~/.config/distillate/knowledge and pollutes the real Notebook.
    """
    import distillate.lab_notebook as nb_mod
    kb_dir = tmp_path / "knowledge"
    notebook_root = kb_dir / "notebook"
    monkeypatch.setattr(nb_mod, "_KB_DIR", kb_dir)
    # NOTEBOOK_ROOT is computed from _KB_DIR at module import time — must patch it too
    monkeypatch.setattr(nb_mod, "NOTEBOOK_ROOT", notebook_root)
    monkeypatch.setattr(nb_mod, "PINS_PATH", notebook_root / "pins.json")
    # Also set the env var so any code that re-reads it gets the temp path
    monkeypatch.setenv("DISTILLATE_KNOWLEDGE_DIR", str(kb_dir))
    yield kb_dir


def pytest_sessionfinish(session, exitstatus):
    """Warn if peak RSS exceeds 2 GB — indicates a leaking test."""
    try:
        import psutil
        rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        if rss_mb > 2048:
            print(f"\nWARNING: peak RSS {rss_mb:.0f} MB — investigate leaking tests")
    except ImportError:
        pass


@pytest.fixture
def force_remarkable_mode(monkeypatch):
    """Force READING_SOURCE=remarkable for tests written against the legacy path.

    The default flipped to zotero — tests that assert on reMarkable-specific
    output (e.g., "On reMarkable (N)" groups, rm upload calls) should use this
    fixture explicitly.
    """
    import distillate.config as config
    monkeypatch.setattr(config, "READING_SOURCE", "remarkable")
