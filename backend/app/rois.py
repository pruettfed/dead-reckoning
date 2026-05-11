"""Static registry of named Regions of Interest.

The active ROI (selected via `ACTIVE_ROI`) drives both the AISStream subscription
filter and, later, the imagery query. Bboxes are first-pass approximations.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ROI:
    name: str
    label: str
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


ROIS: dict[str, ROI] = {
    "south_china_sea": ROI(
        name="south_china_sea",
        label="South China Sea",
        bbox=(105.0, 0.0, 122.0, 23.0),
    ),
    "strait_of_hormuz": ROI(
        name="strait_of_hormuz",
        label="Strait of Hormuz",
        bbox=(54.0, 24.0, 58.5, 27.5),
    ),
    "gulf_of_guinea": ROI(
        name="gulf_of_guinea",
        label="Gulf of Guinea",
        bbox=(-5.0, -2.0, 9.0, 7.0),
    ),
    "eastern_mediterranean": ROI(
        name="eastern_mediterranean",
        label="Eastern Mediterranean",
        bbox=(22.0, 30.0, 36.0, 38.0),
    ),
}


def get_roi(name: str) -> ROI:
    try:
        return ROIS[name]
    except KeyError as exc:
        known = ", ".join(sorted(ROIS))
        raise ValueError(f"unknown ROI {name!r}; known: {known}") from exc
