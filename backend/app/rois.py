from dataclasses import dataclass
from typing import Literal

Bbox = tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)


@dataclass(frozen=True)
class ROI:
    """A monitored region. Two boxes, because they optimise in opposite directions.

    `ais_bbox` is what we subscribe to on AISStream. It is free, so it stays wide
    and hugs the coast — terrestrial receivers and traffic lanes both do.
    `sar_bbox` is what we buy pixels for and clip detections to. It costs PU, so
    it stays small and sits on water, away from land clutter. `ais_bbox` must be
    *strictly* wider than `sar_bbox` on every side, not just containing it
    (enforced in tests) — a shared edge leaves detections at the boundary with
    no AIS buffer to match against, and (2026-07-26) it hid a receiver-range
    cliff in `bosphorus_marmara`, see below.
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
    # One sentence: why this region is tracked. Exposed via GET /api/rois.
    blurb: str


# Three hard constraints, all measured — see docs/ais-coverage.md:
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
#   3. AIS coverage inside a fused sar_bbox must be spatially uniform, not just
#      present somewhere in the box. bosphorus_marmara passed the original
#      "some vessels exist here" probe (45 total) but a 2026-07-26 live review
#      found live AIS density collapsing from 195→271→47→3 vessels moving
#      west→east across the box (a hard receiver-range cliff — latitude bins
#      were smooth, so it wasn't noise). Every detection past the cliff was
#      structurally guaranteed to read "dark" regardless of whether it carried
#      AIS. Check any fused candidate with a 5-bucket width_bucket() density
#      query along both axes, not just an aggregate count — see
#      docs/ais-coverage.md.
#
# Modes: `fused` regions have live AIS, so an unmatched detection is genuinely
# dark. `survey` regions have none — detections there are recorded as observed
# vessels with is_dark = NULL and must never be presented as dark. They still
# subscribe an ais_bbox (free) so a region can be promoted on evidence if a
# receiver ever appears.
#
# Sized 2026-07-26 against live AIS density + probe_regions.py, using
# available PU headroom (10,737/30,000 was 36%) rather than minimizing it —
# boxes are now placed for coverage first, cost second, subject to the
# 85% SAR floor and constraint 3 above.
ROIS: dict[str, ROI] = {
    # ---- fused: AIS verified live, 2026-07-11/12; boxes retuned 2026-07-26 ----
    "singapore_strait": ROI(
        name="singapore_strait",
        label="Singapore Strait",
        ais_bbox=(103.45, 0.95, 104.20, 1.40),
        # Shrunk to mid-channel: old box was 32.6% land, this is 24.9%, at
        # near-identical vessel count (1241 vs 1238) since the trimmed strip
        # was pure land — costs less too (277 -> ~217 PU/mo).
        sar_bbox=(103.55, 1.03, 104.10, 1.28),
        mode="fused",
        passes_per_month=5,
        blurb=(
            "The world's busiest strait by tonnage — persistent armed "
            "robbery/piracy (an IMB-documented hotspot) and its role as a "
            "waypoint for sanctioned Iranian and Venezuelan crude moved "
            "ship-to-ship in nearby Malaysian and Indonesian waters make it "
            "worth watching, even though it isn't a conflict zone in the way "
            "the Gulf regions are."
        ),
    ),
    "north_taiwan": ROI(
        name="north_taiwan",
        label="North Taiwan / ECS approaches",
        ais_bbox=(120.70, 24.90, 122.40, 26.30),
        # Shifted onto the actual Keelung approach corridor: old box sat in
        # near-empty water offshore (175 vessels/2d); this sits on the
        # traffic (316 vessels/2d) and is *less* land (9.5% -> 3.3%).
        sar_bbox=(121.20, 24.95, 122.20, 25.60),
        mode="fused",
        passes_per_month=8,
        blurb=(
            "Gray-zone pressure zone north of Taiwan — the corridor where "
            "Chinese-linked vessels have been implicated in subsea-cable "
            "interference off Keelung, with documented AIS spoofing during "
            "those incidents."
        ),
    ),
    "gulf_of_finland": ROI(
        name="gulf_of_finland",
        label="Gulf of Finland (shadow-fleet corridor)",
        ais_bbox=(24.50, 59.20, 28.60, 60.30),
        # Shifted north onto the real shipping lane: old box sat south of it
        # (35 vessels/2d, mostly empty water); this sits on the lane
        # (144 vessels/2d), land% negligible (~0.1%).
        sar_bbox=(25.20, 59.75, 27.60, 60.15),
        mode="fused",
        passes_per_month=22,
        blurb=(
            "The Baltic exit corridor for Russia's shadow fleet — aging, "
            "opaquely-owned tankers moving sanctioned crude out of Primorsk "
            "and Ust-Luga, with widespread documented AIS manipulation."
        ),
    ),
    "skagen_kattegat": ROI(
        name="skagen_kattegat",
        label="Skagen Anchorage (Kattegat)",
        ais_bbox=(9.85, 57.15, 11.95, 58.45),
        # Already well-placed; enlarged since the swath tolerates it at 100%
        # coverage (354 -> 495 vessels/2d).
        sar_bbox=(10.00, 57.40, 11.60, 58.20),
        mode="fused",
        passes_per_month=20,
        blurb=(
            "The only sea exit from the Baltic to the North Sea — every "
            "shadow-fleet tanker loading in the Gulf of Finland must funnel "
            "through here, which is why Danish authorities have begun "
            "boarding and inspecting suspect tankers specifically in this "
            "chokepoint."
        ),
    ),
    "bosphorus_marmara": ROI(
        name="bosphorus_marmara",
        label="Bosphorus Approaches (Sea of Marmara)",
        ais_bbox=(28.20, 40.55, 29.70, 41.15),
        # AIS-cliff fix (2026-07-26, see constraint 3 above): shrunk to
        # 28.85 max_lon, inside the real Istanbul-receiver coverage boundary
        # (density collapses past ~28.78). Still 100% SAR coverage, and
        # cheaper as a side effect (930 -> ~400 PU/mo) since the imaged area
        # shrank.
        sar_bbox=(28.45, 40.72, 28.85, 41.00),
        mode="fused",
        passes_per_month=15,
        blurb=(
            "The sole sea route for Russian Black Sea oil and grain exports "
            "leaving through Turkey's Straits, and the closest observable "
            "proxy for Kerch Strait traffic, which itself has no usable AIS "
            "coverage."
        ),
    ),
    "malta_hurds_bank": ROI(
        name="malta_hurds_bank",
        label="Hurd Bank (Malta offshore STS)",
        # Widened for viewport headroom only — traffic was already saturated
        # inside the old ais_bbox, and sar_bbox is unchanged: enlarging it
        # drops SAR coverage below 85% (narrow swath track here).
        ais_bbox=(13.90, 35.30, 15.35, 36.50),
        sar_bbox=(14.35, 35.75, 14.95, 36.15),
        mode="fused",
        passes_per_month=15,
        blurb=(
            "A documented ship-to-ship transfer hotspot in the central "
            "Mediterranean for sanctioned Russian and Iranian crude — Hurd "
            "Bank sits just outside Maltese territorial waters, beyond "
            "routine port-state inspection but a short hop from EU "
            "refineries."
        ),
    ),
    # ---- survey: no terrestrial AIS; vessel presence only, never "dark" ----
    "hormuz_strait": ROI(
        name="hormuz_strait",
        label="Strait of Hormuz (TSS)",
        ais_bbox=(55.85, 26.10, 56.95, 26.90),
        # Enlarged onto the TSS lanes (98% median coverage, 14 usable/mo) —
        # PU headroom spent here since it's the anchor of the Hormuz/Oman
        # story; a literal merge with fujairah_anchorage/musandam_stage into
        # one box was tried and rejected (median coverage collapses to
        # 36-58% — different swath geometry per sub-area).
        sar_bbox=(56.20, 26.20, 56.80, 26.80),
        mode="survey",
        passes_per_month=14,
        blurb=(
            "About a fifth of global oil transits this 33km-wide "
            "chokepoint, and Iran has repeatedly seized or harassed tankers "
            "here amid sanctions tensions. No terrestrial AIS reaches it, so "
            "this is presence monitoring, not a dark-vessel claim."
        ),
    ),
    "fujairah_anchorage": ROI(
        name="fujairah_anchorage",
        label="Fujairah / Khor Fakkan Anchorage (STS hub)",
        ais_bbox=(56.20, 24.95, 56.90, 25.65),
        sar_bbox=(56.265, 25.33, 56.535, 25.57),
        mode="survey",
        passes_per_month=16,
        blurb=(
            "The anchorage south of Hormuz where tankers wait, disguise "
            "cargo, or transfer it ship-to-ship before or after transiting "
            "the strait — the other half of the same oil-chokepoint story."
        ),
    ),
    "musandam_stage": ROI(
        name="musandam_stage",
        label="Musandam Staging Area",
        ais_bbox=(56.80, 25.50, 57.50, 26.10),
        sar_bbox=(56.90, 25.60, 57.40, 26.05),
        mode="survey",
        passes_per_month=10,
        blurb=(
            "A staging area for vessels queuing to transit Hormuz, on the "
            "Omani peninsula that pinches the strait to its narrowest point."
        ),
    ),
    "kharg_island": ROI(
        name="kharg_island",
        label="Kharg Island Terminal",
        ais_bbox=(50.00, 29.00, 50.70, 29.60),
        sar_bbox=(50.10, 29.10, 50.60, 29.50),
        mode="survey",
        passes_per_month=10,
        blurb=(
            "Iran's primary crude export terminal — nearly all the oil that "
            "later transits Hormuz loads here first, making this the "
            "upstream half of the same sanctions-evasion story."
        ),
    ),
    "eopl_tompok_utara": ROI(
        name="eopl_tompok_utara",
        label="EOPL / Tompok Utara (STS anchorage)",
        # Extended west toward Johor and the Singapore receivers that feed
        # singapore_strait — the one survey region with a plausible path to fused.
        ais_bbox=(104.30, 1.10, 105.15, 1.80),
        sar_bbox=(104.65, 1.25, 105.10, 1.70),
        mode="survey",
        passes_per_month=5,
        blurb=(
            "A known ship-to-ship anchorage off eastern Malaysia for "
            "sanctioned Iranian and Venezuelan crude, and the survey region "
            "closest to real AIS coverage — its box reaches toward the "
            "Singapore receivers, so it's the likeliest candidate to promote "
            "to fused if one ever appears."
        ),
    ),
    "kerch_strait": ROI(
        name="kerch_strait",
        label="Kerch Strait",
        ais_bbox=(36.30, 45.00, 36.80, 45.50),
        sar_bbox=(36.35, 45.05, 36.75, 45.45),
        mode="survey",
        passes_per_month=10,
        blurb=(
            "The Russian-side loading point for the same Black Sea shadow "
            "fleet that surfaces again at Bosphorus, the nearest region "
            "with usable AIS."
        ),
    ),
    "syria_coast_sts": ROI(
        name="syria_coast_sts",
        label="Syrian Coast (Baniyas STS)",
        ais_bbox=(35.40, 34.90, 36.00, 35.50),
        sar_bbox=(35.50, 35.00, 35.95, 35.45),
        mode="survey",
        passes_per_month=20,
        blurb=(
            "Ship-to-ship transfers at the Baniyas terminal have been a "
            "documented mechanism for moving sanctioned Syrian and Iranian "
            "crude since the civil war."
        ),
    ),
    "somali_coast": ROI(
        name="somali_coast",
        label="NE Somalia Coast",
        ais_bbox=(50.50, 8.50, 51.50, 9.30),
        sar_bbox=(51.02, 8.76, 51.38, 9.09),
        mode="survey",
        passes_per_month=10,
        blurb=(
            "A historic piracy corridor off the Horn of Africa, now "
            "entangled with the broader Red Sea shipping crisis reshaping "
            "traffic patterns and insurance risk in the region."
        ),
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
