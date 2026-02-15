"""Tests for metadata sync, frontmatter parsing, and email enrichment helpers."""

import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestParseFrontmatterBlocks:
    """Tests for obsidian._parse_frontmatter_blocks()."""

    def test_simple_key_values(self):
        from distillate.obsidian import _parse_frontmatter_blocks

        fm = 'title: "My Paper"\ndate_added: 2026-01-15\ndate_read: 2026-02-10'
        blocks = _parse_frontmatter_blocks(fm)

        assert list(blocks.keys()) == ["title", "date_added", "date_read"]
        assert blocks["title"] == 'title: "My Paper"'
        assert blocks["date_added"] == "date_added: 2026-01-15"

    def test_multiline_list(self):
        from distillate.obsidian import _parse_frontmatter_blocks

        fm = (
            'title: "Test"\n'
            "authors:\n"
            "  - Alice\n"
            "  - Bob\n"
            "date_added: 2026-01-01"
        )
        blocks = _parse_frontmatter_blocks(fm)

        assert list(blocks.keys()) == ["title", "authors", "date_added"]
        assert "Alice" in blocks["authors"]
        assert "Bob" in blocks["authors"]

    def test_preserves_order(self):
        from distillate.obsidian import _parse_frontmatter_blocks

        fm = "a: 1\nb: 2\nc: 3\nd: 4"
        blocks = _parse_frontmatter_blocks(fm)
        assert list(blocks.keys()) == ["a", "b", "c", "d"]

    def test_tags_block(self):
        from distillate.obsidian import _parse_frontmatter_blocks

        fm = (
            "tags:\n"
            "  - paper\n"
            "  - read\n"
            "  - machine-learning"
        )
        blocks = _parse_frontmatter_blocks(fm)
        assert "tags" in blocks
        assert "machine-learning" in blocks["tags"]


class TestRebuildFrontmatter:
    """Tests for obsidian._rebuild_frontmatter()."""

    def test_roundtrip(self):
        from distillate.obsidian import (
            _parse_frontmatter_blocks,
            _rebuild_frontmatter,
        )

        fm = (
            'title: "Test Paper"\n'
            "authors:\n"
            "  - Alice\n"
            "  - Bob\n"
            "date_added: 2026-01-01\n"
            "tags:\n"
            "  - paper\n"
            "  - read"
        )
        blocks = _parse_frontmatter_blocks(fm)
        result = _rebuild_frontmatter(blocks)
        assert result == fm


class TestUpdateNoteFrontmatter:
    """Tests for obsidian.update_note_frontmatter()."""

    def test_updates_tags_and_authors(self, tmp_path):
        from distillate.obsidian import (
            update_note_frontmatter,
        )

        note_content = textwrap.dedent("""\
        ---
        title: "Test Paper"
        authors:
          - Old Author
        date_added: 2026-01-01
        date_read: 2026-02-01
        zotero: "zotero://select/library/items/ABC123"
        tags:
          - paper
          - read
          - old-tag
        ---

        # Test Paper

        Some content here.
        """)

        note_path = tmp_path / "Read" / "Test Paper.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text(note_content)

        metadata = {
            "authors": ["New Author", "Second Author"],
            "tags": ["new-tag", "ml"],
            "doi": "10.1234/test",
        }

        with patch("distillate.obsidian._read_dir", return_value=tmp_path / "Read"):
            result = update_note_frontmatter("Test Paper", metadata)

        assert result is True
        updated = note_path.read_text()

        # Check authors updated
        assert "New Author" in updated
        assert "Second Author" in updated
        assert "Old Author" not in updated

        # Check tags updated
        assert "new-tag" in updated
        assert "ml" in updated
        assert "old-tag" not in updated
        assert "paper" in updated  # preserved prefix
        assert "read" in updated

        # Check DOI added
        assert "10.1234/test" in updated

        # Check body preserved
        assert "Some content here." in updated

        # Check other frontmatter preserved
        assert "zotero:" in updated
        assert "date_added: 2026-01-01" in updated

    def test_returns_false_for_missing_note(self, tmp_path):
        from distillate.obsidian import update_note_frontmatter

        with patch("distillate.obsidian._read_dir", return_value=tmp_path):
            result = update_note_frontmatter("Nonexistent", {})

        assert result is False


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------


class TestTagPillsHtml:
    """Tests for digest._tag_pills_html()."""

    def test_empty_tags(self):
        from distillate.digest import _tag_pills_html
        assert _tag_pills_html([]) == ""

    def test_renders_pills(self):
        from distillate.digest import _tag_pills_html
        html = _tag_pills_html(["ml", "nlp"])
        assert "ml" in html
        assert "nlp" in html
        assert "border-radius" in html

    def test_deterministic_colors(self):
        from distillate.digest import _tag_pills_html
        html1 = _tag_pills_html(["ml"])
        html2 = _tag_pills_html(["ml"])
        assert html1 == html2


class TestReadingVelocityHtml:
    """Tests for digest._reading_velocity_html()."""

    def test_renders_counts(self):
        from distillate.digest import _reading_velocity_html

        state = MagicMock()
        state.documents_processed_since = MagicMock(
            side_effect=lambda since: [{}] * 2 if "days=7" not in since else [{}] * 5
        )
        # Both calls return some docs
        state.documents_processed_since.side_effect = [
            [{}, {}],       # week count = 2
            [{}, {}, {}],   # month count = 3
        ]

        html = _reading_velocity_html(state)
        assert "Read 2 papers this week" in html
        assert "3 this month." in html

    def test_singular_paper(self):
        from distillate.digest import _reading_velocity_html

        state = MagicMock()
        state.documents_processed_since.side_effect = [
            [{}],   # week = 1
            [{}],   # month = 1
        ]

        html = _reading_velocity_html(state)
        assert "Read 1 paper this week" in html


class TestQueueHealthHtml:
    """Tests for digest._queue_health_html()."""

    def test_renders_stats(self):
        from distillate.digest import _queue_health_html

        now = datetime.now(timezone.utc)
        old_date = (now - timedelta(days=45)).isoformat()

        state = MagicMock()
        state.documents_with_status.return_value = [
            {"uploaded_at": old_date},
            {"uploaded_at": now.isoformat()},
            {"uploaded_at": now.isoformat()},
        ]
        state.documents = {
            "a": {"uploaded_at": now.isoformat(), "status": "on_remarkable"},
        }
        state.documents_processed_since.return_value = [{}]

        html = _queue_health_html(state)
        assert "3 papers waiting" in html
        assert "45 days" in html or "44 days" in html  # timezone edge
        assert "+1 added" in html
        assert "-1 read" in html
