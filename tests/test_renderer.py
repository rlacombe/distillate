"""Tests for renderer: cross-page highlight merging and OCR cleanup."""


class TestMergeCrossPage:
    """Tests for _merge_cross_page() in renderer."""

    def test_merges_across_page_break(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["This sentence continues on the next"],
            2: ["page with more text.", "A separate highlight."],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["This sentence continues on the next page with more text."]
        assert result[2] == ["A separate highlight."]

    def test_no_merge_when_punctuation_ends(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["A complete sentence."],
            2: ["Another complete sentence."],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["A complete sentence."]
        assert result[2] == ["Another complete sentence."]

    def test_no_merge_when_next_starts_uppercase(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["Some text without punctuation"],
            2: ["The next page starts a new thought."],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["Some text without punctuation"]
        assert result[2] == ["The next page starts a new thought."]

    def test_no_merge_for_non_consecutive_pages(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["Text on page one"],
            3: ["text on page three"],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["Text on page one"]
        assert result[3] == ["text on page three"]

    def test_removes_empty_page_after_merge(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["Sentence continues"],
            2: ["on the next page."],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["Sentence continues on the next page."]
        assert 2 not in result

    def test_single_page_unchanged(self):
        from distillate.renderer import _merge_cross_page

        by_page = {3: ["Just one page of highlights."]}
        result = _merge_cross_page(by_page)
        assert result == {3: ["Just one page of highlights."]}

    def test_empty_dict_unchanged(self):
        from distillate.renderer import _merge_cross_page

        assert _merge_cross_page({}) == {}

    def test_chain_merge_across_three_pages(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["Start of a long"],
            2: ["passage that continues"],
            3: ["across three pages."],
        }
        result = _merge_cross_page(by_page)
        # Page 1→2 merges: "Start of a long passage that continues"
        # Page 2 is deleted. Then loop tries 2→3 but page 2 is gone, so skipped.
        # Page 3 remains separate.
        assert result[1] == ["Start of a long passage that continues"]
        assert 2 not in result
        assert result[3] == ["across three pages."]

    def test_question_mark_prevents_merge(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["Is this a question?"],
            2: ["yes it is."],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["Is this a question?"]
        assert result[2] == ["yes it is."]

    def test_colon_prevents_merge(self):
        from distillate.renderer import _merge_cross_page

        by_page = {
            1: ["The methods include:"],
            2: ["several approaches."],
        }
        result = _merge_cross_page(by_page)
        assert result[1] == ["The methods include:"]
        assert result[2] == ["several approaches."]


class TestCleanHighlightText:
    """Tests for _clean_highlight_text() OCR cleanup."""

    def test_strips_citation_markers(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("some text(p1) here") == "some text here"
        assert _clean_highlight_text("text(23) more") == "text more"

    def test_fixes_double_commas(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("a, , b") == "a, b"

    def test_fixes_missing_space_after_period(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("end.Start") == "end. Start"

    def test_fixes_line_wrap_joins(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("operationsWe found") == "operations We found"

    def test_preserves_acronyms(self):
        from distillate.renderer import _clean_highlight_text

        # GenAI should NOT be split (only 1 lowercase before uppercase)
        assert _clean_highlight_text("using GenAI tools") == "using GenAI tools"

    def test_collapses_multiple_spaces(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("too   many  spaces") == "too many spaces"
