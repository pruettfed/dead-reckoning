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
    # ---- fused: AIS verified live, 2026-07-11/12; boxes retuned 2026-07-26/2026-08-02 ----
    "north_taiwan": ROI(
        name="north_taiwan",
        label="North Taiwan / ECS approaches",
        ais_bbox=(120.70, 24.90, 122.40, 26.30),
        # Shifted onto the actual Keelung approach corridor: old box sat in
        # near-empty water offshore (175 vessels/2d); this sits on the
        # traffic (316 vessels/2d) and is *less* land (9.5% -> 3.3%).
        # 2026-08-02: nudged west to pick up more of the west-coast approach;
        # coverage held at 98% median / 7 usable passes all the way out to
        # 120.50, so this is a conservative slice of that headroom (probed).
        sar_bbox=(120.90, 24.95, 122.20, 25.60),
        mode="fused",
        passes_per_month=7,
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
        # Widened north edge to keep ais_bbox strictly containing the
        # enlarged sar_bbox below (2026-08-02).
        ais_bbox=(24.50, 59.20, 28.60, 60.40),
        # Shifted north onto the real shipping lane: old box sat south of it
        # (35 vessels/2d, mostly empty water); this sits on the lane
        # (144 vessels/2d), land% negligible (~0.1%).
        # 2026-08-02: enlarged north/south — this region's swath geometry
        # tolerates a lot of along-track growth without losing coverage
        # (probed 59.45-60.28: still 20/39 usable at 91% median, vs 20/39 at
        # 92% for the old box); east/west growth was tried and does cost
        # usable passes (a few marginal scenes drop below the 85% floor), so
        # left alone.
        sar_bbox=(25.20, 59.45, 27.60, 60.28),
        mode="fused",
        passes_per_month=20,
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
    "syria_coast_sts": ROI(
        name="syria_coast_sts",
        label="Syrian Coast (Baniyas / Tartus STS)",
        # Promoted from survey 2026-08-02: live AIS grew 4 -> 38 vessels in
        # the ais_bbox, but the old sar_bbox (35.50,35.00,35.95,35.45) only
        # reached 8 of those 38 — the rest clustered just south, around
        # Tartus. Same receiver-alignment gap that caused the
        # bosphorus_marmara false-dark bug, so the box was shifted south to
        # cover both Baniyas and Tartus (probed: 37/38 vessels now inside
        # sar_bbox, 5-bucket density check shows a gentle gradient rather
        # than a cliff). SAR coverage held at 20/20 usable, 100% median.
        ais_bbox=(35.40, 34.65, 36.15, 35.50),
        sar_bbox=(35.55, 34.80, 36.00, 35.30),
        mode="fused",
        passes_per_month=20,
        blurb=(
            "Ship-to-ship transfers at the Baniyas terminal and the "
            "Tartus naval/commercial port just south of it have been a "
            "documented mechanism for moving sanctioned Syrian and Iranian "
            "crude since the civil war."
        ),
    ),
    # ---- survey: no terrestrial AIS; vessel presence only, never "dark" ----
    "hormuz_strait": ROI(
        name="hormuz_strait",
        label="Strait of Hormuz (TSS)",
        # Widened on every side to keep strict containment of the enlarged
        # sar_bbox below (2026-08-02).
        ais_bbox=(55.65, 26.00, 56.95, 27.00),
        # Enlarged onto the TSS lanes (98% median coverage, 14 usable/mo) —
        # PU headroom spent here since it's the anchor of the Hormuz/Oman
        # story; a literal merge with fujairah_anchorage/musandam_stage into
        # one box was tried and rejected (median coverage collapses to
        # 36-58% — different swath geometry per sub-area). fujairah_anchorage
        # was dropped 2026-08-02 (see note below); this box is unrelated to
        # that decision.
        # 2026-08-02: enlarged west (probed) — usable passes drop 12 -> 10 as
        # a couple of marginal scenes fall below the 85% floor on the wider
        # box, but median coverage stays 95-99%, so still solid.
        sar_bbox=(55.95, 26.15, 56.85, 26.85),
        mode="survey",
        passes_per_month=10,
        blurb=(
            "About a fifth of global oil transits this 33km-wide "
            "chokepoint, and Iran has repeatedly seized or harassed tankers "
            "here amid sanctions tensions. No terrestrial AIS reaches it, so "
            "this is presence monitoring, not a dark-vessel claim."
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
        # Extended west toward Johor and the Singapore Strait receivers.
        ais_bbox=(104.30, 1.10, 105.15, 1.80),
        sar_bbox=(104.65, 1.25, 105.10, 1.70),
        mode="survey",
        passes_per_month=5,
        blurb=(
            "A known ship-to-ship anchorage off eastern Malaysia for "
            "sanctioned Iranian and Venezuelan crude, and one of the survey "
            "regions closest to real AIS coverage — its box reaches toward "
            "the Singapore Strait receivers, so it's a plausible candidate "
            "to promote to fused on further evidence (syria_coast_sts was "
            "promoted first, 2026-08-02)."
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
    "somali_coast": ROI(
        name="somali_coast",
        label="NE Somalia Coast",
        # Widened north/south to keep strict containment of the enlarged
        # sar_bbox below (2026-08-02).
        ais_bbox=(50.50, 8.40, 51.50, 9.45),
        # 2026-08-02: enlarged north/south (probed) — 8.60-9.25 holds the
        # same 10/15 usable passes at 86% median as the old box; pushing
        # further (8.50-9.35) drops usable to 7, so this is the largest
        # step that doesn't cost coverage.
        sar_bbox=(51.02, 8.60, 51.38, 9.25),
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
#
# Dropped 2026-08-02: `singapore_strait` (ais 103.45,0.95,104.20,1.40 / sar
# 103.55,1.03,104.10,1.28). Removed on user judgment — the region read as
# inconsistent against its own AIS ground truth and CV recall was weak given
# how dense the traffic is. `north_taiwan` is the new default ROI everywhere
# it was hardcoded (backend query defaults, frontend initial state).
#
# Dropped 2026-08-02: `fujairah_anchorage` (ais 56.20,24.95,56.90,25.65 / sar
# 56.265,25.33,56.535,25.57). Removed on user judgment. `hormuz_strait` still
# carries the Strait-of-Hormuz/Gulf-of-Oman narrative alongside
# `musandam_stage` and `kharg_island`.


def get_roi(name: str) -> ROI:
    try:
        return ROIS[name]
    except KeyError as e:
        known = ", ".join(sorted(ROIS))
        raise ValueError(f"unknown ROI {name!r}; known: {known}") from e
