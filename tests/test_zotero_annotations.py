# Covers: distillate/zotero_client.py

import json
import pytest


# ---------------------------------------------------------------------------
# Annotation creation API format
# ---------------------------------------------------------------------------

class TestAnnotationCreation:
    def test_create_highlight_annotations_builds_correct_payload(self, monkeypatch):
        """Verify the annotation payload format matches Zotero API."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "12345")

        # Mock HTTP calls
        class MockResp:
            status_code = 200
            def json(self):
                return []

        class MockPostResp:
            status_code = 200
            def json(self):
                return {"successful": {"0": {"key": "ANN1"}}, "failed": {}}

        calls = []
        def mock_get(path, params=None, **kwargs):
            return MockResp()

        def mock_post(path, **kwargs):
            calls.append(kwargs.get("json", []))
            return MockPostResp()

        monkeypatch.setattr(zotero_client, "_get", mock_get)
        monkeypatch.setattr(zotero_client, "_post", mock_post)

        highlights = [{
            "text": "test highlight",
            "page_index": 0,
            "page_label": "1",
            "rects": [[10.0, 20.0, 100.0, 30.0]],
            "sort_index": "00000|000042|00750",
            "color": "#ffd400",
        }]

        keys = zotero_client.create_highlight_annotations("ATT1", highlights)
        assert keys == ["ANN1"]
        assert len(calls) == 1

        item = calls[0][0]
        assert item["itemType"] == "annotation"
        assert item["parentItem"] == "ATT1"
        assert item["annotationType"] == "highlight"
        assert item["annotationText"] == "test highlight"
        assert item["annotationPageLabel"] == "1"

        pos = json.loads(item["annotationPosition"])
        assert pos["pageIndex"] == 0
        assert pos["rects"] == [[10.0, 20.0, 100.0, 30.0]]

        assert {"tag": "distillate"} in item["tags"]

    def test_duplicate_prevention_deletes_existing(self, monkeypatch):
        """Existing distillate annotations are deleted before creating new ones."""
        from distillate import zotero_client, config

        monkeypatch.setattr(config, "ZOTERO_API_KEY", "fake")
        monkeypatch.setattr(config, "ZOTERO_USER_ID", "12345")

        deleted = []

        class MockGetResp:
            status_code = 200
            def json(self):
                return [
                    {"key": "OLD1", "version": 42, "data": {"tags": [{"tag": "distillate"}]}},
                    {"key": "OTHER", "version": 10, "data": {"tags": [{"tag": "manual"}]}},
                ]

        class MockPostResp:
            status_code = 200
            def json(self):
                return {"successful": {"0": {"key": "NEW1"}}, "failed": {}}

        class MockDelResp:
            status_code = 204

        def mock_get(path, params=None, **kwargs):
            return MockGetResp()

        def mock_post(path, **kwargs):
            return MockPostResp()

        def mock_delete(path, **kwargs):
            deleted.append(path)
            return MockDelResp()

        monkeypatch.setattr(zotero_client, "_get", mock_get)
        monkeypatch.setattr(zotero_client, "_post", mock_post)
        monkeypatch.setattr(zotero_client, "_delete", mock_delete)

        zotero_client.create_highlight_annotations("ATT1", [
            {"text": "x", "page_index": 0, "page_label": "1",
             "rects": [], "sort_index": "00000|000000|00000", "color": "#ffd400"},
        ])

        # Only OLD1 should be deleted (has distillate tag), not OTHER
        assert "/items/OLD1" in deleted
        assert "/items/OTHER" not in deleted
