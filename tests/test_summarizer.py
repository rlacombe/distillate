"""Tests for distillate.summarizer — fallback summary logic."""


class TestFallbackRead:
    def test_prefers_hf_summary_over_abstract(self):
        from distillate.summarizer import _fallback_read
        summary, one_liner = _fallback_read(
            "Test Paper",
            abstract="Long abstract. Second sentence. Third sentence.",
            key_learnings=["A learning"],
            hf_summary="HF generated summary.",
        )
        assert summary == "HF generated summary."
        assert one_liner == "HF generated summary."

    def test_falls_back_to_abstract_without_hf(self):
        from distillate.summarizer import _fallback_read
        summary, one_liner = _fallback_read(
            "Test Paper",
            abstract="First sentence. Second sentence. Third sentence.",
            key_learnings=None,
            hf_summary="",
        )
        assert "First sentence" in summary
        assert one_liner == "First sentence."

    def test_falls_back_to_key_learnings(self):
        from distillate.summarizer import _fallback_read
        summary, one_liner = _fallback_read(
            "Test Paper",
            abstract="",
            key_learnings=["Main insight here"],
            hf_summary="",
        )
        assert summary == "Main insight here"
        assert one_liner == "Main insight here"

    def test_falls_back_to_pending_marker(self):
        from distillate.summarizer import _fallback_read, _PENDING_SUMMARY
        summary, one_liner = _fallback_read(
            "Test Paper", abstract="", key_learnings=None, hf_summary="",
        )
        assert summary == _PENDING_SUMMARY
        assert one_liner == _PENDING_SUMMARY

    def test_hf_summary_default_param(self):
        """hf_summary defaults to empty string (backwards compat)."""
        from distillate.summarizer import _fallback_read
        summary, _ = _fallback_read("Test", abstract="Sentence one.", key_learnings=None)
        assert "Sentence one" in summary
