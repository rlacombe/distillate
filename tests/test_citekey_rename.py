"""Tests for citekey rename on metadata sync."""

import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point state module at a temp directory so tests don't touch real state."""
    import distillate.state as state_mod

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "state.lock"
    monkeypatch.setattr(state_mod, "STATE_PATH", state_file)
    monkeypatch.setattr(state_mod, "LOCK_PATH", lock_file)
    yield tmp_path


@pytest.fixture()
def obs_env(tmp_path, monkeypatch):
    """Set up obsidian / output environment for tests."""
    from distillate import config

    monkeypatch.setattr(config, "OBSIDIAN_VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_NAME", "Notes")
    monkeypatch.setattr(config, "OBSIDIAN_PAPERS_FOLDER", "Distillate")
    monkeypatch.setattr(config, "OUTPUT_PATH", "")
    return tmp_path / "vault" / "Distillate"


# ---------------------------------------------------------------------------
# rename_paper — file renames
# ---------------------------------------------------------------------------

class TestRenamePaperFiles:
    def test_renames_note_and_pdf(self, obs_env):
        """Both .md and .pdf are renamed when citekey changes."""
        from distillate import obsidian

        saved = obs_env / "Saved"
        saved.mkdir(parents=True)
        (saved / "amodei_machines.md").write_text("# Note")
        (saved / "amodei_machines.pdf").write_bytes(b"PDF")

        result = obsidian.rename_paper("Machines", "amodei_machines", "amodei_machines_2025")

        assert result is True
        assert (saved / "amodei_machines_2025.md").exists()
        assert (saved / "amodei_machines_2025.pdf").exists()
        assert not (saved / "amodei_machines.md").exists()
        assert not (saved / "amodei_machines.pdf").exists()

    def test_skip_when_target_exists(self, obs_env):
        """Don't overwrite if target file already exists."""
        from distillate import obsidian

        saved = obs_env / "Saved"
        saved.mkdir(parents=True)
        (saved / "old_key.md").write_text("old content")
        (saved / "new_key.md").write_text("new content")

        result = obsidian.rename_paper("Paper", "old_key", "new_key")

        assert result is False
        assert (saved / "old_key.md").read_text() == "old content"
        assert (saved / "new_key.md").read_text() == "new content"

    def test_missing_source_no_crash(self, obs_env):
        """Missing source files are silently skipped."""
        from distillate import obsidian

        saved = obs_env / "Saved"
        saved.mkdir(parents=True)

        result = obsidian.rename_paper("Paper", "nonexistent", "new_key")

        assert result is False

    def test_rename_from_title_based_name(self, obs_env):
        """Empty old citekey falls back to sanitized title."""
        from distillate import obsidian

        saved = obs_env / "Saved"
        saved.mkdir(parents=True)
        (saved / "My Great Paper.md").write_text("# Note")

        result = obsidian.rename_paper("My Great Paper", "", "smith_great_2025")

        assert result is True
        assert (saved / "smith_great_2025.md").exists()
        assert not (saved / "My Great Paper.md").exists()


# ---------------------------------------------------------------------------
# rename_paper — reading log wikilinks
# ---------------------------------------------------------------------------

class TestRenamePaperLog:
    def test_updates_reading_log_wikilinks(self, obs_env):
        """Wikilinks in Distillate Log.md are updated."""
        from distillate import obsidian

        obs_env.mkdir(parents=True, exist_ok=True)
        (obs_env / "Saved").mkdir(parents=True)
        (obs_env / "Saved" / "old_key.md").write_text("# Note")

        log_path = obs_env / "Distillate Log.md"
        log_path.write_text(
            "# Distillate Log\n\n"
            "- 2026-02-17 — [[old_key|Paper Title]] — Summary\n"
        )

        obsidian.rename_paper("Paper Title", "old_key", "new_key")

        content = log_path.read_text()
        assert "[[new_key|Paper Title]]" in content
        assert "[[old_key|" not in content

    def test_no_log_no_crash(self, obs_env):
        """Missing reading log file is silently ignored."""
        from distillate import obsidian

        saved = obs_env / "Saved"
        saved.mkdir(parents=True)
        (saved / "old_key.md").write_text("# Note")

        # No Distillate Log.md — should not crash
        result = obsidian.rename_paper("Paper", "old_key", "new_key")
        assert result is True


# ---------------------------------------------------------------------------
# update_note_frontmatter — citekey + pdf fields
# ---------------------------------------------------------------------------

class TestFrontmatterCitekeyUpdate:
    def test_updates_citekey_and_pdf_fields(self, obs_env):
        """Citekey and pdf frontmatter fields are set on update."""
        from distillate import obsidian

        saved = obs_env / "Saved"
        saved.mkdir(parents=True)
        note_path = saved / "smith_great_2025.md"
        note_path.write_text(
            '---\ntitle: "Great Paper"\ncitekey: "smith_great"\npdf: "[[smith_great.pdf]]"\n---\n\nBody\n'
        )

        metadata = {
            "citekey": "smith_great_2025",
            "authors": ["Smith"],
            "tags": [],
        }
        obsidian.update_note_frontmatter("Great Paper", metadata, citekey="smith_great_2025")

        content = note_path.read_text()
        assert 'citekey: "smith_great_2025"' in content
        assert 'pdf: "[[smith_great_2025.pdf]]"' in content


# ---------------------------------------------------------------------------
# Zotero PATCH helpers
# ---------------------------------------------------------------------------

class TestUpdateObsidianLink:
    def test_patches_obsidian_link(self, monkeypatch):
        """update_obsidian_link PATCHes the correct attachment."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "123")

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = [
            {
                "key": "LINK1",
                "version": 5,
                "data": {"title": "Open in Obsidian", "linkMode": "linked_url", "url": "old"},
            },
        ]

        patch_resp = MagicMock()
        patch_resp.status_code = 204

        monkeypatch.setattr(zotero_client, "_get", lambda *a, **kw: get_resp)
        monkeypatch.setattr(zotero_client, "_patch", lambda *a, **kw: patch_resp)

        result = zotero_client.update_obsidian_link("PARENT1", "obsidian://new-url")
        assert result is True

    def test_returns_false_no_attachment(self, monkeypatch):
        """Returns False when no 'Open in Obsidian' attachment exists."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "123")

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = []

        monkeypatch.setattr(zotero_client, "_get", lambda *a, **kw: get_resp)

        result = zotero_client.update_obsidian_link("PARENT1", "obsidian://new-url")
        assert result is False


class TestUpdateLinkedAttachmentPath:
    def test_patches_linked_file(self, monkeypatch):
        """update_linked_attachment_path PATCHes the correct attachment."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "123")

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = [
            {
                "key": "FILE1",
                "version": 3,
                "data": {"linkMode": "linked_file", "title": "old.pdf", "path": "/old/path"},
            },
        ]

        patch_resp = MagicMock()
        patch_resp.status_code = 204

        monkeypatch.setattr(zotero_client, "_get", lambda *a, **kw: get_resp)
        monkeypatch.setattr(zotero_client, "_patch", lambda *a, **kw: patch_resp)

        result = zotero_client.update_linked_attachment_path("PARENT1", "new.pdf", "/new/path")
        assert result is True

    def test_returns_false_no_linked_file(self, monkeypatch):
        """Returns False when no linked_file attachment exists."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "123")

        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = []

        monkeypatch.setattr(zotero_client, "_get", lambda *a, **kw: get_resp)

        result = zotero_client.update_linked_attachment_path("PARENT1", "new.pdf", "/new/path")
        assert result is False
