# Covers: distillate/integrations/remarkable/renderer.py

import io
import json
import zipfile


class TestParsePageIds:
    def _make_zip(self, content_data: dict | None = None, rm_files: list[str] | None = None):
        """Create an in-memory zip with optional .content and .rm files."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            if content_data is not None:
                zf.writestr("doc.content", json.dumps(content_data))
            for name in (rm_files or []):
                zf.writestr(name, b"")
        buf.seek(0)
        return buf

    def test_cpages_format(self):
        from distillate.integrations.remarkable.renderer import _parse_page_ids
        content = {"cPages": {"pages": [{"id": "aaa"}, {"id": "bbb"}]}}
        buf = self._make_zip(content)
        with zipfile.ZipFile(buf) as zf:
            ids = _parse_page_ids(zf)
        assert ids == ["aaa", "bbb"]

    def test_legacy_format(self):
        from distillate.integrations.remarkable.renderer import _parse_page_ids
        content = {"pages": ["p1", "p2", "p3"]}
        buf = self._make_zip(content)
        with zipfile.ZipFile(buf) as zf:
            ids = _parse_page_ids(zf)
        assert ids == ["p1", "p2", "p3"]

    def test_no_content_file(self):
        from distillate.integrations.remarkable.renderer import _parse_page_ids
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "no content")
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            ids = _parse_page_ids(zf)
        assert ids == []

    def test_empty_pages(self):
        from distillate.integrations.remarkable.renderer import _parse_page_ids
        content = {"cPages": {"pages": []}, "pages": []}
        buf = self._make_zip(content)
        with zipfile.ZipFile(buf) as zf:
            ids = _parse_page_ids(zf)
        assert ids == []

    def test_mixed_dict_and_string_pages(self):
        from distillate.integrations.remarkable.renderer import _parse_page_ids
        content = {"cPages": {"pages": [{"id": "aaa"}, "plain-id"]}}
        buf = self._make_zip(content)
        with zipfile.ZipFile(buf) as zf:
            ids = _parse_page_ids(zf)
        assert ids == ["aaa", "plain-id"]
