"""Pure-function tests for SAR fetch planning and Process API request building.

Network paths (token fetch, tile fetch, catalog search) are intentionally out of
scope — they belong in a separate integration suite.
"""

import io
from datetime import datetime, timezone
from math import ceil

import numpy as np
import pytest
from PIL import Image

from app.sar import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    DB_MIN,
    EVALSCRIPT,
    MAX_TILE_PX,
    SarChip,
    SarScene,
    _retry_delay_seconds,
    build_process_request,
    chip_overview_png,
    estimate_pu,
    plan_fetch_grid,
)

# 0.6° x 0.6° box at 25°N — representative ROI-sized fetch area
TEST_BBOX = (56.50, 25.00, 57.10, 25.60)


def make_scene(sensed_at: datetime | None = None) -> SarScene:
    return SarScene(
        id="abc-123",
        name="S1A_IW_GRDH_1SDV_20260701T023000_20260701T023025_012345_016D9A_ABCD.SAFE",
        sensed_at=sensed_at or datetime(2026, 7, 1, 2, 30, 0, tzinfo=timezone.utc),
        footprint_wkt=None,
        platform="S1A",
        is_cog=False,
    )


class TestPlanFetchGrid:
    def test_roi_sized_bbox_needs_3x3_tiles(self):
        grid = plan_fetch_grid(TEST_BBOX)
        assert len(grid.tiles) == 9
        assert all(t.width <= MAX_TILE_PX and t.height <= MAX_TILE_PX for t in grid.tiles)

    def test_tiles_cover_pixel_grid_exactly(self):
        grid = plan_fetch_grid(TEST_BBOX)
        covered = [[False] * grid.width for _ in range(grid.height)]
        for t in grid.tiles:
            for y in range(t.y_off, t.y_off + t.height):
                for x in range(t.x_off, t.x_off + t.width):
                    assert not covered[y][x], "tiles overlap"
                    covered[y][x] = True
        assert all(all(row) for row in covered), "grid not fully covered"

    def test_tile_bboxes_tile_the_roi(self):
        min_lon, min_lat, max_lon, max_lat = TEST_BBOX
        grid = plan_fetch_grid(TEST_BBOX)
        assert min(t.bbox[0] for t in grid.tiles) == pytest.approx(min_lon)
        assert min(t.bbox[1] for t in grid.tiles) == pytest.approx(min_lat)
        assert max(t.bbox[2] for t in grid.tiles) == pytest.approx(max_lon)
        assert max(t.bbox[3] for t in grid.tiles) == pytest.approx(max_lat)

    def test_row_zero_is_north_edge(self):
        _, _, _, max_lat = TEST_BBOX
        grid = plan_fetch_grid(TEST_BBOX)
        for t in grid.tiles:
            if t.y_off == 0:
                assert t.bbox[3] == pytest.approx(max_lat)

    def test_adjacent_tiles_share_edges(self):
        grid = plan_fetch_grid(TEST_BBOX)
        by_offset = {(t.x_off, t.y_off): t for t in grid.tiles}
        for t in grid.tiles:
            right = by_offset.get((t.x_off + t.width, t.y_off))
            if right:
                assert t.bbox[2] == pytest.approx(right.bbox[0])
            below = by_offset.get((t.x_off, t.y_off + t.height))
            if below:
                assert t.bbox[1] == pytest.approx(below.bbox[3])

    def test_small_bbox_is_single_tile(self):
        grid = plan_fetch_grid((56.50, 25.00, 56.52, 25.02))
        assert len(grid.tiles) == 1
        tile = grid.tiles[0]
        assert (tile.width, tile.height) == (grid.width, grid.height)
        assert tile.bbox == pytest.approx((56.50, 25.00, 56.52, 25.02))

    def test_ten_meter_pixels_at_25n(self):
        # 0.6 deg of latitude at 10 m/px ≈ 6679 px
        grid = plan_fetch_grid(TEST_BBOX)
        assert grid.height == pytest.approx(6679, abs=2)
        # longitude shrinks by cos(25.3 deg)
        assert grid.width < grid.height


class TestEstimatePu:
    def test_matches_area_bands_ortho_formula(self):
        grid = plan_fetch_grid(TEST_BBOX)
        expected = grid.width * grid.height / (512 * 512) * (1 / 3) * 2
        assert estimate_pu(grid) == pytest.approx(expected)

    def test_singapore_strait_is_about_55_pu(self):
        # Smallest live ROI — the demo box, and the cheapest to fetch.
        grid = plan_fetch_grid((103.55, 1.03, 104.10, 1.35))
        assert estimate_pu(grid) == pytest.approx(55, abs=3)


