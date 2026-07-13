"""SAR detections ↔ AIS fusion (PostGIS ST_DWithin, 500 m / ±2 h).

A SAR detection is flagged "dark" if no AIS position matches within
FUSION_MAX_DISTANCE_M / ±FUSION_MAX_TIME_DELTA_HOURS of the scene's acquisition
timestamp. Two rules keep this honest:
  - Clip conclusions to ROI ∩ image-footprint at the single acquisition
    timestamp; a detection outside the imaged footprint is *unobserved*, not dark.
  - Never mosaic passes for the correlation — different passes have different
    times and the vessels have moved. Mosaic only as a visual backdrop.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.detect import GeoDetection


def coverage_ok(
    sensed_at: datetime, min_ais_time: datetime | None, window_hours: float
) -> bool:
    """The AIS buffer must reach back past the scene's full correlation window.

    Fusing a scene older than the buffer would mark every detection falsely dark.
    """
    if min_ais_time is None:
        return False
    return sensed_at - timedelta(hours=window_hours) >= min_ais_time


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

# Nearest AIS position per detection within the distance + time window of this
# one acquisition; no candidate → dark.
FUSE_QUERY = text(
    """
    WITH nearest AS (
        SELECT d.id AS det_id, a.mmsi, a.dist_m, a.time_delta_s
        FROM sar_detections d
        LEFT JOIN LATERAL (
            SELECT p.mmsi,
                   ST_Distance(p.location, d.location) AS dist_m,
                   EXTRACT(EPOCH FROM (p.time - CAST(:sensed_at AS timestamptz))) AS time_delta_s
            FROM ais_positions p
            WHERE p.time BETWEEN CAST(:sensed_at AS timestamptz) - make_interval(secs => :window_s)
                             AND CAST(:sensed_at AS timestamptz) + make_interval(secs => :window_s)
              AND ST_DWithin(p.location, d.location, :max_distance_m)
            ORDER BY p.location <-> d.location
            LIMIT 1
        ) a ON true
        WHERE d.scene_id = :scene_id
    )
    UPDATE sar_detections d
    SET matched_mmsi = n.mmsi,
        match_distance_m = n.dist_m,
        match_time_delta_s = n.time_delta_s,
        is_dark = (n.mmsi IS NULL)
    FROM nearest n
    WHERE d.id = n.det_id
    """
)

FUSION_COUNTS = text(
    """
    SELECT count(*) AS total, count(*) FILTER (WHERE is_dark) AS dark
    FROM sar_detections
    WHERE scene_id = :scene_id
    """
)


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


async def fuse_scene(
    session: AsyncSession,
    scene_id: str,
    sensed_at: datetime,
    *,
    max_distance_m: float,
    window_hours: float,
) -> dict:
    """Clip to footprint, match AIS, flag dark. Returns {"total": n, "dark": n}."""
    params = {"scene_id": scene_id}
    await session.execute(CLIP_TO_FOOTPRINT, params)
    await session.execute(
        FUSE_QUERY,
        {
            "scene_id": scene_id,
            "sensed_at": sensed_at,
            "window_s": window_hours * 3600,
            "max_distance_m": max_distance_m,
        },
    )
    row = (await session.execute(FUSION_COUNTS, params)).mappings().one()
    return {"total": row["total"], "dark": row["dark"]}
