from dataclasses import dataclass


@dataclass(frozen=True)
class ROI:
    name: str
    label: str
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


# Every ROI is constrained by two things at once: a dark-vessel narrative AND
# live AISStream terrestrial-receiver coverage (without AIS, fusion cannot call
# anything dark). Each bbox below is exactly a box that probed hot against the
# live feed (counts + method in docs/ais-coverage.md) — do not shrink one
# without re-probing: receivers and lanes hug coastlines, so "water-centering"
# a box can silently cut its coverage to zero. Some coastal land in the SAR
# chip is the accepted cost. Boxes fit one Sentinel-1 IW swath (~250 km).
ROIS: dict[str, ROI] = {
    "singapore_strait": ROI(
        name="singapore_strait",
        label="Singapore Strait",
        bbox=(103.55, 1.03, 104.10, 1.35),
    ),
    "north_taiwan": ROI(
        name="north_taiwan",
        label="North Taiwan / ECS approaches",
        bbox=(120.70, 25.10, 122.40, 26.30),
    ),
    "gulf_of_finland": ROI(
        name="gulf_of_finland",
        label="Gulf of Finland (shadow-fleet corridor)",
        bbox=(24.50, 59.20, 28.60, 60.30),
    ),
    "skagen_kattegat": ROI(
        name="skagen_kattegat",
        label="Skagen Anchorage (Kattegat)",
        bbox=(10.00, 57.30, 11.80, 58.30),
    ),
    "bosphorus_marmara": ROI(
        name="bosphorus_marmara",
        label="Bosphorus Approaches (Sea of Marmara)",
        bbox=(28.45, 40.72, 29.45, 40.98),
    ),
    "malta_hurds_bank": ROI(
        name="malta_hurds_bank",
        label="Hurd Bank (Malta offshore STS)",
        bbox=(14.20, 35.60, 15.00, 36.20),
    ),
}


def get_roi(name: str) -> ROI:
    try:
        return ROIS[name]
    except KeyError as e:
        known = ", ".join(sorted(ROIS))
        raise ValueError(f"unknown ROI {name!r}; known: {known}") from e