class TestBuildProcessRequest:
    def test_time_range_brackets_single_pass(self):
        scene = make_scene(datetime(2026, 7, 1, 2, 30, 0, tzinfo=timezone.utc))
        body = build_process_request(scene, TEST_BBOX, 100, 100)
        time_range = body["input"]["data"][0]["dataFilter"]["timeRange"]
        assert time_range["from"] == "2026-07-01T02:29:00Z"
        assert time_range["to"] == "2026-07-01T02:40:00Z"

    def test_s1_grd_filters_and_processing(self):
        body = build_process_request(make_scene(), TEST_BBOX, 100, 100)
        data = body["input"]["data"][0]
        assert data["type"] == "sentinel-1-grd"
        assert data["dataFilter"]["acquisitionMode"] == "IW"
        assert data["dataFilter"]["polarization"] == "DV"
        assert data["dataFilter"]["resolution"] == "HIGH"
        assert data["dataFilter"]["mosaickingOrder"] == "mostRecent"
        assert data["processing"]["orthorectify"] is True
        assert data["processing"]["backCoeff"] == "SIGMA0_ELLIPSOID"

    def test_output_shape_and_format(self):
        body = build_process_request(make_scene(), TEST_BBOX, 2400, 1200)
        assert body["output"]["width"] == 2400
        assert body["output"]["height"] == 1200
        responses = body["output"]["responses"]
        assert len(responses) == 1
        assert responses[0]["format"]["type"] == "image/png"

    def test_bbox_passthrough(self):
        bbox = (56.5, 25.0, 56.7, 25.2)
        body = build_process_request(make_scene(), bbox, 100, 100)
        assert body["input"]["bounds"]["bbox"] == list(bbox)

    def test_evalscript_scales_vv_db(self):
        assert "VV" in EVALSCRIPT
        assert str(DB_MIN) in EVALSCRIPT
        assert body_uses_evalscript()

    def test_no_speckle_filter_by_default(self):
        body = build_process_request(make_scene(), TEST_BBOX, 100, 100)
        assert "speckleFilter" not in body["input"]["data"][0]["processing"]

    def test_speckle_filter_injected_when_given(self):
        speckle = {"type": "LEE", "windowSizeX": 3, "windowSizeY": 3}
        body = build_process_request(
            make_scene(), TEST_BBOX, 100, 100, speckle_filter=speckle
        )
        assert body["input"]["data"][0]["processing"]["speckleFilter"] == speckle

    def test_evalscript_override_passthrough(self):
        body = build_process_request(
            make_scene(), TEST_BBOX, 100, 100, evalscript="//custom"
        )
        assert body["evalscript"] == "//custom"


class TestRetryDelaySeconds:
    def test_honors_retry_after_header(self):
        assert _retry_delay_seconds(0, "5") == 5.0

    def test_retry_after_overrides_attempt_number(self):
        assert _retry_delay_seconds(3, "5") == 5.0

    def test_falls_back_to_exponential_backoff_without_header(self):
        assert _retry_delay_seconds(0, None) == BACKOFF_BASE_SECONDS
        assert _retry_delay_seconds(1, None) == BACKOFF_BASE_SECONDS * 2
        assert _retry_delay_seconds(2, None) == BACKOFF_BASE_SECONDS * 4

    def test_exponential_backoff_caps_at_max(self):
        assert _retry_delay_seconds(10, None) == BACKOFF_MAX_SECONDS

    def test_unparseable_retry_after_falls_back_to_exponential(self):
        assert _retry_delay_seconds(0, "not-a-number") == BACKOFF_BASE_SECONDS


def body_uses_evalscript() -> bool:
    body = build_process_request(make_scene(), TEST_BBOX, 100, 100)
    return body["evalscript"] == EVALSCRIPT


class TestChipOverviewPng:
    @staticmethod
    def _chip(pixels):
        return SarChip(
            pixels=pixels, bbox=TEST_BBOX, width=pixels.shape[1], height=pixels.shape[0]
        )

    def test_bounds_long_edge_to_max_px(self):
        chip = self._chip(np.zeros((1000, 5000), dtype=np.uint8))
        image = Image.open(io.BytesIO(chip_overview_png(chip, max_px=1000)))
        assert max(image.size) <= 1000

    def test_small_chip_is_left_alone(self):
        chip = self._chip(np.zeros((40, 60), dtype=np.uint8))
        image = Image.open(io.BytesIO(chip_overview_png(chip, max_px=2048)))
        assert image.size == (60, 40)

    def test_isolated_bright_pixel_survives_downsampling(self):
        """A ship is a few bright pixels on dark water; averaging would erase it."""
        pixels = np.zeros((800, 800), dtype=np.uint8)
        pixels[417, 233] = 255
        chip = self._chip(pixels)
        image = Image.open(io.BytesIO(chip_overview_png(chip, max_px=100)))
        assert np.asarray(image).max() == 255

    def test_pads_rather_than_crops_so_extent_is_preserved(self):
        # 805 is not a multiple of the reduction factor (9); cropping the
        # remainder would shrink the covered area and misregister the overlay
        # against imaged_bbox.
        chip = self._chip(np.zeros((805, 805), dtype=np.uint8))
        image = Image.open(io.BytesIO(chip_overview_png(chip, max_px=100)))
        assert 805 % 9 != 0, "pick a size that actually exercises padding"
        assert image.size == (ceil(805 / 9), ceil(805 / 9))
