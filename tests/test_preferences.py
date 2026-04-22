# Covers: distillate/preferences.py
"""Tests for distillate.preferences — tiny JSON store for user prefs.

RED tests. See docs/research/nicolas-billing-action-plan.md §7.2.

Imports are inlined per-test so pytest collects every case individually
(the module doesn't exist yet — this is the red state).
"""
import json
import pytest


@pytest.fixture
def prefs(tmp_path, monkeypatch):
    """Isolate preferences to a temp dir per test.

    Imports `distillate.preferences` lazily so the fixture raises
    ImportError (not a collection-time SKIP) when the module is missing.
    """
    from distillate import preferences
    from distillate import config

    pref_file = tmp_path / "preferences.json"
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(preferences, "PREFERENCES_PATH", pref_file)
    if hasattr(preferences, "_reset_cache"):
        preferences._reset_cache()
    return preferences


class TestLoad:
    def test_load_empty_returns_defaults(self, prefs):
        from distillate import pricing
        data = prefs.load()
        assert data.get("nicolas_model") == pricing.DEFAULT_MODEL

    def test_load_ignores_unknown_keys(self, prefs, tmp_path):
        (tmp_path / "preferences.json").write_text(
            json.dumps({"nicolas_model": "claude-opus-4-6", "mystery": 42})
        )
        data = prefs.load()
        # We tolerate the extra key but don't crash.
        assert data["nicolas_model"] == "claude-opus-4-6"

    def test_corrupt_file_returns_defaults_and_quarantines(self, prefs, tmp_path):
        pref_file = tmp_path / "preferences.json"
        pref_file.write_text("not json {{{")
        from distillate import pricing

        data = prefs.load()
        assert data["nicolas_model"] == pricing.DEFAULT_MODEL
        # Bad file quarantined, not silently overwritten.
        assert (tmp_path / "preferences.json.bak").exists()


class TestGetSet:
    def test_set_and_get_roundtrip(self, prefs):
        prefs.set("nicolas_model", "claude-opus-4-6")
        assert prefs.get("nicolas_model") == "claude-opus-4-6"

    def test_set_persists_to_disk(self, prefs, tmp_path):
        prefs.set("nicolas_model", "claude-haiku-4-5-20251001")
        on_disk = json.loads((tmp_path / "preferences.json").read_text())
        assert on_disk["nicolas_model"] == "claude-haiku-4-5-20251001"

    def test_get_missing_key_returns_default_arg(self, prefs):
        assert prefs.get("nothing_here", "fallback") == "fallback"

    def test_get_missing_key_returns_none_without_default(self, prefs):
        assert prefs.get("nothing_here") is None

    def test_set_then_load_other_session(self, prefs):
        """A second 'process' (fresh load) sees the written value."""
        prefs.set("nicolas_model", "claude-sonnet-4-5-20250929")
        # Simulate a cold read — no in-memory cache should mask disk state.
        if hasattr(prefs, "_reset_cache"):
            prefs._reset_cache()
        assert prefs.load()["nicolas_model"] == "claude-sonnet-4-5-20250929"


class TestDirectoryCreation:
    def test_config_dir_created_on_set(self, tmp_path, monkeypatch):
        from distillate import preferences
        from distillate import config

        sub = tmp_path / "nested" / "distillate"
        monkeypatch.setattr(config, "CONFIG_DIR", sub)
        monkeypatch.setattr(preferences, "PREFERENCES_PATH", sub / "preferences.json")
        if hasattr(preferences, "_reset_cache"):
            preferences._reset_cache()

        preferences.set("nicolas_model", "claude-opus-4-6")
        assert (sub / "preferences.json").exists()
