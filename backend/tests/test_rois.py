import pytest

from app.rois import ROIS, get_roi
from app.sar import PU_MONTHLY_BUDGET, estimate_pu, plan_fetch_grid

FUSED = {
    "north_taiwan",
    "gulf_of_finland",
    "skagen_kattegat",
    "bosphorus_marmara",
    "malta_hurds_bank",
    "syria_coast_sts",
}
SURVEY = {
    "hormuz_strait",
    "musandam_stage",
    "kharg_island",
    "eopl_tompok_utara",
    "kerch_strait",
    "somali_coast",
}


def test_expected_rois_present():
    assert set(ROIS) == FUSED | SURVEY


def test_modes_match_registry():
    assert {n for n, r in ROIS.items() if r.mode == "fused"} == FUSED
    assert {n for n, r in ROIS.items() if r.mode == "survey"} == SURVEY


@pytest.mark.parametrize("name", list(ROIS))
def test_bboxes_are_well_formed(name: str):
    roi = ROIS[name]
    for bbox in (roi.ais_bbox, roi.sar_bbox):
        min_lon, min_lat, max_lon, max_lat = bbox
        assert -180 <= min_lon < max_lon <= 180
        assert -90 <= min_lat < max_lat <= 90
    assert roi.name == name
    assert roi.passes_per_month > 0
    assert roi.blurb.strip() == roi.blurb and len(roi.blurb) > 20


@pytest.mark.parametrize("name", list(ROIS))
def test_sar_bbox_strictly_within_ais_bbox(name: str):
    """ais_bbox must be strictly wider than sar_bbox on every side, not just
    containing it. AIS is free so it should always have slack; a shared
    edge (or sar_bbox == ais_bbox) means a detection right at the boundary
    has no AIS buffer around it to match against."""
    roi = ROIS[name]
    a_min_lon, a_min_lat, a_max_lon, a_max_lat = roi.ais_bbox
    s_min_lon, s_min_lat, s_max_lon, s_max_lat = roi.sar_bbox
    assert a_min_lon < s_min_lon and s_max_lon < a_max_lon
    assert a_min_lat < s_min_lat and s_max_lat < a_max_lat


def test_monthly_pu_within_budget():
    """Every pass on every ROI must fit the 30,000 PU/month budget.

    Costs come from sar_bbox only — the AIS subscription is free.
    """
    total = sum(
        estimate_pu(plan_fetch_grid(roi.sar_bbox)) * roi.passes_per_month
        for roi in ROIS.values()
    )
    assert total < PU_MONTHLY_BUDGET, f"registry would spend {total:,.0f} PU/month"


def test_get_roi_returns_known():
    assert get_roi("north_taiwan").label == "North Taiwan / ECS approaches"


def test_get_roi_unknown_raises():
    with pytest.raises(ValueError, match="unknown ROI"):
        get_roi("nonexistent")
