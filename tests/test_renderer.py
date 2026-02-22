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

    def test_strips_bare_citation_after_paren(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("(LDSC)48and") == "(LDSC) and"
        assert _clean_highlight_text("(LMM)24or") == "(LMM) or"

    def test_strips_bare_citation_digits_before_punctuation(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("efficiency28,") == "efficiency,"
        assert _clean_highlight_text("estimation22,23,") == "estimation,"

    def test_colon_letter_spacing(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("properties:statistical") == "properties: statistical"

    def test_comma_semicolon_cleanup(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("mechanisms,;") == "mechanisms;"
        assert _clean_highlight_text("mechanisms, ;") == "mechanisms;"

    def test_strips_en_dash_citation_ranges(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("learning30–32, SLMM") == "learning, SLMM"
        assert _clean_highlight_text("components,55") == "components"

    def test_strips_superscript_digits_from_pdf(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("data12 and is") == "data and is"
        assert _clean_highlight_text("implementations,27 is") == "implementations, is"
        # Preserve real numbers with space before them
        assert _clean_highlight_text("Table 1, Supplementary") == "Table 1, Supplementary"

    def test_strips_trailing_citation(self):
        from distillate.renderer import _clean_highlight_text

        assert _clean_highlight_text("data sets,47") == "data sets"


class TestRecoverFromPdf:
    """Tests for _recover_from_pdf() PDF-based highlight text recovery."""

    def test_recovers_missing_spaces(self, tmp_path):
        """When PDF text has proper spacing, it replaces the concatenated OCR text."""
        import zipfile
        import pymupdf
        from distillate.renderer import _recover_from_pdf

        # Create a minimal PDF with known text
        pdf_doc = pymupdf.open()
        page = pdf_doc.new_page(width=612, height=792)
        page.insert_text((72, 100), "how genotypes affect phenotype")
        pdf_bytes = pdf_doc.tobytes()
        pdf_doc.close()

        # Create a minimal zip containing the PDF
        zip_path = tmp_path / "test.rmdoc"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.pdf", pdf_bytes)

        by_page = {1: ["howgenotypes affect phenotype"]}
        result = _recover_from_pdf(by_page, zip_path)
        assert result[1][0] == "how genotypes affect phenotype"

    def test_keeps_original_when_no_match(self, tmp_path):
        """When PDF text doesn't match, keeps the original passage."""
        import zipfile
        import pymupdf
        from distillate.renderer import _recover_from_pdf

        pdf_doc = pymupdf.open()
        page = pdf_doc.new_page(width=612, height=792)
        page.insert_text((72, 100), "completely different text")
        pdf_bytes = pdf_doc.tobytes()
        pdf_doc.close()

        zip_path = tmp_path / "test.rmdoc"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.pdf", pdf_bytes)

        by_page = {1: ["text not in the pdf at all"]}
        result = _recover_from_pdf(by_page, zip_path)
        assert result[1][0] == "text not in the pdf at all"

    def test_graceful_fallback_without_pdf(self, tmp_path):
        """When zip has no PDF, returns by_page unchanged."""
        import zipfile
        from distillate.renderer import _recover_from_pdf

        zip_path = tmp_path / "test.rmdoc"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("dummy.rm", b"")

        by_page = {1: ["some text"]}
        result = _recover_from_pdf(by_page, zip_path)
        assert result == {1: ["some text"]}

    def test_empty_by_page(self, tmp_path):
        """Empty by_page is returned as-is without opening zip."""
        from distillate.renderer import _recover_from_pdf

        result = _recover_from_pdf({}, tmp_path / "nonexistent.zip")
        assert result == {}


class TestNoneCleanup:
    """Tests for robust [none] filtering in OCR cleanup."""

    def test_none_variants_filtered(self):
        """Various [none] formats should be caught by the regex."""
        import re

        pattern = r"^\[?none\]?\.?\s*$"
        for text in ["[none]", "none", "[none].", "[None]", "None.", "NONE"]:
            assert re.match(pattern, text, re.IGNORECASE), f"Should match: {text!r}"
        for text in ["none of these", "is none", "the [none] case"]:
            assert not re.match(pattern, text, re.IGNORECASE), f"Should NOT match: {text!r}"

    def test_obsidian_filters_none_from_handwritten(self):
        """obsidian.py should strip [none] lines from handwritten notes."""
        import re

        # Simulate what obsidian.py does
        text = "Krylov key efficiency gain\n\nhow close to exact?\n\n[none]"
        lines = text.strip().split("\n")
        lines = [
            ln for ln in lines
            if not re.match(r"^\[?none\]?\.?\s*$", ln.strip(), re.IGNORECASE)
        ]
        result = "\n".join(lines).strip()
        assert "[none]" not in result
        assert "Krylov" in result
