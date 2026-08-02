from datetime import datetime, timezone

from app.main import _sighting, list_rois
from app.rois import ROIS


async def test_rois_expose_passes_per_month():
    rois = await list_rois()
    assert {r["name"] for r in rois} == set(ROIS)
    for r in rois:
        assert r["passes_per_month"] == ROIS[r["name"]].passes_per_month
        assert isinstance(r["passes_per_month"], int)


def test_sighting_resolves_roi_label():
    sensed = datetime(2026, 8, 1, 22, 14, tzinfo=timezone.utc)
    row = {
        "detection_id": 91,
        "scene_id": "abc",
        "roi": "north_taiwan",
        "sensed_at": sensed,
        "match_state": "matched",
        "is_dark": False,
        "confidence": 0.81,
        "matched": True,
    }
    assert _sighting(row) == {
        "detection_id": 91,
        "scene_id": "abc",
        "roi": "north_taiwan",
        "label": ROIS["north_taiwan"].label,
        "sensed_at": sensed,
        "match_state": "matched",
        "is_dark": False,
        "confidence": 0.81,
        "matched": True,
    }


def test_sighting_passes_through_matched_false():
    # A candidate-only sighting (this MMSI was the nearest candidate for a
    # dark/indeterminate detection, but not the one actually matched). Guards
    # against _sighting() coercing a boolean False via truthiness/bool() —
    # it does not do the SQL-level NULL-vs-false coercion itself (that lives
    # in SIGHTINGS_QUERY's COALESCE), so this only confirms the helper is a
    # faithful passthrough.
    row = {
        "detection_id": 5,
        "scene_id": "y",
        "roi": "north_taiwan",
        "sensed_at": datetime(2026, 7, 15, tzinfo=timezone.utc),
        "match_state": "dark",
        "is_dark": True,
        "confidence": 0.7,
        "matched": False,
    }
    assert _sighting(row)["matched"] is False


def test_sighting_falls_back_to_roi_name_for_unknown_roi():
    row = {
        "detection_id": 1,
        "scene_id": "x",
        "roi": "retired_region",
        "sensed_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "match_state": "dark",
        "is_dark": True,
        "confidence": 0.9,
        "matched": False,
    }
    assert _sighting(row)["label"] == "retired_region"
