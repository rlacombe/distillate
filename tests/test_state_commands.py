# Covers: distillate/commands.py — export/import state commands

import json

import pytest


@pytest.fixture()
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod
    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    return tmp_path


class TestExportImportState:
    def test_export_state(self, isolate_state, capsys):
        from distillate.state import STATE_PATH
        from distillate.commands import _export_state

        STATE_PATH.write_text(json.dumps({"documents": {}, "schema_version": 1}))
        dest = isolate_state / "exported.json"
        _export_state(str(dest))

        assert dest.exists()
        data = json.loads(dest.read_text())
        assert data["schema_version"] == 1
        out = capsys.readouterr().out
        assert "exported" in out.lower()

    def test_export_no_state(self, isolate_state, capsys):
        from distillate.commands import _export_state
        _export_state(str(isolate_state / "out.json"))
        out = capsys.readouterr().out
        assert "no state" in out.lower() or "nothing" in out.lower()

    def test_import_valid_state(self, isolate_state, capsys):
        from distillate.state import STATE_PATH
        from distillate.commands import _import_state

        # Create source file
        src = isolate_state / "import.json"
        src.write_text(json.dumps({
            "schema_version": 1,
            "documents": {"K1": {"title": "Paper A"}},
        }))

        _import_state(str(src))

        imported = json.loads(STATE_PATH.read_text())
        assert "K1" in imported["documents"]
        out = capsys.readouterr().out
        assert "1 papers" in out

    def test_import_backs_up_existing(self, isolate_state, capsys):
        from distillate.state import STATE_PATH
        from distillate.commands import _import_state

        # Create existing state
        STATE_PATH.write_text(json.dumps({"documents": {}, "schema_version": 1}))

        src = isolate_state / "new_state.json"
        src.write_text(json.dumps({"documents": {"K1": {}}, "schema_version": 1}))

        _import_state(str(src))

        backup = STATE_PATH.with_suffix(".json.bak")
        assert backup.exists()
        out = capsys.readouterr().out
        assert "backed up" in out.lower()

    def test_import_invalid_json(self, isolate_state):
        from distillate.commands import _import_state

        src = isolate_state / "bad.json"
        src.write_text("{invalid json")

        with pytest.raises(SystemExit):
            _import_state(str(src))

    def test_import_missing_documents_key(self, isolate_state):
        from distillate.commands import _import_state

        src = isolate_state / "nokey.json"
        src.write_text(json.dumps({"other": "data"}))

        with pytest.raises(SystemExit):
            _import_state(str(src))

    def test_import_nonexistent_file(self, isolate_state):
        from distillate.commands import _import_state

        with pytest.raises(SystemExit):
            _import_state(str(isolate_state / "nope.json"))
