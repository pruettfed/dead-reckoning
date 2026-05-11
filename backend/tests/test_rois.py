import pytest

from app.rois import ROIS, get_roi


def test_four_rois_present():
    assert set(ROIS) == {
        "south_china_sea",
        "strait_of_hormuz",
        "gulf_of_guinea",
        "eastern_mediterranean",
    }


@pytest.mark.parametrize("name", list(ROIS))
def test_bbox_is_well_formed(name: str):
    roi = ROIS[name]
    min_lon, min_lat, max_lon, max_lat = roi.bbox
    assert -180 <= min_lon < max_lon <= 180
    assert -90 <= min_lat < max_lat <= 90
    assert roi.name == name


def test_get_roi_returns_known():
    assert get_roi("south_china_sea").label == "South China Sea"


def test_get_roi_unknown_raises():
    with pytest.raises(ValueError, match="unknown ROI"):
        get_roi("nonexistent")
