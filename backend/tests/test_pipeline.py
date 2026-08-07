"""Pure-function tests for pipeline scene selection, pass estimation, and
footprint normalization. Orchestration (fetch/detect/fuse) is exercised live."""

from datetime import datetime, timedelta, timezone

from app.pipeline import (
    MIN_SCENE_AGE,
    _footprint_wkts_in_window,
    eligible_scenes,
    estimate_next_pass,
    imaged_footprint_wkts,
)
from app.sar import SarScene, _bbox_to_polygon_wkt

NOW = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)


def make_scene(scene_id: str, sensed_at: datetime) -> SarScene:
    return SarScene(
        id=scene_id,
        name=f"S1A_IW_GRDH_1SDV_{scene_id}",
        sensed_at=sensed_at,
        footprint_wkt=None,
        platform="S1A",
        is_cog=False,
    )


class TestEligibleScenes:
    # A live stream: AIS reaches up to now, so the ceiling never excludes a
    # scene and each test below still measures only what it names.
    MAX_AIS = NOW

    def test_newest_first(self):
        """Order matters: the caller walks these until one covers the bbox."""
        old = make_scene("old", NOW - timedelta(days=2))
        new = make_scene("new", NOW - timedelta(hours=6))
        min_ais = NOW - timedelta(days=3)
        assert eligible_scenes([old, new], min_ais, self.MAX_AIS, window_hours=2) == [new, old]

    def test_scene_outside_buffer_excluded(self):
        scene = make_scene("s", NOW - timedelta(days=2))
        min_ais = NOW - timedelta(days=1)
        assert eligible_scenes([scene], min_ais, self.MAX_AIS, window_hours=2) == []

    def test_no_scenes(self):
        assert eligible_scenes([], NOW - timedelta(days=1), self.MAX_AIS, window_hours=2) == []

    def test_no_ais_data_excludes_everything(self):
        assert eligible_scenes([make_scene("a", NOW)], None, None, window_hours=2) == []

    def test_scene_past_a_stale_buffer_excluded(self):
        # Ingest died six hours ago; the scene is newer than anything AIS can
        # bracket, so fusing it would call every vessel dark.
        scene = make_scene("s", NOW - timedelta(hours=1))
        min_ais = NOW - timedelta(days=2)
        max_ais = NOW - timedelta(hours=6)
        assert eligible_scenes([scene], min_ais, max_ais, window_hours=2) == []


class TestFootprintWktsInWindow:
    """The Process API mosaics [-1 min, +10 min], so coverage is the union of
    the slices in that window, not the anchor slice alone."""

    @staticmethod
    def _scene(scene_id, offset_min, wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))"):
        s = make_scene(scene_id, NOW + timedelta(minutes=offset_min))
        return SarScene(
            id=s.id, name=s.name, sensed_at=s.sensed_at,
            footprint_wkt=wkt, platform=s.platform, is_cog=False,
        )

    def test_includes_slices_ahead_of_anchor(self):
        anchor = self._scene("a", 0)
        scenes = [anchor, self._scene("b", 5), self._scene("c", 9)]
        assert len(_footprint_wkts_in_window(scenes, anchor)) == 3

    def test_excludes_slices_outside_window(self):
        anchor = self._scene("a", 0)
        scenes = [anchor, self._scene("late", 11), self._scene("early", -2)]
        assert len(_footprint_wkts_in_window(scenes, anchor)) == 1

    def test_strips_srid_prefix(self):
        anchor = self._scene("a", 0, "SRID=4326;POLYGON((0 0,1 0,1 1,0 1,0 0))")
        assert _footprint_wkts_in_window([anchor], anchor) == [
            "POLYGON((0 0,1 0,1 1,0 1,0 0))"
        ]

    def test_skips_scenes_without_footprint(self):
        anchor = self._scene("a", 0)
        assert _footprint_wkts_in_window([anchor, make_scene("nofp", NOW)], anchor) == [
            "POLYGON((0 0,1 0,1 1,0 1,0 0))"
        ]


