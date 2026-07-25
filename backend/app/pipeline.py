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
from app.landmask import mark_land_detections
from app.rois import ROI
from app.sar import (
    PROCESS_WINDOW_BACK,
    PROCESS_WINDOW_FWD,
    SarScene,
    _bbox_to_polygon_wkt,
    chip_overview_png,
    fetch_scene_pixels,
    search_scenes,
)

logger = logging.getLogger(__name__)

SOURCE = "sar_sentinel1"
# Wide enough to reach past a run of grazing passes. Only ~half of passes image
# enough of the box to be usable, and the gap between usable ones runs to 4.5
# days over Hormuz — a 3-day window missed one in ~4 attempts. Fused ROIs are
# still capped by AIS retention, since `eligible_scenes` drops any scene the AIS
# buffer no longer brackets; survey ROIs genuinely use the full window.
SEARCH_WINDOW_DAYS = 7
# A pass must image at least this much of the sar_bbox to be worth fetching.
MIN_FOOTPRINT_COVERAGE = 0.85
NEXT_PASS_LOOKBACK_DAYS = 14
NEXT_PASS_CACHE_SECONDS = 600

_in_flight: dict[str, asyncio.Task] = {}
_next_pass_cache: dict[str, tuple[float, dict]] = {}


class NoEligibleScene(Exception):
    """No recent scene falls inside the AIS buffer's correlation window."""


def eligible_scenes(
    scenes: list[SarScene], min_ais_time: datetime | None, window_hours: float
) -> list[SarScene]:
    """Scenes whose ±window the AIS buffer covers, newest first."""
    return sorted(
        (s for s in scenes if coverage_ok(s.sensed_at, min_ais_time, window_hours)),
        key=lambda s: s.sensed_at,
        reverse=True,
    )


def _footprint_wkts_in_window(scenes: list[SarScene], anchor: SarScene) -> list[str]:
    """Footprints the Process API would mosaic for `anchor`, as plain WKT."""
    return [
        s.footprint_wkt.split(";", 1)[-1].strip().rstrip("'")
        for s in scenes
        if s.footprint_wkt
        and anchor.sensed_at - PROCESS_WINDOW_BACK
        <= s.sensed_at
        <= anchor.sensed_at + PROCESS_WINDOW_FWD
    ]


# Fraction of sar_bbox the mosaicked footprints actually cover. A pass that only
# clips the corner still costs full PU and comes back black, so this runs first.
FOOTPRINT_COVERAGE = text(
    """
    SELECT ST_Area(ST_Intersection(
               ST_Union(ST_GeomFromText(w, 4326)),
               ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)))
         / ST_Area(ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326))
    FROM unnest(CAST(:wkts AS text[])) AS w
    """
)


async def footprint_coverage(
    session, scenes: list[SarScene], anchor: SarScene, bbox: tuple[float, float, float, float]
) -> float:
    """0–1 fraction of `bbox` that fetching `anchor` would actually return imagery for."""
    wkts = _footprint_wkts_in_window(scenes, anchor)
    if not wkts:
        return 0.0
    min_lon, min_lat, max_lon, max_lat = bbox
    value = (
        await session.execute(
            FOOTPRINT_COVERAGE,
            {
                "wkts": wkts,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        )
    ).scalar()
    return float(value or 0.0)


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


def imaged_footprint_wkts(
    window_scenes: list[SarScene], anchor: SarScene, sar_bbox: tuple[float, float, float, float]
) -> list[str]:
    """Footprint WKTs the Process API mosaics for `anchor`; the bbox itself when none.

    Storing the mosaic-window union (clipped to sar_bbox at insert time), not the
    anchor slice, keeps the footprint clip from deleting detections we did image.
    """
    return _footprint_wkts_in_window(window_scenes, anchor) or [_bbox_to_polygon_wkt(sar_bbox)]


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


async def find_target_scene(roi: ROI, *, require_ais: bool = True) -> tuple[SarScene, str | None]:
    """Newest analyzable scene for the ROI and its current DB status (None if new).

    Free (catalog + DB only). Raises NoEligibleScene when nothing qualifies.

    require_ais=True (production) refuses a fused-ROI scene with no AIS to bracket it;
    False takes the newest covering pass regardless (for offline detector benchmarking).
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    scenes = await search_scenes(roi.sar_bbox, now - timedelta(days=SEARCH_WINDOW_DAYS), now)
    if not scenes:
        raise NoEligibleScene(
            f"no Sentinel-1 scene over {roi.name!r} in the last {SEARCH_WINDOW_DAYS} days"
        )
    async with SessionLocal() as session:
        if roi.mode == "survey" or not require_ais:
            # No AIS to bracket the scene against (survey ROI, or benchmark bypass).
            # Detections stay unfused (is_dark NULL), so every pass is a candidate.
            candidates = sorted(scenes, key=lambda s: s.sensed_at, reverse=True)
        else:
            min_lon, min_lat, max_lon, max_lat = roi.ais_bbox
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
            candidates = eligible_scenes(
                scenes, min_ais_time, settings.fusion_max_time_delta_hours
            )
            if not candidates:
                raise NoEligibleScene(
                    f"no scene over {roi.name!r} falls inside the AIS buffer "
                    f"(oldest AIS in ROI: {min_ais_time.isoformat()}) — let AIS ingest and retry"
                )

        # Newest pass that actually images the box. Catalog "intersects" is not
        # enough: a pass clipping one corner costs full PU and returns black.
        scene, best = None, 0.0
        for candidate in candidates:
            covered = await footprint_coverage(session, scenes, candidate, roi.sar_bbox)
            best = max(best, covered)
            if covered >= MIN_FOOTPRINT_COVERAGE:
                scene = candidate
                logger.info(
                    "%s: picked %s covering %.0f%% of sar_bbox",
                    roi.name, candidate.name, covered * 100,
                )
                break
        if scene is None:
            raise NoEligibleScene(
                f"no recent pass covers enough of {roi.name!r}'s sar_bbox "
                f"(best {best * 100:.0f}%, need {MIN_FOOTPRINT_COVERAGE * 100:.0f}%) — "
                "the swath only clips it; wait for a better pass or re-probe the box "
                "with scripts/probe_regions.py"
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
    VALUES (
        :id, :name, :roi, :sensed_at,
        -- Imaged region = mosaic-window slices unioned, clipped to sar_bbox.
        ST_Intersection(
            ST_Union(ARRAY(
                SELECT ST_GeomFromText(w, 4326)
                FROM unnest(CAST(:wkts AS text[])) AS w
            )),
            ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
        )::geography,
        :platform, 'processing'
    )
    ON CONFLICT (id) DO UPDATE SET status = 'processing', error = NULL
    """
)

