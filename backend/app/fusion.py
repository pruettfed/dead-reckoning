"""SAR detections ↔ AIS fusion by dead reckoning.

AIS is a sparse, laggy sample of a moving thing; a SAR image is an instant.
Matching raw positions cannot work — measured cadence is a median 168 s between
fixes, 864 m of travel at 10 kn, further than any sane radius. So positions are
projected to the acquisition instant and compared against a physical budget:

    gate     = MATCH_RADIUS_M + AZIMUTH_SHIFT_S · v    (may be this vessel)
    envelope = gate + COURSE_ERR_FRAC · v · |fix age|  (cannot be ruled out)

`AZIMUTH_SHIFT_S · v` is SAR physics: a moving target images displaced
along-azimuth by (R/V)·v_radial, ~450 m at 10 kn. Carried isotropically since
the look vector is unknown — over-inclusive by design, because the direction
that costs us is a false "dark".

Every scene probes empty water in its own traffic and stores the resulting
`chance_match_rate`; above MAX_CHANCE_MATCH_RATE dark calls are withheld. Three
outcomes: matched, indeterminate, dark. Survey ROIs skip the match entirely.

Derivation and measurements: docs/fusion-rework.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.detect import GeoDetection

KNOTS_TO_MS = 0.514444
# Below this, `cog` is noise and would fling moored ships across the harbour.
MIN_SOG_FOR_DR_KN = 0.5
# Vessels this far outside the footprint can still dead-reckon into it.
CANDIDATE_PAD_M = 30_000.0
PROBE_RADIUS_M = 4_000.0
PROBE_MIN_SEPARATION_M = 600.0
PROBE_TARGET = 750
# AIS ship_type 60-89: passenger, cargo, tanker — the hulls 10 m/px resolves.
# Trusted regardless of which class (A or B) reported it.
LARGE_VESSEL_TYPE_MIN = 60
LARGE_VESSEL_TYPE_MAX = 89
RECALL_RADIUS_M = 500.0


@dataclass(frozen=True)
class MatchCandidate:
    """One (detection, vessel) pair that survived the gate."""

    det_id: int
    mmsi: int
    distance_m: float
    time_delta_s: float


def coverage_ok(
    sensed_at: datetime,
    min_ais_time: datetime | None,
    max_ais_time: datetime | None,
    window_hours: float,
) -> bool:
    """Whether the AIS buffer brackets the scene's correlation window on both sides.

    The ceiling is not symmetry for its own sake: a floor-only check passes a
    fresh scene even when ingest died days ago, because rows from before the
    outage survive the retention prune. Fusion then matches nothing, measures a
    0% chance-match rate on empty water, reads as discriminating, and calls
    every vessel dark — a false dark-fleet report that looks sound.
    """
    if min_ais_time is None or max_ais_time is None:
        return False
    return (
        sensed_at - timedelta(hours=window_hours) >= min_ais_time
        and sensed_at + timedelta(hours=window_hours) <= max_ais_time
    )


def assign_one_to_one(candidates: list[MatchCandidate]) -> dict[int, MatchCandidate]:
    """Greedy mutual-exclusion assignment, closest pair first.

    One vessel cannot be in two places; without this, MMSI 416042000 was assigned
    to two detections 4.2 km apart. Ties break on id so the result is stable.
    """
    assigned: dict[int, MatchCandidate] = {}
    claimed: set[int] = set()
    for candidate in sorted(
        candidates, key=lambda c: (c.distance_m, c.det_id, c.mmsi)
    ):
        if candidate.det_id in assigned or candidate.mmsi in claimed:
            continue
        assigned[candidate.det_id] = candidate
        claimed.add(candidate.mmsi)
    return assigned


def classify(det_id: int, assigned: dict[int, MatchCandidate], margin_m: float | None,
             discriminating: bool) -> str:
    """matched | dark | indeterminate.

    `margin_m` is metres outside the nearest envelope (None = no AIS candidate);
    `discriminating` is the scene's chance-match verdict, which vetoes all darks.
    """
    if det_id in assigned:
        return "matched"
    if margin_m is None or margin_m <= 0:
        return "indeterminate"
    return "dark" if discriminating else "indeterminate"


INSERT_DETECTION = text(
    """
    INSERT INTO sar_detections (scene_id, location, confidence, confidence_bucket)
    VALUES (
        :scene_id,
        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
        :confidence,
        :bucket
    )
    """
)

# A detection outside the imaged footprint is unobserved, not dark — drop it.
CLIP_TO_FOOTPRINT = text(
    """
    DELETE FROM sar_detections d
    USING sar_scenes s
    WHERE d.scene_id = :scene_id
      AND s.id = :scene_id
      AND NOT ST_Covers(s.footprint, d.location)
    """
)

ADD_FUSION_COLUMNS = (
    text("ALTER TABLE sar_detections ADD COLUMN IF NOT EXISTS match_state VARCHAR(16)"),
    text("ALTER TABLE sar_detections ADD COLUMN IF NOT EXISTS dark_margin_m DOUBLE PRECISION"),
    text("ALTER TABLE sar_scenes ADD COLUMN IF NOT EXISTS chance_match_rate DOUBLE PRECISION"),
    text("ALTER TABLE sar_scenes ADD COLUMN IF NOT EXISTS recall_large_total INTEGER"),
    text("ALTER TABLE sar_scenes ADD COLUMN IF NOT EXISTS recall_large_detected INTEGER"),
    text("ALTER TABLE sar_detections ADD COLUMN IF NOT EXISTS candidate_mmsi BIGINT"),
)

# Every vessel's nearest-in-time fix projected to the acquisition instant. Shared
# by the match, probe and recall queries so all three measure the same matcher.
DR_CTE = """
    fix AS (
        SELECT DISTINCT ON (p.mmsi)
               p.mmsi, p.location, p.sog, p.cog,
               EXTRACT(EPOCH FROM (CAST(:sensed_at AS timestamptz) - p.time))::double precision AS lead_s,
               EXTRACT(EPOCH FROM (p.time - CAST(:sensed_at AS timestamptz)))::double precision AS delta_s
        FROM ais_positions p
        WHERE p.time BETWEEN CAST(:sensed_at AS timestamptz) - make_interval(secs => :fix_max_age_s)
                         AND CAST(:sensed_at AS timestamptz) + make_interval(secs => :fix_max_age_s)
          AND ST_DWithin(
                  p.location,
                  (SELECT footprint FROM sar_scenes WHERE id = :scene_id),
                  :candidate_pad_m)
        ORDER BY p.mmsi, abs(EXTRACT(EPOCH FROM (p.time - CAST(:sensed_at AS timestamptz))))
    ),
    dr AS (
        SELECT mmsi, delta_s, sog,
               CASE WHEN sog IS NULL OR cog IS NULL OR sog < :min_sog_kn
                    THEN location
                    ELSE ST_Project(location, sog * :kn_to_ms * lead_s, radians(cog))::geography
               END AS loc,
               :match_radius_m + :azimuth_shift_s * COALESCE(sog, 0) * :kn_to_ms
                   AS gate_m,
               :match_radius_m + :azimuth_shift_s * COALESCE(sog, 0) * :kn_to_ms
                   + :course_err_frac * COALESCE(sog, 0) * :kn_to_ms * abs(lead_s)
                   AS envelope_m
        FROM fix
    )
