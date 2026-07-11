from dataclasses import dataclass


@dataclass(frozen=True)
class ROI:
    name: str
    label: str
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


ROIS: dict[str, ROI] = {
    "strait_of_hormuz": ROI(
        name="strait_of_hormuz",
        label="Fujairah Anchorage (Gulf of Oman)",
        bbox=(56.50, 25.00, 57.10, 25.60),
    ),
    "taiwan_strait": ROI(
        name="taiwan_strait",
        label="Taiwan Strait",
        bbox=(119.00, 23.70, 119.80, 24.50),
    ),
    "spratly_islands": ROI(
        name="spratly_islands",
        label="Spratly Islands (S. China Sea)",
        bbox=(114.80, 9.60, 115.60, 10.40),
    ),
    "black_sea": ROI(
        name="black_sea",
        label="NE Black Sea (Kerch approaches)",
        bbox=(36.50, 44.20, 37.30, 44.90),
    ),
}


def get_roi(name: str) -> ROI:
    try:
        return ROIS[name]
    except KeyError as e:
        known = ", ".join(sorted(ROIS))
        raise ValueError(f"unknown ROI {name!r}; known: {known}") from e
