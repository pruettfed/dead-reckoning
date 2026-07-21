"""Pure-function coverage for the land mask's geometry helpers.

The masking itself is a PostGIS query and is not exercised here — this suite
runs without a database (see CLAUDE.md).
"""

import pytest

from app.rois import ROIS
from scripts.load_land import bboxes_overlap, clip_multipolygon_wkt

BOX = (103.0, 1.0, 104.0, 2.0)


@pytest.mark.parametrize(
    "other,expected",
    [
        ((103.4, 1.4, 103.6, 1.6), True),   # fully inside
        ((102.0, 0.0, 105.0, 3.0), True),   # fully surrounding
        ((103.5, 1.5, 105.0, 3.0), True),   # partial overlap
        ((104.0, 1.0, 105.0, 2.0), True),   # edge-touching counts; SQL drops it
        ((104.5, 1.0, 105.0, 2.0), False),  # east
        ((101.0, 1.0, 102.0, 2.0), False),  # west
        ((103.0, 2.5, 104.0, 3.0), False),  # north
        ((103.0, -1.0, 104.0, 0.5), False),  # south
    ],
)
def test_bboxes_overlap(other, expected):
    assert bboxes_overlap(BOX, other) is expected
    assert bboxes_overlap(other, BOX) is expected


def test_clip_multipolygon_wkt_is_closed_and_counted():
    wkt = clip_multipolygon_wkt([BOX, (10.0, 20.0, 11.0, 21.0)])
    assert wkt.startswith("MULTIPOLYGON(((")
    assert wkt.count("((") == 2  # one ring per box
    # Each ring must close back on its first vertex or PostGIS rejects it.
    assert "103.0 1.0,104.0 1.0,104.0 2.0,103.0 2.0,103.0 1.0" in wkt


def test_clip_covers_every_roi_sar_bbox():
    """The clip region is what survives the load — no ROI may fall outside it."""
    wkt = clip_multipolygon_wkt([roi.sar_bbox for roi in ROIS.values()])
    assert wkt.count("((") == len(ROIS)
