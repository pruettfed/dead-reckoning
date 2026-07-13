"""Analysis orchestration: catalog search → pixel fetch → detection → fusion.

One analysis runs per ROI at a time. Results persist in sar_scenes /
sar_detections, so re-requesting a processed scene serves from the DB (0 PU).
Only `fetch_scene_pixels` spends PU; every guard runs before it.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import sources
from app.config import get_settings
from app.database import SessionLocal
from app.detect import Detector, run_detection
from app.fusion import coverage_ok, fuse_scene, insert_detections
from app.rois import ROI
from app.sar import SarScene, _bbox_to_polygon_wkt, fetch_scene_pixels, search_scenes

logger = logging.getLogger(__name__)

SOURCE = "sar_sentinel1"
SEARCH_WINDOW_DAYS = 3
NEXT_PASS_LOOKBACK_DAYS = 14
NEXT_PASS_CACHE_SECONDS = 600

_in_flight: dict[str, asyncio.Task] = {}
_next_pass_cache: dict[str, tuple[float, dict]] = {}


class NoEligibleScene(Exception):
    """No recent scene falls inside the AIS buffer's correlation window."""


def pick_scene(
    scenes: list[SarScene], min_ais_time: datetime | None, window_hours: float
) -> SarScene | None:
    """Newest scene whose ±window is fully covered by the AIS buffer."""
    eligible = [s for s in scenes if coverage_ok(s.sensed_at, min_ais_time, window_hours)]
    return max(eligible, key=lambda s: s.sensed_at, default=None)


def estimate_next_pass(sensed_times: list[datetime], now: datetime) -> datetime | None:
    """Median interval between recent passes, rolled forward past `now`.

    Deduplicates timestamps first — the catalog lists multiple products
    (e.g. standard + COG) for one acquisition.
    """
    times = sorted(set(sensed_times))
    if len(times) < 3:
        return None
    interval = statistics.median(
        (b - a).total_seconds() for a, b in zip(times, times[1:])
    )
    if interval <= 0:
        return None
    expected = times[-1]
    while expected <= now:
        expected += timedelta(seconds=interval)
    return expected


def footprint_to_ewkt(footprint_wkt: str | None, bbox: tuple[float, float, float, float]) -> str:
    """CDSE OData footprint (`geography'SRID=4326;POLYGON(...)'`) → plain EWKT.

    Falls back to the ROI bbox when the catalog returned no footprint.
    """
    if footprint_wkt:
        wkt = footprint_wkt.split(";", 1)[-1].strip().rstrip("'")
        return f"SRID=4326;{wkt}"
    return f"SRID=4326;{_bbox_to_polygon_wkt(bbox)}"


def is_in_flight(roi_name: str) -> bool:
    task = _in_flight.get(roi_name)
    return task is not None and not task.done()


# AIS coverage is judged per ROI: data in another region must not greenlight
# fusion here, or every detection would be falsely dark.
MIN_AIS_IN_ROI = text(
    """
    SELECT min(time) FROM ais_positions
    WHERE ST_Within(
        location::geometry,
        ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
    )
    """
)


