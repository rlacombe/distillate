"""Tests for Zotero reader mode (READING_SOURCE=zotero)."""

import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Config: is_zotero_reader()
# ---------------------------------------------------------------------------

class TestZoteroReaderConfig:
    """Config detection for Zotero reader mode."""

    def test_default_is_remarkable(self, monkeypatch):
        monkeypatch.delenv("READING_SOURCE", raising=False)
        from distillate import config
        monkeypatch.setattr(config, "READING_SOURCE", "remarkable")
        assert not config.is_zotero_reader()

    def test_zotero_reader_detected(self, monkeypatch):
        from distillate import config
        monkeypatch.setattr(config, "READING_SOURCE", "zotero")
        assert config.is_zotero_reader()


# ---------------------------------------------------------------------------
# Zotero annotation extraction
# ---------------------------------------------------------------------------

def _make_zotero_annotation(text, page_label="1", page_index=0, rects=None, tags=None):
    """Build a Zotero annotation API response item."""
    pos = {"pageIndex": page_index, "rects": rects or [[72, 700, 540, 712]]}
    data = {
        "annotationType": "highlight",
        "annotationText": text,
        "annotationPageLabel": str(page_label),
        "annotationPosition": json.dumps(pos),
        "annotationColor": "#ffd400",
        "tags": [{"tag": t} for t in (tags or [])],
    }
    return {"data": data}