STORE_OVERVIEW = text(
    """
    UPDATE sar_scenes
    SET imaged_bbox = :imaged_bbox, overview_png = :overview_png
    WHERE id = :id
    """
)


async def _run_analysis(roi: ROI, scene: SarScene, detector: Detector) -> None:
    settings = get_settings()
    # The slices the Process API will mosaic for this pass (free catalog call).
    window = await search_scenes(
        roi.sar_bbox, scene.sensed_at - PROCESS_WINDOW_BACK, scene.sensed_at + PROCESS_WINDOW_FWD
    )
    wkts = imaged_footprint_wkts(window, scene, roi.sar_bbox)
    min_lon, min_lat, max_lon, max_lat = roi.sar_bbox
    async with SessionLocal() as session:
        await session.execute(
            UPSERT_SCENE,
            {
                "id": scene.id,
                "name": scene.name,
                "roi": roi.name,
                "sensed_at": scene.sensed_at,
                "wkts": wkts,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
                "platform": scene.platform,
            },
        )
        await session.commit()

    try:
        chip = await fetch_scene_pixels(scene, roi.sar_bbox)
        sources.mark_connected(SOURCE)
        sources.mark_message(SOURCE)
        # Store the imagery before detection: if detection fails, the frame that
        # was paid for is still there to look at.
        overview = await asyncio.to_thread(chip_overview_png, chip)
        async with SessionLocal() as session:
            await session.execute(
                STORE_OVERVIEW,
                {"id": scene.id, "imaged_bbox": list(chip.bbox), "overview_png": overview},
            )
            await session.commit()
        detections = await asyncio.to_thread(run_detection, chip, detector)
        async with SessionLocal() as session:
            await insert_detections(session, scene.id, detections)
            # Before fusion: a masked detection must never reach the AIS match.
            on_land = await mark_land_detections(
                session, settings.land_mask_buffer_m, scene_id=scene.id
            )
            counts = await fuse_scene(
                session,
                scene.id,
                scene.sensed_at,
                max_distance_m=settings.fusion_max_distance_m,
                window_hours=settings.fusion_max_time_delta_hours,
                fused=roi.mode == "fused",
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
            "analysis %s scene %s: %s detections (%s masked on land), %s",
            roi.name,
            scene.name,
            counts["total"],
            on_land,
            f"{counts['dark']} dark" if roi.mode == "fused" else "unfused (survey ROI)",
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
    scenes = await search_scenes(roi.sar_bbox, now - timedelta(days=NEXT_PASS_LOOKBACK_DAYS), now)
    sensed_times = [s.sensed_at for s in scenes]
    expected = estimate_next_pass(sensed_times, now)
    info = {
        "latest_scene_sensed_at": max(sensed_times).isoformat() if sensed_times else None,
        "next_expected_at": expected.isoformat() if expected else None,
    }
    _next_pass_cache[roi.name] = (time.monotonic(), info)
    return info