"""

# Pairs inside the gate; Python does the assignment (combinatorics, not geometry).
MATCH_CANDIDATES = text(
    f"""
    WITH {DR_CTE}
    SELECT d.id AS det_id, dr.mmsi, dr.delta_s,
           ST_Distance(dr.loc, d.location) AS dist_m
    FROM sar_detections d
    JOIN dr ON ST_DWithin(dr.loc, d.location, dr.gate_m)
    WHERE d.scene_id = :scene_id AND NOT d.on_land
    """
)

# Metres outside the nearest envelope — the strength of a dark call.
DARK_MARGINS = text(
    f"""
    WITH {DR_CTE}
    SELECT d.id AS det_id, x.margin_m, x.mmsi AS candidate_mmsi
    FROM sar_detections d
    LEFT JOIN LATERAL (
        SELECT ST_Distance(dr.loc, d.location) - dr.envelope_m AS margin_m, dr.mmsi
        FROM dr
        ORDER BY ST_Distance(dr.loc, d.location) - dr.envelope_m
        LIMIT 1
    ) x ON true
    WHERE d.scene_id = :scene_id AND NOT d.on_land
    """
)

# The null test: run the identical gate over empty water in the same traffic.
CHANCE_MATCH_PROBE = text(
    f"""
    WITH {DR_CTE},
    scene AS (SELECT footprint FROM sar_scenes WHERE id = :scene_id),
    probe AS (
        SELECT ST_Project(d.location, random() * :probe_radius_m, random() * 2 * pi()) AS g
        FROM sar_detections d, generate_series(1, :per_detection)
        WHERE d.scene_id = :scene_id AND NOT d.on_land
    ),
    empty_water AS (
        SELECT probe.g FROM probe, scene
        WHERE ST_Covers(scene.footprint, probe.g)
          AND NOT EXISTS (
              SELECT 1 FROM land_polygons l
              WHERE ST_DWithin(l.geom, probe.g, :probe_land_clearance_m))
          AND NOT EXISTS (
              SELECT 1 FROM sar_detections d2
              WHERE d2.scene_id = :scene_id
                AND ST_DWithin(d2.location, probe.g, :probe_separation_m))
    )
    SELECT count(*) AS probes,
           count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM dr WHERE ST_DWithin(dr.loc, empty_water.g, dr.gate_m)
           )) AS matched
    FROM empty_water
    """
)

# Recall against resolvable hulls only; missing a fishing boat measures the sensor.
RECALL_LARGE = text(
    f"""
    WITH {DR_CTE},
    scene AS (SELECT footprint FROM sar_scenes WHERE id = :scene_id),
    present AS (
        SELECT dr.mmsi, dr.loc
        FROM dr
        JOIN ship_metadata m ON m.mmsi = dr.mmsi
        CROSS JOIN scene
        WHERE ST_Covers(scene.footprint, dr.loc)
          AND dr.sog >= :underway_sog_kn
          AND m.ship_type BETWEEN :type_min AND :type_max
    )
    SELECT count(*) AS total,
           count(*) FILTER (WHERE EXISTS (
               SELECT 1 FROM sar_detections d
               WHERE d.scene_id = :scene_id AND NOT d.on_land
                 AND ST_DWithin(d.location, present.loc, :recall_radius_m)
           )) AS detected
    FROM present
    """
)

# `:state` is CAST at both uses or asyncpg deduces two types for one parameter.
APPLY_MATCH = text(
    """
    UPDATE sar_detections SET
        match_state = CAST(:state AS text),
        is_dark = CASE CAST(:state AS text)
                      WHEN 'dark' THEN true WHEN 'matched' THEN false ELSE NULL END,
        matched_mmsi = :mmsi,
        match_distance_m = :distance_m,
        match_time_delta_s = :time_delta_s,
        dark_margin_m = :margin_m,
        candidate_mmsi = :candidate_mmsi
    WHERE id = :det_id
    """
)

RESET_MATCH = text(
    """
    UPDATE sar_detections
    SET match_state = NULL, is_dark = NULL, matched_mmsi = NULL,
        match_distance_m = NULL, match_time_delta_s = NULL, dark_margin_m = NULL,
        candidate_mmsi = NULL
    WHERE scene_id = :scene_id
    """
)

STORE_SCENE_QUALITY = text(
    """
    UPDATE sar_scenes
    SET chance_match_rate = :chance_match_rate,
        recall_large_total = :recall_large_total,
        recall_large_detected = :recall_large_detected
    WHERE id = :scene_id
    """
)

FUSION_COUNTS = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE is_dark) AS dark,
           count(*) FILTER (WHERE match_state = 'indeterminate') AS indeterminate
    FROM sar_detections
    WHERE scene_id = :scene_id AND NOT on_land
    """
)