class TestGetHighlightAnnotations:
    """Tests for zotero_client.get_highlight_annotations()."""

    @patch("distillate.zotero_client._get")
    def test_extracts_highlights_by_page(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_zotero_annotation("First highlight", page_label="1", rects=[[72, 700, 540, 712]]),
            _make_zotero_annotation("Second highlight", page_label="1", rects=[[72, 650, 540, 662]]),
            _make_zotero_annotation("Page two highlight", page_label="2", rects=[[72, 700, 540, 712]]),
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_highlight_annotations
        result = get_highlight_annotations("ATT123")

        assert 1 in result
        assert 2 in result
        assert len(result[1]) == 2
        assert len(result[2]) == 1
        assert result[2][0] == "Page two highlight"

    @patch("distillate.zotero_client._get")
    def test_excludes_distillate_tagged(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_zotero_annotation("User highlight"),
            _make_zotero_annotation("Back-propagated", tags=["distillate"]),
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_highlight_annotations
        result = get_highlight_annotations("ATT123")

        assert len(result.get(1, [])) == 1
        assert result[1][0] == "User highlight"

    @patch("distillate.zotero_client._get")
    def test_sorts_by_position_within_page(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_zotero_annotation("Bottom", page_label="1", rects=[[72, 100, 540, 112]]),
            _make_zotero_annotation("Top", page_label="1", rects=[[72, 700, 540, 712]]),
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_highlight_annotations
        result = get_highlight_annotations("ATT123")

        # Lower y-value comes first in sort (closer to bottom of page)
        assert result[1][0] == "Bottom"
        assert result[1][1] == "Top"

    @patch("distillate.zotero_client._get")
    def test_empty_on_api_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_highlight_annotations
        assert get_highlight_annotations("ATT123") == {}

    @patch("distillate.zotero_client._get")
    def test_skips_empty_text(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_zotero_annotation(""),
            _make_zotero_annotation("  "),
            _make_zotero_annotation("Valid text"),
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_highlight_annotations
        result = get_highlight_annotations("ATT123")

        assert len(result.get(1, [])) == 1


class TestGetRawAnnotations:
    """Tests for zotero_client.get_raw_annotations()."""

    @patch("distillate.zotero_client._get")
    def test_returns_parsed_annotations(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_zotero_annotation(
                "Test highlight", page_label="3", page_index=2,
                rects=[[72, 700, 540, 712], [72, 688, 540, 700]],
            ),
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_raw_annotations
        result = get_raw_annotations("ATT123")

        assert len(result) == 1
        ann = result[0]
        assert ann["text"] == "Test highlight"
        assert ann["page_index"] == 2
        assert ann["page_label"] == "3"
        assert len(ann["rects"]) == 2

    @patch("distillate.zotero_client._get")
    def test_excludes_distillate_tagged(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            _make_zotero_annotation("User highlight"),
            _make_zotero_annotation("Ours", tags=["distillate"]),
        ]
        mock_get.return_value = mock_resp

        from distillate.zotero_client import get_raw_annotations
        result = get_raw_annotations("ATT123")

        assert len(result) == 1
        assert result[0]["text"] == "User highlight"


# ---------------------------------------------------------------------------
# PDF rendering from Zotero annotations
# ---------------------------------------------------------------------------

class TestRenderAnnotatedPdfFromAnnotations:
    """Tests for renderer.render_annotated_pdf_from_annotations()."""

    def _make_pdf_bytes(self):
        """Create a minimal single-page PDF for testing."""
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), "Sample text for testing")
        pdf_bytes = doc.tobytes()
        doc.close()
        return pdf_bytes

    def test_renders_highlights(self, tmp_path):
        from distillate.renderer import render_annotated_pdf_from_annotations

        pdf_bytes = self._make_pdf_bytes()
        output = tmp_path / "annotated.pdf"
        annotations = [
            {"page_index": 0, "rects": [[72, 88, 300, 100]]},
        ]
        result = render_annotated_pdf_from_annotations(pdf_bytes, annotations, output)

        assert result is True
        assert output.exists()
        assert output.stat().st_size > 0

    def test_returns_false_for_empty_annotations(self, tmp_path):
        from distillate.renderer import render_annotated_pdf_from_annotations

        pdf_bytes = self._make_pdf_bytes()
        output = tmp_path / "annotated.pdf"
        result = render_annotated_pdf_from_annotations(pdf_bytes, [], output)

        assert result is False

    def test_skips_out_of_range_pages(self, tmp_path):
        from distillate.renderer import render_annotated_pdf_from_annotations

        pdf_bytes = self._make_pdf_bytes()
        output = tmp_path / "annotated.pdf"
        annotations = [
            {"page_index": 99, "rects": [[72, 88, 300, 100]]},  # out of range
        ]
        result = render_annotated_pdf_from_annotations(pdf_bytes, annotations, output)

        # Still returns True (PDF saved, 0 highlights rendered)
        assert result is True

    def test_handles_multiple_rects(self, tmp_path):
        from distillate.renderer import render_annotated_pdf_from_annotations

        pdf_bytes = self._make_pdf_bytes()
        output = tmp_path / "annotated.pdf"
        annotations = [
            {
                "page_index": 0,
                "rects": [[72, 88, 300, 100], [72, 76, 200, 88]],
            },
        ]
        result = render_annotated_pdf_from_annotations(pdf_bytes, annotations, output)
        assert result is True


# ---------------------------------------------------------------------------
# Init wizard — Zotero reader path
# ---------------------------------------------------------------------------

_WIZARD_ENV_KEYS = [
    "ZOTERO_API_KEY", "ZOTERO_USER_ID", "REMARKABLE_DEVICE_TOKEN",
    "OBSIDIAN_VAULT_PATH", "OUTPUT_PATH", "PDF_SUBFOLDER",
    "KEEP_ZOTERO_PDF", "ANTHROPIC_API_KEY", "RESEND_API_KEY", "DIGEST_TO",
    "READING_SOURCE",
]


class TestInitWizardZoteroReader:
    """Init wizard with Zotero reader choice (option 2)."""

    def test_zotero_reader_skips_remarkable_step(self, tmp_path, monkeypatch, capsys):
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

        inputs = iter([
            "test_api_key",     # API key
            "12345",            # User ID
            "",                 # Skip WebDAV
            "2",                # Any device (not reMarkable)
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder (default pdf)
            "",                 # Keep PDFs (default 1)
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "",                 # Skip newsletter
            "n",                # Skip experiments
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        assert "READING_SOURCE=zotero" in text
        assert "SYNC_HIGHLIGHTS=false" in text

        # Should NOT show Step 2 (reMarkable)
        output = capsys.readouterr().out
        assert "Step 2 of 6" not in output
        assert "Zotero" in output

    def test_remarkable_default_shows_step2(self, tmp_path, monkeypatch, capsys):
        from distillate import config

        env_file = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", env_file)
        for key in _WIZARD_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

        inputs = iter([
            "test_api_key",     # API key
            "12345",            # User ID
            "",                 # Skip WebDAV
            "",                 # Reading surface (default: reMarkable)
            "n",                # Skip reMarkable registration
            "n",                # Don't use Obsidian
            "",                 # Skip plain folder
            "",                 # PDF subfolder
            "",                 # Keep PDFs
            "",                 # Skip Anthropic
            "",                 # Skip Resend
            "",                 # Skip newsletter
            "n",                # Skip experiments
        ])
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch("builtins.input", lambda _: next(inputs)), \
             patch("requests.get", return_value=mock_resp), \
             patch("requests.post", return_value=mock_resp), \
             patch("shutil.which", return_value="/usr/local/bin/rmapi"), \
             patch("platform.system", return_value="Linux"):
            from distillate.main import _init_wizard
            _init_wizard()

        text = env_file.read_text()
        assert "READING_SOURCE=remarkable" in text

        # Should show reMarkable setup
        output = capsys.readouterr().out
        assert "reMarkable Setup" in output


# ---------------------------------------------------------------------------
# Agent: mode-aware system prompt
# ---------------------------------------------------------------------------

class TestAgentModeAwareness:
    """Agent system prompt adapts to reading surface."""

    def _mock_state(self):
        state = MagicMock()
        state.documents_with_status.return_value = []
        state.documents_processed_since.return_value = []
        state.promoted_papers = []
        return state

    def test_system_prompt_remarkable_mode(self, monkeypatch):
        from distillate import config
        monkeypatch.setattr(config, "READING_SOURCE", "remarkable")

        from distillate.agent import _build_system_prompt
        prompt = _build_system_prompt(self._mock_state())
        assert "reMarkable" in prompt

    def test_system_prompt_zotero_mode(self, monkeypatch):
        from distillate import config
        monkeypatch.setattr(config, "READING_SOURCE", "zotero")

        from distillate.agent import _build_system_prompt
        prompt = _build_system_prompt(self._mock_state())
        assert "Zotero app" in prompt
        assert "reMarkable" not in prompt


# ---------------------------------------------------------------------------
# Tools: mode-aware queue queries
# ---------------------------------------------------------------------------

class TestToolsModeAwareness:
    """Tool implementations query the correct status in each mode."""

    def _make_state(self, docs):
        """Build a mock state with given documents."""
        state = MagicMock()
        state.promoted_papers = []

        def documents_with_status(status):
            return [d for d in docs.values() if d.get("status") == status]

        def documents_processed_since(since):
            return [d for d in docs.values() if d.get("status") == "processed"]

        def index_of(key):
            return list(docs.keys()).index(key) + 1 if key in docs else 0

        state.documents_with_status = documents_with_status
        state.documents_processed_since = documents_processed_since
        state.index_of = index_of
        return state

    def test_get_queue_remarkable_mode(self, monkeypatch):
        from distillate import config
        from distillate.tools import get_queue

        monkeypatch.setattr(config, "READING_SOURCE", "remarkable")

        state = self._make_state({
            "K1": {"status": "on_remarkable", "title": "Paper A", "zotero_item_key": "K1", "metadata": {}},
            "K2": {"status": "tracked", "title": "Paper B", "zotero_item_key": "K2", "metadata": {}},
        })
        result = get_queue(state=state)
        assert result["total"] == 1
        assert result["queue"][0]["title"] == "Paper A"

    def test_get_queue_zotero_mode(self, monkeypatch):
        from distillate import config
        from distillate.tools import get_queue

        monkeypatch.setattr(config, "READING_SOURCE", "zotero")

        state = self._make_state({
            "K1": {"status": "on_remarkable", "title": "Paper A", "zotero_item_key": "K1", "metadata": {}},
            "K2": {"status": "tracked", "title": "Paper B", "zotero_item_key": "K2", "metadata": {}},
        })
        result = get_queue(state=state)
        assert result["total"] == 1
        assert result["queue"][0]["title"] == "Paper B"

    def test_get_reading_stats_zotero_mode(self, monkeypatch):
        from distillate import config
        from distillate.tools import get_reading_stats

        monkeypatch.setattr(config, "READING_SOURCE", "zotero")

        state = self._make_state({
            "K1": {"status": "tracked", "title": "Tracked", "metadata": {}},
            "K2": {"status": "on_remarkable", "title": "On RM", "metadata": {}},
        })
        result = get_reading_stats(state=state)
        assert result["queue_size"] == 1  # only "tracked" counted