async def find_target_scene(roi: ROI) -> tuple[SarScene, str | None]:
    """Newest analyzable scene for the ROI and its current DB status (None if new).

    Free (catalog + DB only). Raises NoEligibleScene when nothing qualifies.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    scenes = await search_scenes(roi.bbox, now - timedelta(days=SEARCH_WINDOW_DAYS), now)
    if not scenes:
        raise NoEligibleScene(
            f"no Sentinel-1 scene over {roi.name!r} in the last {SEARCH_WINDOW_DAYS} days"
        )
    min_lon, min_lat, max_lon, max_lat = roi.bbox
    async with SessionLocal() as session:
        min_ais_time = (
            await session.execute(
                MIN_AIS_IN_ROI,
                {"min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
            )
        ).scalar()
        if min_ais_time is None:
            raise NoEligibleScene(
                f"no AIS positions recorded inside {roi.name!r} — either ingest just "
                "started or AISStream has no receiver coverage there; fusion would "
                "mark every detection falsely dark"
            )
        scene = pick_scene(scenes, min_ais_time, settings.fusion_max_time_delta_hours)
        if scene is None:
            raise NoEligibleScene(
                f"no scene over {roi.name!r} falls inside the AIS buffer "
                f"(oldest AIS in ROI: {min_ais_time.isoformat()}) — let AIS ingest and retry"
            )
        status = (
            await session.execute(
                text("SELECT status FROM sar_scenes WHERE id = :id"), {"id": scene.id}
            )
        ).scalar()
    return scene, status


def start_analysis(roi: ROI, scene: SarScene, detector: Detector) -> None:
    task = asyncio.create_task(_run_analysis(roi, scene, detector), name=f"analysis-{roi.name}")
    _in_flight[roi.name] = task
    task.add_done_callback(lambda _: _in_flight.pop(roi.name, None))


UPSERT_SCENE = text(
    """
    INSERT INTO sar_scenes (id, name, roi, sensed_at, footprint, platform, status)
    VALUES (:id, :name, :roi, :sensed_at, ST_GeogFromText(:footprint), :platform, 'processing')
    ON CONFLICT (id) DO UPDATE SET status = 'processing', error = NULL
    """
)


async def _run_analysis(roi: ROI, scene: SarScene, detector: Detector) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        await session.execute(
            UPSERT_SCENE,
            {
                "id": scene.id,
                "name": scene.name,
                "roi": roi.name,
                "sensed_at": scene.sensed_at,
                "footprint": footprint_to_ewkt(scene.footprint_wkt, roi.bbox),
                "platform": scene.platform,
            },
        )
        await session.commit()

    try:
        chip = await fetch_scene_pixels(scene, roi.bbox)
        sources.mark_connected(SOURCE)
        sources.mark_message(SOURCE)
        detections = await asyncio.to_thread(run_detection, chip, detector)
        async with SessionLocal() as session:
            await insert_detections(session, scene.id, detections)
            counts = await fuse_scene(
                session,
                scene.id,
                scene.sensed_at,
                max_distance_m=settings.fusion_max_distance_m,
                window_hours=settings.fusion_max_time_delta_hours,
            )
            await session.execute(
                text(
                    "UPDATE sar_scenes SET status = 'processed', processed_at = now() "
                    "WHERE id = :id"
                ),
                {"id": scene.id},
            )
            await session.commit()
        logger.info(
            "analysis %s scene %s: %s detections, %s dark",
            roi.name, scene.name, counts["total"], counts["dark"],
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("analysis failed for %s scene %s", roi.name, scene.name)
        sources.mark_error(SOURCE, str(exc))
        async with SessionLocal() as session:
            await session.execute(
                text("UPDATE sar_scenes SET status = 'failed', error = :error WHERE id = :id"),
                {"id": scene.id, "error": str(exc)[:500]},
            )
            await session.commit()


async def next_pass_info(roi: ROI) -> dict:
    """Latest + expected pass times from the free catalog; cached 10 min per ROI."""
    cached = _next_pass_cache.get(roi.name)
    if cached and time.monotonic() - cached[0] < NEXT_PASS_CACHE_SECONDS:
        return cached[1]

    now = datetime.now(tz=timezone.utc)
    scenes = await search_scenes(roi.bbox, now - timedelta(days=NEXT_PASS_LOOKBACK_DAYS), now)
    sensed_times = [s.sensed_at for s in scenes]
    expected = estimate_next_pass(sensed_times, now)
    info = {
        "latest_scene_sensed_at": max(sensed_times).isoformat() if sensed_times else None,
        "next_expected_at": expected.isoformat() if expected else None,
    }
    _next_pass_cache[roi.name] = (time.monotonic(), info)
    return info
