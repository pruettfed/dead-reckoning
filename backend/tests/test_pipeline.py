"""Pure-function tests for pipeline scene selection, pass estimation, and
footprint normalization. Orchestration (fetch/detect/fuse) is exercised live."""

from datetime import datetime, timedelta, timezone

from app.pipeline import estimate_next_pass, footprint_to_ewkt, pick_scene
from app.sar import SarScene

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


class TestPickScene:
    def test_newest_eligible_scene_wins(self):
        old = make_scene("old", NOW - timedelta(days=2))
        new = make_scene("new", NOW - timedelta(hours=6))
        min_ais = NOW - timedelta(days=3)
        assert pick_scene([old, new], min_ais, window_hours=2) is new

    def test_only_scene_outside_buffer_returns_none(self):
        scene = make_scene("s", NOW - timedelta(days=2))
        min_ais = NOW - timedelta(days=1)
        assert pick_scene([scene], min_ais, window_hours=2) is None

    def test_no_scenes_returns_none(self):
        assert pick_scene([], NOW - timedelta(days=1), window_hours=2) is None

    def test_no_ais_data_returns_none(self):
        assert pick_scene([make_scene("a", NOW)], None, window_hours=2) is None


class TestEstimateNextPass:
    def test_needs_at_least_three_passes(self):
        times = [NOW - timedelta(days=2), NOW - timedelta(days=1)]
        assert estimate_next_pass(times, NOW) is None

    def test_projects_median_interval_past_now(self):
        last = NOW - timedelta(days=2, hours=12)
        times = [last - timedelta(days=6), last - timedelta(days=3), last]
        assert estimate_next_pass(times, NOW) == last + timedelta(days=3)

    def test_rolls_forward_when_passes_missed(self):
        last = NOW - timedelta(days=7)
        times = [last - timedelta(days=6), last - timedelta(days=3), last]
        assert estimate_next_pass(times, NOW) == last + timedelta(days=9)

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
        assert estimate_next_pass(times, NOW) == last + timedelta(days=3)

    def test_unsorted_input_matches_sorted(self):
        times = [NOW - timedelta(days=1), NOW - timedelta(days=3), NOW - timedelta(days=2)]
        assert estimate_next_pass(times, NOW) == estimate_next_pass(sorted(times), NOW)


class TestFootprintToEwkt:
    BBOX = (56.5, 25.0, 57.1, 25.6)

    def test_cdse_wrapper_stripped(self):
        raw = "geography'SRID=4326;POLYGON ((56.1 24.9, 57.5 24.9, 57.5 25.8, 56.1 25.8, 56.1 24.9))'"
        assert footprint_to_ewkt(raw, self.BBOX) == (
            "SRID=4326;POLYGON ((56.1 24.9, 57.5 24.9, 57.5 25.8, 56.1 25.8, 56.1 24.9))"
        )

    def test_plain_wkt_gets_srid_prefix(self):
        assert footprint_to_ewkt("POLYGON ((0 0, 1 0, 1 1, 0 0))", self.BBOX) == (
            "SRID=4326;POLYGON ((0 0, 1 0, 1 1, 0 0))"
        )

    def test_missing_footprint_falls_back_to_bbox(self):
        ewkt = footprint_to_ewkt(None, self.BBOX)
        assert ewkt.startswith("SRID=4326;POLYGON((56.5 25.0,")
        assert "57.1 25.6" in ewkt