async def apply_schema(conn) -> None:
    """Add the fusion-quality columns. Additive so analyzed scenes survive."""
    for statement in ADD_FUSION_COLUMNS:
        await conn.execute(statement)


async def insert_detections(
    session: AsyncSession, scene_id: str, detections: list[GeoDetection]
) -> None:
    """Replace the scene's detections (idempotent across re-runs)."""
    await session.execute(
        text("DELETE FROM sar_detections WHERE scene_id = :scene_id"),
        {"scene_id": scene_id},
    )
    if not detections:
        return
    await session.execute(
        INSERT_DETECTION,
        [
            {
                "scene_id": scene_id,
                "lon": d.lon,
                "lat": d.lat,
                "confidence": d.confidence,
                "bucket": d.bucket,
            }
            for d in detections
        ],
    )


def _dr_params(scene_id: str, sensed_at: datetime, settings) -> dict:
    return {
        "scene_id": scene_id,
        "sensed_at": sensed_at,
        "fix_max_age_s": settings.ais_fix_max_age_s,
        "candidate_pad_m": CANDIDATE_PAD_M,
        "min_sog_kn": MIN_SOG_FOR_DR_KN,
        "kn_to_ms": KNOTS_TO_MS,
        "match_radius_m": settings.match_radius_m,
        "azimuth_shift_s": settings.sar_azimuth_shift_s,
        "course_err_frac": settings.dr_course_err_frac,
    }


