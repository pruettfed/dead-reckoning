"""Pure-function tests for the xView3 chipper.

Loaded by path (it lives in ml/, outside the backend package) so that
`cd backend && pytest` stays the single test command. rasterio and PIL are only
touched inside chip_scene, which these tests do not call.
"""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from app.sar import DB_MAX, DB_MIN

_SPEC = importlib.util.spec_from_file_location(
    "prepare_xview3", Path(__file__).resolve().parents[2] / "ml" / "prepare_xview3.py"
)
prepare_xview3 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prepare_xview3)


class TestRenderParity:
    def test_db_window_matches_production_evalscript(self):
        """The whole point of xView3 is training on production's rendering. If
        sar.py's window moves and this doesn't, that parity is silently lost."""
        assert (prepare_xview3.DB_MIN, prepare_xview3.DB_MAX) == (DB_MIN, DB_MAX)

    def test_endpoints_and_midpoint(self):
        window = np.array([[DB_MIN, DB_MAX, (DB_MIN + DB_MAX) / 2]], dtype=np.float32)
        assert prepare_xview3.render_db(window).tolist() == [[0, 255, 127]]

    def test_clamps_outside_the_window(self):
        window = np.array([[DB_MIN - 30, DB_MAX + 30]], dtype=np.float32)
        assert prepare_xview3.render_db(window).tolist() == [[0, 255]]

    def test_nan_collapses_to_zero_like_the_datamask_branch(self):
        window = np.array([[np.nan, DB_MAX]], dtype=np.float32)
        assert prepare_xview3.render_db(window).tolist() == [[0, 255]]

    def test_is_monotonic(self):
        window = np.linspace(DB_MIN, DB_MAX, 50, dtype=np.float32).reshape(1, -1)
        rendered = prepare_xview3.render_db(window)[0]
        assert np.all(np.diff(rendered.astype(int)) >= 0)


class TestSynthesiseBox:
    def test_length_sets_the_side_at_ten_metres_per_pixel(self):
        x1, y1, x2, y2 = prepare_xview3.synthesise_box(100.0, 200.0, 80.0)
        assert (x2 - x1, y2 - y1) == (8.0, 8.0)
        assert ((x1 + x2) / 2, (y1 + y2) / 2) == (200.0, 100.0)

    def test_unknown_length_falls_back_to_the_default(self):
        x1, _, x2, _ = prepare_xview3.synthesise_box(0.0, 0.0, math.nan)
        assert x2 - x1 == prepare_xview3.DEFAULT_BOX_PX

    def test_tiny_vessels_get_a_floor(self):
        """A 10 m skiff is 1 px at 10 m/px — too small for YOLO to fit."""
        x1, _, x2, _ = prepare_xview3.synthesise_box(0.0, 0.0, 10.0)
        assert x2 - x1 == prepare_xview3.MIN_BOX_PX


class TestParseLabel:
    def _record(self, **overrides):
        record = {
            "detect_scene_row": "100",
            "detect_scene_column": "200",
            "top": "", "left": "", "bottom": "", "right": "",
            "vessel_length_m": "80",
            "is_vessel": "True",
            "confidence": "HIGH",
        }
        record.update(overrides)
        return record

    def test_supplied_box_wins_over_synthesis(self):
        label = prepare_xview3.parse_label(
            self._record(top="90", left="190", bottom="110", right="215")
        )
        assert label.box == (190.0, 90.0, 215.0, 110.0)

    def test_falls_back_to_synthesis_when_box_is_partial(self):
        """A row with only some corners present must not produce a nan box."""
        label = prepare_xview3.parse_label(self._record(top="90", left="190"))
        assert not any(math.isnan(v) for v in label.box)
        assert label.box == prepare_xview3.synthesise_box(100.0, 200.0, 80.0)

    @pytest.mark.parametrize(
        "overrides,expected",
        [
            ({}, True),
            ({"confidence": "MEDIUM"}, True),
            ({"confidence": "LOW"}, False),
            ({"is_vessel": "False"}, False),   # platform, wind turbine
            ({"is_vessel": ""}, False),        # unknown object
        ],
    )
    def test_keep_filter(self, overrides, expected):
        assert prepare_xview3.parse_label(self._record(**overrides)).keep is expected

    def test_missing_length_and_box_still_parses(self):
        label = prepare_xview3.parse_label(self._record(vessel_length_m=""))
        assert label.box[2] - label.box[0] == prepare_xview3.DEFAULT_BOX_PX


