"""Pure-function tests for detection tiling, geo-mapping, NMS, and buckets.

YoloDetector itself (torch) is out of scope — a fake detector exercises the
tiling/merge path exactly as the real one would.
"""

import numpy as np
import pytest

from app.detect import (
    bucket_confidence,
    iter_tiles,
    merge_detections,
    pixel_to_lonlat,
    run_detection,
)
from app.sar import SarChip


class TestIterTiles:
    def test_single_tile_when_chip_fits(self):
        assert iter_tiles(800, 800) == [(0, 0)]
        assert iter_tiles(500, 300) == [(0, 0)]

    def test_last_tile_flush_with_edge(self):
        offsets = iter_tiles(2000, 800)
        xs = sorted({x for x, _ in offsets})
        assert xs[-1] == 2000 - 800

    @pytest.mark.parametrize("width,height", [(2000, 1000), (801, 800), (6039, 6679)])
    def test_tiles_cover_every_pixel(self, width, height):
        covered_x = np.zeros(width, dtype=bool)
        covered_y = np.zeros(height, dtype=bool)
        for x, y in iter_tiles(width, height):
            covered_x[x:x + 800] = True
            covered_y[y:y + 800] = True
        assert covered_x.all() and covered_y.all()

    def test_consecutive_tiles_overlap(self):
        xs = sorted({x for x, _ in iter_tiles(2000, 800)})
        for a, b in zip(xs, xs[1:]):
            assert b - a <= 800 - 160


class TestPixelToLonlat:
    BBOX = (56.5, 25.0, 57.1, 25.6)

    def test_origin_is_northwest_corner(self):
        assert pixel_to_lonlat(0, 0, self.BBOX, 600, 600) == pytest.approx((56.5, 25.6))

    def test_far_corner_is_southeast(self):
        assert pixel_to_lonlat(600, 600, self.BBOX, 600, 600) == pytest.approx((57.1, 25.0))

    def test_center(self):
        assert pixel_to_lonlat(300, 300, self.BBOX, 600, 600) == pytest.approx((56.8, 25.3))


class TestMergeDetections:
    def test_identical_boxes_keep_highest_confidence(self):
        box_low = (10.0, 10.0, 30.0, 30.0, 0.6)
        box_high = (10.0, 10.0, 30.0, 30.0, 0.9)
        assert merge_detections([box_low, box_high]) == [box_high]

    def test_disjoint_boxes_all_kept(self):
        a = (0.0, 0.0, 10.0, 10.0, 0.9)
        b = (100.0, 100.0, 110.0, 110.0, 0.8)
        assert sorted(merge_detections([a, b]), key=lambda d: d[4]) == [b, a]

    def test_partial_overlap_below_threshold_kept(self):
        a = (0.0, 0.0, 40.0, 40.0, 0.9)
        b = (32.0, 32.0, 72.0, 72.0, 0.8)  # IoU ≈ 0.02, centres 450 m apart
        assert len(merge_detections([a, b])) == 2

    def test_sliver_at_tile_seam_merged(self):
        """A hull cut by a seam: one tile boxes the whole ship, one a sliver."""
        full = (0.0, 0.0, 30.0, 30.0, 0.50)
        sliver = (0.0, 0.0, 6.0, 30.0, 0.42)  # IoU 0.2 — survives an IoU-only gate
        assert merge_detections([full, sliver]) == [full]

    def test_split_hull_boxes_merged(self):
        """Bow and stern boxed separately: no overlap at all, so IoU cannot see it."""
        bow = (0.0, 0.0, 10.0, 20.0, 0.32)
        stern = (12.0, 0.0, 22.0, 20.0, 0.30)
        assert merge_detections([bow, stern]) == [bow]

    def test_chain_collapses_onto_highest_confidence(self):
        """Kharg 1035/1038/1040: three boxes on one hull, spanning 130 m."""
        boxes = [
            (0.0, 0.0, 20.0, 20.0, 0.32),
            (7.0, 0.0, 27.0, 20.0, 0.31),
            (13.0, 0.0, 33.0, 20.0, 0.27),
        ]
        assert merge_detections(boxes) == [boxes[0]]

    def test_neighbouring_vessels_kept(self):
        """550 m apart — the closest genuinely distinct pair measured in the DB."""
        a = (0.0, 0.0, 20.0, 20.0, 0.52)
        b = (55.0, 0.0, 75.0, 20.0, 0.38)
        assert len(merge_detections([a, b])) == 2


class TestBucketConfidence:
    @pytest.mark.parametrize(
        "conf,bucket",
        [(0.95, "high"), (0.6, "high"), (0.599, "medium"), (0.25, "medium"), (0.249, "low"), (0.1, "low")],
    )
    def test_boundaries(self, conf, bucket):
        assert bucket_confidence(conf) == bucket


class BrightSpotDetector:
    """Fake detector: reports one box around any bright region in the tile."""

    def detect_tile(self, tile: np.ndarray):
        ys, xs = np.nonzero(tile > 128)
        if len(xs) == 0:
            return []
        return [(float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1), 0.9)]


class TestRunDetection:
    def make_chip(self) -> SarChip:
        pixels = np.zeros((800, 2000), dtype=np.uint8)
        pixels[300:320, 700:720] = 255  # one "ship", visible in two overlapping tiles
        return SarChip(pixels=pixels, bbox=(0.0, 0.0, 2.0, 0.8), width=2000, height=800)

    def test_seam_duplicate_merged_to_single_detection(self):
        detections = run_detection(self.make_chip(), BrightSpotDetector())
        assert len(detections) == 1

    def test_hull_cut_by_seam_merged_to_single_detection(self):
        """Ship straddling the last seam: one tile sees 15 px of it, one sees all 40."""
        pixels = np.zeros((800, 2000), dtype=np.uint8)
        pixels[300:320, 1425:1465] = 255
        chip = SarChip(pixels=pixels, bbox=(0.0, 0.0, 2.0, 0.8), width=2000, height=800)
        assert len(run_detection(chip, BrightSpotDetector())) == 1

    def test_centroid_geolocated(self):
        det = run_detection(self.make_chip(), BrightSpotDetector())[0]
        assert det.lon == pytest.approx(0.71)   # px 710 of 2000 over 2 deg
        assert det.lat == pytest.approx(0.49)   # px 310 of 800 from north over 0.8 deg
        assert det.confidence == 0.9
        assert det.bucket == "high"

    def test_empty_chip_no_detections(self):
        chip = SarChip(pixels=np.zeros((800, 800), dtype=np.uint8), bbox=(0.0, 0.0, 1.0, 1.0), width=800, height=800)
        assert run_detection(chip, BrightSpotDetector()) == []