async def measure_chance_match(
    session: AsyncSession, scene_id: str, sensed_at: datetime, settings,
    detection_count: int,
) -> float | None:
    """Fraction of empty water this scene's gate would call "matched"."""
    if detection_count == 0:
        return None
    per_detection = max(1, PROBE_TARGET // detection_count)
    row = (
        await session.execute(
            CHANCE_MATCH_PROBE,
            _dr_params(scene_id, sensed_at, settings)
            | {
                "per_detection": per_detection,
                "probe_radius_m": PROBE_RADIUS_M,
                "probe_separation_m": PROBE_MIN_SEPARATION_M,
                "probe_land_clearance_m": max(settings.land_mask_buffer_m, 300.0),
            },
        )
    ).mappings().one()
    if not row["probes"]:
        return None
    return row["matched"] / row["probes"]


async def measure_recall(
    session: AsyncSession, scene_id: str, sensed_at: datetime, settings
) -> tuple[int | None, int | None]:
    """(large vessels AIS puts in the footprint, how many the detector found)."""
    row = (
        await session.execute(
            RECALL_LARGE,
            _dr_params(scene_id, sensed_at, settings)
            | {
                "underway_sog_kn": 1.0,
                "type_min": LARGE_VESSEL_TYPE_MIN,
                "type_max": LARGE_VESSEL_TYPE_MAX,
                "recall_radius_m": RECALL_RADIUS_M,
            },
        )
    ).mappings().one()
    return row["total"], row["detected"]


async def fuse_scene(
    session: AsyncSession,
    scene_id: str,
    sensed_at: datetime,
    *,
    settings,
    fused: bool = True,
) -> dict:
    """Clip to footprint, then dead-reckon, assign and classify for fused ROIs.

    `dark` is None for survey ROIs — with no AIS the number would be meaningless.
    """
    params = {"scene_id": scene_id}
    await session.execute(CLIP_TO_FOOTPRINT, params)
    await session.execute(RESET_MATCH, params)

    if not fused:
        row = (await session.execute(FUSION_COUNTS, params)).mappings().one()
        return {
            "total": row["total"], "dark": None, "indeterminate": None,
            "chance_match_rate": None, "discriminating": None,
            "recall_large_total": None, "recall_large_detected": None,
        }

    dr_params = _dr_params(scene_id, sensed_at, settings)
    candidates = [
        MatchCandidate(
            det_id=r["det_id"], mmsi=r["mmsi"],
            distance_m=r["dist_m"], time_delta_s=r["delta_s"],
        )
        for r in (await session.execute(MATCH_CANDIDATES, dr_params)).mappings()
    ]
    assigned = assign_one_to_one(candidates)
    margins = {
        r["det_id"]: (r["margin_m"], r["candidate_mmsi"])
        for r in (await session.execute(DARK_MARGINS, dr_params)).mappings()
    }

    chance = await measure_chance_match(
        session, scene_id, sensed_at, settings, detection_count=len(margins)
    )
    # Unmeasurable is not good: with no probes, dark calls are withheld.
    discriminating = chance is not None and chance <= settings.max_chance_match_rate

    for det_id, (margin_m, nearest_mmsi) in margins.items():
        state = classify(det_id, assigned, margin_m, discriminating)
        match = assigned.get(det_id)
        await session.execute(
            APPLY_MATCH,
            {
                "det_id": det_id,
                "state": state,
                "mmsi": match.mmsi if match else None,
                "distance_m": match.distance_m if match else None,
                "time_delta_s": match.time_delta_s if match else None,
                "margin_m": margin_m,
                "candidate_mmsi": nearest_mmsi if state == "indeterminate" and margin_m is not None and margin_m <= 0 else None,
            },
        )

    recall_total, recall_detected = await measure_recall(
        session, scene_id, sensed_at, settings
    )
    await session.execute(
        STORE_SCENE_QUALITY,
        {
            "scene_id": scene_id,
            "chance_match_rate": chance,
            "recall_large_total": recall_total,
            "recall_large_detected": recall_detected,
        },
    )

    row = (await session.execute(FUSION_COUNTS, params)).mappings().one()
    return {
        "total": row["total"],
        "dark": row["dark"],
        "indeterminate": row["indeterminate"],
        "chance_match_rate": chance,
        "discriminating": discriminating,
        "recall_large_total": recall_total,
        "recall_large_detected": recall_detected,
    }