def _label(row, col, keep=True, side=8.0):
    half = side / 2
    return prepare_xview3.Label(
        row=row, col=col, box=(col - half, row - half, col + half, row + half), keep=keep
    )


class TestLabelsInWindow:
    def test_only_labels_inside_the_window_are_emitted(self):
        labels = [_label(100, 100), _label(5000, 5000)]
        lines, rejected = prepare_xview3.labels_in_window(labels, 0, 0, 800)
        assert not rejected and len(lines) == 1

    def test_coordinates_are_relative_to_the_window(self):
        lines, _ = prepare_xview3.labels_in_window([_label(900, 1000)], 800, 800, 800)
        _, cx, cy, w, h = lines[0].split()
        assert (float(cx), float(cy)) == pytest.approx((200 / 800, 100 / 800))
        assert (float(w), float(h)) == pytest.approx((8 / 800, 8 / 800))

    def test_a_filtered_label_rejects_the_whole_chip(self):
        """A non-vessel's pixels hold a bright target with no box — training on it
        as background teaches suppression of exactly what we want found."""
        labels = [_label(100, 100), _label(200, 200, keep=False)]
        lines, rejected = prepare_xview3.labels_in_window(labels, 0, 0, 800)
        assert rejected and lines == []

    def test_boxes_are_clipped_to_the_window(self):
        lines, _ = prepare_xview3.labels_in_window([_label(2, 2, side=20.0)], 0, 0, 800)
        _, cx, cy, w, h = (float(v) for v in lines[0].split())
        assert cx - w / 2 == pytest.approx(0.0)
        assert cy - h / 2 == pytest.approx(0.0)

    def test_empty_window_yields_a_background_chip(self):
        assert prepare_xview3.labels_in_window([], 0, 0, 800) == ([], False)


class TestOffsets:
    def test_last_offset_is_flush_with_the_edge(self):
        offsets = prepare_xview3._offsets(2000, 800)
        assert offsets[-1] == 1200
        assert all(o + 800 <= 2000 for o in offsets)

    def test_exact_multiple_has_no_duplicate(self):
        assert prepare_xview3._offsets(1600, 800) == [0, 800]

    def test_smaller_than_a_chip(self):
        assert prepare_xview3._offsets(500, 800) == [0]

    def test_grid_covers_every_pixel(self):
        covered = np.zeros((1000, 1700), dtype=bool)
        for x, y in prepare_xview3.iter_windows(1700, 1000, 800):
            covered[y : y + 800, x : x + 800] = True
        assert covered.all()


class TestLoadLabels:
    def test_groups_by_scene_and_drops_locationless_rows(self, tmp_path):
        csv_path = tmp_path / "validation.csv"
        csv_path.write_text(
            "scene_id,detect_scene_row,detect_scene_column,top,left,bottom,right,"
            "vessel_length_m,is_vessel,confidence\n"
            "sceneA,100,200,,,,,80,True,HIGH\n"
            "sceneA,300,400,,,,,,True,MEDIUM\n"
            "sceneB,10,20,,,,,50,False,HIGH\n"
            "sceneB,,,,,,,50,True,HIGH\n"  # no pixel location → dropped
        )
        by_scene = prepare_xview3.load_labels(csv_path)
        assert sorted(by_scene) == ["sceneA", "sceneB"]
        assert len(by_scene["sceneA"]) == 2
        assert len(by_scene["sceneB"]) == 1
        assert by_scene["sceneB"][0].keep is False
