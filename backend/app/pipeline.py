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
from app.detect import DetectorSpec
from app.detect_worker import run_detection_isolated
from app.fusion import coverage_ok, fuse_scene, insert_detections
from app.landmask import mark_land_detections
from app.rois import ROI
from app.sar import (
    PROCESS_WINDOW_BACK,
    PROCESS_WINDOW_FWD,
    SarScene,
    _bbox_to_polygon_wkt,
    chip_overview_png,
    estimate_pu,
    fetch_scene_pixels,
    plan_fetch_grid,
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
# How long a pass sits in CDSE's catalog before its pixels are reliably ready
# from the Process API — the catalog can list a product before it's fully
# processed.
MIN_SCENE_AGE = timedelta(hours=3)
NEXT_PASS_LOOKBACK_DAYS = 14
NEXT_PASS_CACHE_SECONDS = 600

_in_flight: dict[str, asyncio.Task] = {}
_next_pass_cache: dict[str, tuple[float, dict]] = {}


class NoEligibleScene(Exception):
    """No recent scene falls inside the AIS buffer's correlation window."""


class SarCoverageTooLow(Exception):
    """Fetched chip's real dataMask coverage falls short of MIN_FOOTPRINT_COVERAGE."""


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
    """Median interval between recent passes, rolled forward past `now`, plus
    MIN_SCENE_AGE.

    Deduplicates timestamps first — the catalog lists multiple products
    (e.g. standard + COG) for one acquisition. The MIN_SCENE_AGE offset keeps
    this an estimate of when analysis will actually start rather than when the
    satellite flies over.
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
    return expected + MIN_SCENE_AGE


def imaged_footprint_wkts(
    window_scenes: list[SarScene], anchor: SarScene, sar_bbox: tuple[float, float, float, float]
) -> list[str]:
    """Footprint WKTs the Process API mosaics for `anchor`; the bbox itself when none.

    Storing the mosaic-window union (clipped to sar_bbox at insert time), not the
    anchor slice, keeps the footprint clip from deleting detections we did image.
    """
    return _footprint_wkts_in_window(window_scenes, anchor) or [_bbox_to_polygon_wkt(sar_bbox)]


def _fusion_summary(counts: dict) -> str:
    """Fusion verdict for the log; the dark count means nothing without its noise floor."""
    if counts["chance_match_rate"] is None:
        return f"{counts['indeterminate']} indeterminate; chance-match unmeasurable"
    quality = "discriminating" if counts["discriminating"] else "NOT DISCRIMINATING, dark calls withheld"
    return (
        f"{counts['dark']} dark, {counts['indeterminate']} indeterminate; "
        f"chance-match {counts['chance_match_rate']:.1%} ({quality}); "
        f"large-vessel recall {counts['recall_large_detected']}/{counts['recall_large_total']}"
    )


def is_in_flight(roi_name: str) -> bool:
    task = _in_flight.get(roi_name)
    return task is not None and not task.done()


def any_in_flight() -> bool:
    return any(not task.done() for task in _in_flight.values())


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


async def find_target_scene(
    roi: ROI, *, require_ais: bool = True, scenes: list[SarScene] | None = None
) -> tuple[SarScene, str | None]:
    """Newest analyzable scene for the ROI and its current DB status (None if new).

    Free (catalog + DB only). Raises NoEligibleScene when nothing qualifies.

    require_ais=True (production) refuses a fused-ROI scene with no AIS to bracket it;
    False takes the newest covering pass regardless (for offline detector benchmarking).

    `scenes` supplies an already-fetched catalog listing covering at least the last
    SEARCH_WINDOW_DAYS, so the scheduler's per-ROI search can serve both the trigger
    decision and its pass-interval estimate from one call.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    if scenes is None:
        scenes = await search_scenes(
            roi.sar_bbox, now - timedelta(days=SEARCH_WINDOW_DAYS), now
        )
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
        scene, best, too_fresh = None, 0.0, 0
        for candidate in candidates:
            if now - candidate.sensed_at < MIN_SCENE_AGE:
                # Catalog listing isn't pixel availability — see MIN_SCENE_AGE.
                # Older candidates below are unaffected and still considered.
                too_fresh += 1
                continue
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
            reason = (
                f"no recent pass covers enough of {roi.name!r}'s sar_bbox "
                f"(best {best * 100:.0f}%, need {MIN_FOOTPRINT_COVERAGE * 100:.0f}%)"
            )
            if too_fresh:
                reason += f"; {too_fresh} newer pass(es) skipped as under {MIN_SCENE_AGE} old"
            raise NoEligibleScene(
                reason + " — wait for a better pass or re-probe the box "
                "with scripts/probe_regions.py"
            )
        status = (
            await session.execute(
                text("SELECT status FROM sar_scenes WHERE id = :id"), {"id": scene.id}
            )
        ).scalar()
    return scene, status


def start_analysis(roi: ROI, scene: SarScene, spec: DetectorSpec) -> asyncio.Task:
    """Run an analysis in the background. Returns the task so a caller that wants
    to serialize work (the scheduler) can await it; the HTTP path ignores it."""
    task = asyncio.create_task(_run_analysis(roi, scene, spec), name=f"analysis-{roi.name}")
    _in_flight[roi.name] = task
    task.add_done_callback(lambda _: _in_flight.pop(roi.name, None))
    return task


RECORD_PU = text(
    "INSERT INTO pu_ledger (roi, scene_id, pu) VALUES (:roi, :scene_id, :pu)"
)

MONTH_TO_DATE_PU = text(
    "SELECT coalesce(sum(pu), 0) FROM pu_ledger WHERE spent_at >= date_trunc('month', now())"
)

SCENE_HAS_PU_SPEND = text("SELECT exists(SELECT 1 FROM pu_ledger WHERE scene_id = :scene_id)")


async def month_to_date_pu(session) -> float:
    """Processing Units spent this calendar month, against PU_MONTHLY_BUDGET."""
    return float((await session.execute(MONTH_TO_DATE_PU)).scalar() or 0.0)


async def scene_has_pu_spend(session, scene_id: str) -> bool:
    """Whether a pixel fetch was ever attempted for this scene — i.e. whether
    retrying it would cost PU a second time."""
    return bool((await session.execute(SCENE_HAS_PU_SPEND, {"scene_id": scene_id})).scalar())


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


async def _run_analysis(roi: ROI, scene: SarScene, spec: DetectorSpec) -> None:
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
        # Record the spend before the call, not after: PU is consumed by the
        # request, so a fetch that dies mid-flight must still count against the
        # budget. This row is also what stops the scheduler auto-retrying a
        # failure that already cost money.
        async with SessionLocal() as session:
            await session.execute(
                RECORD_PU,
                {
                    "roi": roi.name,
                    "scene_id": scene.id,
                    "pu": estimate_pu(plan_fetch_grid(roi.sar_bbox)),
                },
            )
            await session.commit()
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
        # Ground-truth recheck of footprint_coverage's prediction, now that the
        # pixels are actually in hand — stored above either way, so a rejected
        # scene is still visible for debugging even though it's marked failed.
        if chip.mask is not None:
            real_coverage = float(chip.mask.mean()) / 255.0
            if real_coverage < MIN_FOOTPRINT_COVERAGE:
                raise SarCoverageTooLow(
                    f"fetched chip is only {real_coverage:.0%} real data, need "
                    f"{MIN_FOOTPRINT_COVERAGE:.0%} — the catalog footprint that passed "
                    "the pre-fetch check overstated this pass's real coverage"
                )
        detections = await run_detection_isolated(chip, spec)
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
                settings=settings,
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
        verdict = _fusion_summary(counts) if roi.mode == "fused" else "unfused (survey ROI)"
        logger.info(
            "analysis %s scene %s: %s detections (%s masked on land), %s",
            roi.name,
            scene.name,
            counts["total"],
            on_land,
            verdict,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("analysis failed for %s scene %s", roi.name, scene.name)
        sources.mark_error(SOURCE, str(exc))
        async with SessionLocal() as session:
            await session.execute(
                text("UPDATE sar_scenes SET status = 'failed', error = :error WHERE id = :id"),
                # Redacted before storage, not just before serving — psql reads this too.
                {"id": scene.id, "error": sources.redact(str(exc))[:500]},
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
