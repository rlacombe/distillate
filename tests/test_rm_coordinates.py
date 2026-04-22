# Covers: distillate/integrations/remarkable/renderer.py

import pytest

from rmscene import scene_items as si


class TestIsPaperProCoords:
    """_is_paper_pro_coords() should detect Paper Pro coordinate space."""

    def test_classic_rm_coordinates(self):
        from distillate.integrations.remarkable.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=100, y=200, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=500, y=800, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is False

    def test_classic_rm_near_viewport_edge(self):
        """Strokes near the classic viewport boundary should NOT trigger Pro."""
        from distillate.integrations.remarkable.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=-50, y=1850, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=1400, y=1870, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is False

    def test_classic_rm_scrolled_below_page(self):
        """Classic RM user scrolled below page (y~2000) should NOT trigger."""
        from distillate.integrations.remarkable.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=300, y=1950, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=600, y=2050, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is False

    def test_paper_pro_negative_x(self):
        from distillate.integrations.remarkable.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=-742, y=200, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=-500, y=800, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is True

    def test_paper_pro_high_y(self):
        from distillate.integrations.remarkable.renderer import _is_paper_pro_coords

        line = si.Line(
            color=si.PenColor.BLACK, tool=si.Pen.BALLPOINT_1,
            points=[
                si.Point(x=100, y=200, speed=0, direction=0, width=2, pressure=50),
                si.Point(x=500, y=2400, speed=0, direction=0, width=2, pressure=50),
            ],
            thickness_scale=1.0, starting_length=0,
        )
        assert _is_paper_pro_coords({0: [line]}) is True

    def test_empty_ink(self):
        from distillate.integrations.remarkable.renderer import _is_paper_pro_coords

        assert _is_paper_pro_coords({}) is False


class TestRmToPdfMapping:
    """_rm_to_pdf_mapping() should compute correct coordinate mapping."""

    def test_classic_letter_y_offset(self):
        """Classic RM: US Letter fills width, has vertical centering offset."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=False)
        assert abs(x_off) < 0.1
        assert 25 < y_off < 30
        assert abs(rm_scale - 1404.0 / 612.0) < 0.01

    def test_classic_a4_x_offset(self):
        """Classic RM: A4 fills height, has horizontal centering offset."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(595.0, 842.0, paper_pro=False)
        assert abs(y_off) < 0.1
        assert x_off > 30
        assert abs(rm_scale - 1872.0 / 842.0) < 0.01

    def test_classic_maps_center_to_center(self):
        """Classic RM: center of viewport maps to center of page."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=False)
        # RM viewport center = (702, 936)
        pdf_x = (702 - x_off) / rm_scale
        pdf_y = (936 - y_off) / rm_scale
        assert abs(pdf_x - 306) < 1
        assert abs(pdf_y - 396) < 1

    def test_paper_pro_scale(self):
        """Paper Pro uses 227 DPI native coordinates."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        assert abs(rm_scale - 227.0 / 72.0) < 0.01
        assert abs(x_off - (-612.0 * rm_scale / 2)) < 0.1
        assert abs(y_off) < 0.1

    def test_paper_pro_maps_origin_to_center_x(self):
        """Paper Pro: rm_x=0 maps to pdf_x=pdf_w/2 (PDF centered at x=0)."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        pdf_x = (0 - x_off) / rm_scale
        assert abs(pdf_x - 306) < 2  # center of 612pt page

    def test_paper_pro_coordinate_accuracy(self):
        """Paper Pro mapping should place ink within 2pt of correct position.

        Calibrated from 54 highlight positions on an actual Paper Pro document.
        """
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        calibration = [
            (232, 73.2), (333, 105.3), (362, 114.4),
            (1082, 342.9), (1324, 419.7), (1454, 461.0),
        ]
        for rm_y, expected_pdf_y in calibration:
            pdf_y = (rm_y - y_off) / rm_scale
            assert abs(pdf_y - expected_pdf_y) < 2.0, (
                f"rm_y={rm_y}: got {pdf_y:.1f}, expected {expected_pdf_y}"
            )

    def test_paper_pro_a4(self):
        """Paper Pro mapping works for A4 pages too."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        rm_scale, x_off, y_off = _rm_to_pdf_mapping(595.0, 842.0, paper_pro=True)
        assert abs(rm_scale - 227.0 / 72.0) < 0.01
        assert abs(x_off - (-595.0 * rm_scale / 2)) < 0.1

    def test_classic_and_pro_produce_different_results(self):
        """The two mappings give meaningfully different positions."""
        from distillate.integrations.remarkable.renderer import _rm_to_pdf_mapping

        classic = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=False)
        pro = _rm_to_pdf_mapping(612.0, 792.0, paper_pro=True)
        assert classic[0] < 2.5  # ~2.29
        assert pro[0] > 3.0     # ~3.15