class TestEstimateNextPass:
    def test_needs_at_least_three_passes(self):
        times = [NOW - timedelta(days=2), NOW - timedelta(days=1)]
        assert estimate_next_pass(times, NOW) is None

    def test_projects_median_interval_past_now(self):
        last = NOW - timedelta(days=2, hours=12)
        times = [last - timedelta(days=6), last - timedelta(days=3), last]
        assert estimate_next_pass(times, NOW) == last + timedelta(days=3) + MIN_SCENE_AGE

    def test_rolls_forward_when_passes_missed(self):
        last = NOW - timedelta(days=7)
        times = [last - timedelta(days=6), last - timedelta(days=3), last]
        assert estimate_next_pass(times, NOW) == last + timedelta(days=9) + MIN_SCENE_AGE

    def test_result_is_strictly_in_future(self):
        times = [NOW - timedelta(days=3), NOW - timedelta(days=2), NOW - timedelta(days=1)]
        assert estimate_next_pass(times, NOW) > NOW

    def test_identical_timestamps_return_none(self):
        times = [NOW - timedelta(days=1)] * 4
        assert estimate_next_pass(times, NOW) is None

    def test_duplicate_products_per_pass_collapsed(self):
        # CDSE lists two products (standard + COG) per acquisition
        last = NOW - timedelta(days=1)
        passes = [last - timedelta(days=6), last - timedelta(days=3), last]
        times = [t for t in passes for _ in range(2)]
        assert estimate_next_pass(times, NOW) == last + timedelta(days=3) + MIN_SCENE_AGE

    def test_unsorted_input_matches_sorted(self):
        times = [NOW - timedelta(days=1), NOW - timedelta(days=3), NOW - timedelta(days=2)]
        assert estimate_next_pass(times, NOW) == estimate_next_pass(sorted(times), NOW)

    def test_estimate_includes_min_scene_age_offset(self):
        # The satellite pass itself lands earlier — find_target_scene won't
        # fetch it until it clears MIN_SCENE_AGE, so the estimate shown to
        # users must reflect that or it reads "overdue" for hours for no
        # visible reason.
        last = NOW - timedelta(days=2, hours=12)
        times = [last - timedelta(days=6), last - timedelta(days=3), last]
        raw_pass_time = last + timedelta(days=3)
        assert estimate_next_pass(times, NOW) == raw_pass_time + MIN_SCENE_AGE


class TestImagedFootprintWkts:
    """The stored footprint is the mosaic-window union, not the anchor slice — the
    clip deletes real detections in the imaged rest if it's only one slice."""

    BBOX = (56.9, 25.6, 57.4, 26.05)

    @staticmethod
    def _scene(scene_id, offset_min, wkt):
        s = make_scene(scene_id, NOW + timedelta(minutes=offset_min))
        return SarScene(
            id=s.id, name=s.name, sensed_at=s.sensed_at,
            footprint_wkt=wkt, platform=s.platform, is_cog=False,
        )

    def test_returns_every_in_window_slice(self):
        anchor = self._scene("a", 0, "POLYGON((0 0,1 0,1 1,0 1,0 0))")
        ahead = self._scene("b", 5, "POLYGON((1 0,2 0,2 1,1 1,1 0))")
        assert imaged_footprint_wkts([anchor, ahead], anchor, self.BBOX) == [
            "POLYGON((0 0,1 0,1 1,0 1,0 0))",
            "POLYGON((1 0,2 0,2 1,1 1,1 0))",
        ]

    def test_falls_back_to_bbox_when_no_footprints(self):
        anchor = make_scene("a", NOW)  # footprint_wkt is None
        assert imaged_footprint_wkts([anchor], anchor, self.BBOX) == [
            _bbox_to_polygon_wkt(self.BBOX)
        ]
