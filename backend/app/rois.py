from dataclasses import dataclass
from typing import Literal

Bbox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


@dataclass(frozen=True)
class ROI:
    """A monitored region. Two boxes, because they optimise in opposite directions.

    `ais_bbox` is what we subscribe to on AISStream. It is free, so it stays wide
    and hugs the coast — terrestrial receivers and traffic lanes both do.
    `sar_bbox` is what we buy pixels for and clip detections to. It costs PU, so
    it stays small and sits on water, away from land clutter. `sar_bbox` must be
    contained in `ais_bbox` (enforced in tests).
    """

    name: str
    label: str
    ais_bbox: Bbox
    sar_bbox: Bbox
    mode: Literal["fused", "survey"]
    # Measured *usable* passes per 30 days: those whose mosaicked footprint
    # covers >= MIN_FOOTPRINT_COVERAGE of sar_bbox. Passes that merely clip the
    # box are refused before any PU is spent, so this — not the raw pass count —
    # is what the budget test multiplies by estimate_pu. Re-probe with
    # `scripts/probe_regions.py` (free) after changing any sar_bbox.
    passes_per_month: int


# Two hard constraints, both measured — see docs/ais-coverage.md:
#
#   1. AIS coverage follows coastlines. Water-centering the AIS box collapses it
#      (north_taiwan 24→1 vessels, Skagen 82→17, Malta 5→0). Never shrink an
#      ais_bbox without re-probing the live feed.
#   2. SAR coverage has a floor, and "a pass exists" is not "the box is imaged".
#      Sentinel-1 runs IW over land and coastal water only, so an open-ocean box
#      gets nothing (a Somali Basin box returned 0 passes in 30 days) and an
#      open-water corridor gets grazed (Gulf of Aden IRTC: median 3% of the box
#      covered — dropped). Each sar_bbox below is placed to sit *inside* real
#      swath tracks, which is worth far more than making it small: repositioning
#      north_taiwan took it from 3/11 usable passes at 179 PU to 11/11 at 65 PU.
#
# Modes: `fused` regions have live AIS, so an unmatched detection is genuinely
# dark. `survey` regions have none — detections there are recorded as observed
# vessels with is_dark = NULL and must never be presented as dark. They still
# subscribe an ais_bbox (free) so a region can be promoted on evidence if a
# receiver ever appears.
ROIS: dict[str, ROI] = {
    # ---- fused: AIS verified live, 2026-07-11/12 ----
    "singapore_strait": ROI(
        name="singapore_strait",
        label="Singapore Strait",
        ais_bbox=(103.55, 1.03, 104.10, 1.35),
        sar_bbox=(103.55, 1.03, 104.10, 1.35),
        mode="fused",
        passes_per_month=6,
    ),
    "north_taiwan": ROI(
        name="north_taiwan",
        label="North Taiwan / ECS approaches",
        ais_bbox=(120.70, 24.90, 122.40, 26.30),
        sar_bbox=(121.705, 25.04, 122.245, 25.46),
        mode="fused",
        passes_per_month=11,
    ),
    "gulf_of_finland": ROI(
        name="gulf_of_finland",
        label="Gulf of Finland (shadow-fleet corridor)",
        ais_bbox=(24.50, 59.20, 28.60, 60.30),
        sar_bbox=(25.86, 59.475, 27.54, 59.875),
        mode="fused",
        passes_per_month=19,
    ),
    "skagen_kattegat": ROI(
        name="skagen_kattegat",
        label="Skagen Anchorage (Kattegat)",
        ais_bbox=(10.00, 57.30, 11.80, 58.30),
        sar_bbox=(10.35, 57.55, 11.35, 58.05),
        mode="fused",
        passes_per_month=20,
    ),
    "bosphorus_marmara": ROI(
        name="bosphorus_marmara",
        label="Bosphorus Approaches (Sea of Marmara)",
        ais_bbox=(28.45, 40.72, 29.45, 40.98),
        sar_bbox=(28.45, 40.72, 29.45, 40.98),
        mode="fused",
        passes_per_month=14,
    ),
    "malta_hurds_bank": ROI(
        name="malta_hurds_bank",
        label="Hurd Bank (Malta offshore STS)",
        ais_bbox=(14.20, 35.60, 15.00, 36.20),
        sar_bbox=(14.35, 35.75, 14.95, 36.15),
        mode="fused",
        passes_per_month=17,
    ),
    # ---- survey: no terrestrial AIS; vessel presence only, never "dark" ----
    "hormuz_strait": ROI(
        name="hormuz_strait",
        label="Strait of Hormuz (TSS)",
        ais_bbox=(55.90, 26.15, 56.75, 26.85),
        # Nudged north onto the TSS lanes: this also catches the 02:05Z track,
        # which the old box clipped at 69–81%. 10/20 usable → 14/20, 49 → 24 PU.
        sar_bbox=(56.30, 26.455, 56.65, 26.70),
        mode="survey",
        passes_per_month=14,
    ),
    "fujairah_anchorage": ROI(
        name="fujairah_anchorage",
        label="Fujairah / Khor Fakkan Anchorage (STS hub)",
        ais_bbox=(56.20, 24.95, 56.90, 25.65),
        sar_bbox=(56.265, 25.33, 56.535, 25.57),
        mode="survey",
        passes_per_month=14,
    ),
    "musandam_stage": ROI(
        name="musandam_stage",
        label="Musandam Staging Area",
        ais_bbox=(56.80, 25.50, 57.50, 26.10),
        sar_bbox=(56.90, 25.60, 57.40, 26.05),
        mode="survey",
        passes_per_month=9,
    ),
    "kharg_island": ROI(
        name="kharg_island",
        label="Kharg Island Terminal",
        ais_bbox=(50.00, 29.00, 50.70, 29.60),
        sar_bbox=(50.10, 29.10, 50.60, 29.50),
        mode="survey",
        passes_per_month=10,
    ),
    "eopl_tompok_utara": ROI(
        name="eopl_tompok_utara",
        label="EOPL / Tompok Utara (STS anchorage)",
        # Extended west toward Johor and the Singapore receivers that feed
        # singapore_strait — the one survey region with a plausible path to fused.
        ais_bbox=(104.30, 1.10, 105.15, 1.80),
        sar_bbox=(104.65, 1.25, 105.10, 1.70),
        mode="survey",
        passes_per_month=6,
    ),
    "kerch_strait": ROI(
        name="kerch_strait",
        label="Kerch Strait",
        ais_bbox=(36.30, 45.00, 36.80, 45.50),
        sar_bbox=(36.35, 45.05, 36.75, 45.45),
        mode="survey",
        passes_per_month=11,
    ),
    "syria_coast_sts": ROI(
        name="syria_coast_sts",
        label="Syrian Coast (Baniyas STS)",
        ais_bbox=(35.40, 34.90, 36.00, 35.50),
        sar_bbox=(35.50, 35.00, 35.95, 35.45),
        mode="survey",
        passes_per_month=20,
    ),
    "somali_coast": ROI(
        name="somali_coast",
        label="NE Somalia Coast",
        ais_bbox=(50.50, 8.50, 51.50, 9.30),
        sar_bbox=(51.02, 8.76, 51.38, 9.09),
        mode="survey",
        passes_per_month=9,
    ),
}

# Dropped 2026-07-21: `gulf_of_aden_irtc` (44.80, 11.80, 45.60, 12.60). The IRTC
# corridor is open water, and Sentinel-1 runs IW over coastal areas — passes
# covered a median 3% of the box, with only 3/11 usable. No resize fixed it; the
# nearest workable placement was the Berbera coast, which is a different subject.
# `somali_coast` carries the Horn of Africa narrative instead.


def get_roi(name: str) -> ROI:
    try:
        return ROIS[name]
    except KeyError as e:
        known = ", ".join(sorted(ROIS))
        raise ValueError(f"unknown ROI {name!r}; known: {known}") from e
