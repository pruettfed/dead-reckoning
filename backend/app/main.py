import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models  # noqa: F401  (registers models on Base.metadata)
from app import (
    devtools,
    failures,
    fusion,
    ingest,
    landmask,
    pipeline,
    schemas,
    scheduler,
    sources,
)
from app.config import Settings, get_settings
from app.database import Base, SessionLocal, engine, get_session
from app.detect import DetectorSpec
from app.flags import flag_for_mmsi
from app.ingest import run_ingest, run_retention
from app.middleware import RateLimitMiddleware, SecurityHeadersMiddleware, TrustedHostMiddleware
from app.rois import ROI, ROIS, get_roi
from app.scheduler import run_scheduler
from app.security import check_admin_key
from app.spa import mount_spa

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


REAP_INTERRUPTED = text(
    "UPDATE sar_scenes SET status = 'failed', error = 'interrupted by restart' "
    "WHERE status = 'processing'"
)


# A hosting platform has no depends_on, so a cold deploy can start before Postgres does.
DB_CONNECT_ATTEMPTS = 10
DB_CONNECT_BACKOFF = 3.0


async def _wait_for_database() -> None:
    for attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if attempt == DB_CONNECT_ATTEMPTS:
                raise
            # Some connect failures (TimeoutError in particular) stringify to
            # nothing — the exception type is the only diagnostic signal then.
            logger.warning(
                "database not ready (attempt %d/%d): %s: %s",
                attempt,
                DB_CONNECT_ATTEMPTS,
                type(exc).__name__,
                sources.redact(str(exc)) or "(no message)",
            )
            await asyncio.sleep(DB_CONNECT_BACKOFF)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await _wait_for_database()
    async with engine.begin() as conn:
        # A managed Postgres won't have this pre-installed like the postgis image does.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        await landmask.apply_schema(conn)
        await fusion.apply_schema(conn)
        await ingest.apply_schema(conn)
        loaded = await landmask.load_bundled_polygons(conn)
        # In-flight analyses live only in `pipeline._in_flight`, so any restart
        # orphans their rows. Nothing is retrying them; say so.
        await conn.execute(REAP_INTERRUPTED)
    if loaded:
        # First boot with coastline data: re-mask any detections analyzed
        # before it existed, same as scripts/load_land.py does after a load.
        async with SessionLocal() as session:
            masked = await landmask.mark_land_detections(session, settings.land_mask_buffer_m)
            await session.commit()
        logger.info("loaded %d bundled land polygons, masked %d existing detection(s)", loaded, masked)
    sources.mark_disconnected(pipeline.SOURCE)  # list the SAR source in /api/health from boot
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(run_ingest(stop), name="ais-ingest"),
        asyncio.create_task(run_retention(stop), name="ais-retention"),
        asyncio.create_task(run_scheduler(stop), name="sar-scheduler"),
    ]
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await engine.dispose()


# Production publishes no schema: /docs, /redoc and /openapi.json otherwise
# advertise the admin routes and the header name that guards them.
app = FastAPI(
    title="Dark Vessel Detection API",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)
# require_devtools_key reads settings from here rather than the module global,
# so the request-time environment check is testable.
app.state.settings = settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Nothing in the browser client sends credentials: apiGet uses bare fetch,
    # which defaults to same-origin. Off, so a listed origin is not automatically
    # a fully privileged one.
    allow_credentials=False,
    # The browser client is read-only. The dev router needs DELETE, so the
    # permissive list survives only outside production.
    allow_methods=["GET", "OPTIONS"] if settings.is_production else ["*"],
    allow_headers=["Content-Type"] if settings.is_production else ["*"],
)
# Starlette makes the last-registered middleware outermost, so headers must wrap
# the rate limiter — otherwise a 429 goes out with no security headers at all.
app.add_middleware(RateLimitMiddleware)
if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(SecurityHeadersMiddleware, production=settings.is_production)

devtools.register_devtools(app, settings)


@app.get("/api/health", response_model=schemas.Health)
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    # Readiness, not just liveness — a bad DATABASE_URL used to still report "ok".
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        logger.exception("health check: database unreachable")
        database = "error"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "sources": sources.snapshot(),
    }


@app.get("/api/rois", response_model=list[schemas.Roi])
async def list_rois() -> list[dict]:
    return [
        {
            "name": roi.name,
            "label": roi.label,
            "blurb": roi.blurb,
            "passes_per_month": roi.passes_per_month,
            "ais_bbox": list(roi.ais_bbox),
            "sar_bbox": list(roi.sar_bbox),
            "mode": roi.mode,
        }
        for roi in ROIS.values()
    ]


VESSEL_COUNT_QUERY = text(
    """
    SELECT COUNT(DISTINCT mmsi) AS count
    FROM ais_positions
    WHERE time > now() - make_interval(mins => :minutes)
      AND ST_Within(
          location::geometry,
          ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
      )
    """
)


@app.get(
    "/api/vessels/count",
    response_model=schemas.VesselCount,
    summary="Count vessels with a position update in the given ROI within VESSEL_ACTIVE_MINUTES",
)
async def vessel_count(
    session: Annotated[AsyncSession, Depends(get_session)],
    roi: str = Query(default="north_taiwan"),
) -> dict:
    try:
        roi_obj = get_roi(roi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    min_lon, min_lat, max_lon, max_lat = roi_obj.ais_bbox
    row = (
        await session.execute(
            VESSEL_COUNT_QUERY,
            {"minutes": settings.vessel_active_minutes, "min_lon": min_lon, "min_lat": min_lat, "max_lon": max_lon, "max_lat": max_lat},
        )
    ).mappings().one()
    return {"count": row["count"]}


def _with_flag(row: dict, mmsi: int | None) -> dict:
    """Add the vessel's flag state, resolved from its MMSI.
    """
    flag = flag_for_mmsi(mmsi) if mmsi is not None else None
    return row | {
        "flag_iso2": flag.iso2 if flag else None,
        "flag_country": flag.country if flag else None,
    }


VESSELS_QUERY = text(
    """
    SELECT DISTINCT ON (p.mmsi)
        p.mmsi,
        p.time,
        ST_Y(p.location::geometry) AS lat,
        ST_X(p.location::geometry) AS lon,
        p.sog,
        p.cog,
        p.nav_status,
        m.ship_name,
        m.ship_type,
        m.callsign
    FROM ais_positions p
    LEFT JOIN ship_metadata m ON m.mmsi = p.mmsi
    WHERE p.time BETWEEN CAST(:at AS timestamptz) - make_interval(mins => :minutes)
                     AND CAST(:at AS timestamptz) + make_interval(mins => :minutes)
      AND ST_Within(
          p.location::geometry,
          ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
      )
    ORDER BY p.mmsi, abs(EXTRACT(EPOCH FROM (p.time - CAST(:at AS timestamptz))))
    """
)


@app.get("/api/vessels", response_model=list[schemas.Vessel])
async def list_vessels(
    session: Annotated[AsyncSession, Depends(get_session)],
    at: datetime | None = Query(default=None, description="ISO-8601; defaults to now (UTC)"),
    roi: str = Query(default="north_taiwan"),
) -> list[dict]:
    try:
        roi_obj = get_roi(roi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    when = at or datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    min_lon, min_lat, max_lon, max_lat = roi_obj.ais_bbox
    rows = (
        await session.execute(
            VESSELS_QUERY,
            {
                "at": when,
                "minutes": settings.vessel_active_minutes,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        )
    ).mappings().all()
    return [_with_flag(dict(r), r["mmsi"]) for r in rows]


TRACK_QUERY = text(
    """
    SELECT
        p.time,
        ST_Y(p.location::geometry) AS lat,
        ST_X(p.location::geometry) AS lon,
        p.sog,
        p.cog,
        m.ship_name,
        m.ship_type,
        m.callsign
    FROM ais_positions p
    LEFT JOIN ship_metadata m ON m.mmsi = p.mmsi
    WHERE p.mmsi = :mmsi
      AND p.time > now() - make_interval(hours => :hours)
    ORDER BY p.time ASC
    """
)


# A window longer than retention can only return the same rows.
MAX_TRACK_HOURS = 24 * settings.ais_retention_days


@app.get("/api/vessels/{mmsi}/track", response_model=list[schemas.TrackPoint])
async def vessel_track(
    mmsi: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    # Bounded by validation, not clamped after — was ge=1 with a silent min().
    hours: int = Query(default=12, ge=1, le=MAX_TRACK_HOURS),
) -> list[dict]:
    rows = (
        await session.execute(TRACK_QUERY, {"mmsi": mmsi, "hours": hours})
    ).mappings().all()
    # Track rows carry no mmsi column — it's the path param, and constant for
    # the whole track. Repeated per row to match the shape ship_name/ship_type/
    # callsign already have here.
    return [_with_flag(dict(r), mmsi) for r in rows]


# Same MMSI seen across scenes. Scenes persist indefinitely while AIS positions
# prune at AIS_RETENTION_DAYS, so this reaches much further back than a track.
SIGHTINGS_QUERY = text(
    """
    SELECT d.id AS detection_id,
           d.scene_id,
           s.roi,
           s.sensed_at,
           d.match_state,
           d.is_dark,
           d.confidence,
           COALESCE(d.matched_mmsi = :mmsi, false) AS matched
    FROM sar_detections d
    JOIN sar_scenes s ON s.id = d.scene_id
    WHERE (d.matched_mmsi = :mmsi OR d.candidate_mmsi = :mmsi)
      AND NOT d.on_land
    ORDER BY s.sensed_at DESC
    LIMIT :limit
    """
)


def _sighting(row: Mapping[str, Any]) -> dict:
    roi = row["roi"]
    known = ROIS.get(roi)
    return {
        "detection_id": row["detection_id"],
        "scene_id": row["scene_id"],
        "roi": roi,
        "label": known.label if known else roi,
        "sensed_at": row["sensed_at"],
        "match_state": row["match_state"],
        "is_dark": row["is_dark"],
        "confidence": row["confidence"],
        "matched": row["matched"],
    }


@app.get(
    "/api/vessels/{mmsi}/sightings",
    response_model=list[schemas.Sighting],
    summary="Every SAR detection this MMSI matched or was a candidate for, newest first (free)",
)
async def vessel_sightings(
    mmsi: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    rows = (
        await session.execute(SIGHTINGS_QUERY, {"mmsi": mmsi, "limit": limit})
    ).mappings().all()
    return [_sighting(r) for r in rows]


async def require_analysis_key(
    request: Request,
    x_analysis_key: Annotated[str | None, Header()] = None,
) -> None:
    """Re-check the environment per request, then the key — same two-lock shape as devtools."""
    request_settings: Settings = request.app.state.settings
    if request_settings.is_production:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        check_admin_key(x_analysis_key, request_settings.analysis_api_key)
    except HTTPException:
        client = request.client.host if request.client else "unknown"
        logger.warning("analysis auth rejected from %s for %s", client, request.url.path)
        raise


def _resolve_roi(roi: str) -> ROI:
    try:
        return get_roi(roi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# Production has no PU-spending HTTP surface at all. A key that can spend money
# should not be reachable over the network from the internet: this endpoint
# bypasses PU_MONTHLY_CEILING, and a scene that fails *after* its pixel fetch can
# be retried indefinitely, each retry a fresh spend (the scheduler is protected
# from that by scene_has_pu_spend; this path never was). Forcing a run in
# production is a shell action — scripts/analyze.py — not a request.
ops_router = APIRouter()


@ops_router.post(
    "/api/analysis/{roi}",
    dependencies=[Depends(require_analysis_key)],
    summary="Non-production only: analyze the latest Sentinel-1 pass over an ROI (spends PU)",
)
async def trigger_analysis(roi: str, response: Response) -> dict:
    roi_obj = _resolve_roi(roi)
    if pipeline.is_in_flight(roi_obj.name):
        raise HTTPException(status_code=409, detail=f"analysis already running for {roi_obj.name!r}")
    if not (settings.cdse_client_id and settings.cdse_client_secret):
        raise HTTPException(status_code=503, detail="CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not configured")
    # Presence check only; the model is loaded in the detection subprocess, not
    # here. Answering 503 before spending anything is the point.
    if not os.path.exists(settings.sar_model_path):
        raise HTTPException(
            status_code=503,
            detail=f"model checkpoint not found at {settings.sar_model_path!r}",
        )
    spec = DetectorSpec(
        model_path=settings.sar_model_path,
        conf_threshold=settings.detection_conf_threshold,
    )
    try:
        scene, status = await pipeline.find_target_scene(roi_obj)
    except pipeline.NoEligibleScene as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if status == "processed":
        return {"scene_id": scene.id, "status": "processed"}  # cached result, 0 PU
    pipeline.start_analysis(roi_obj, scene, spec)
    response.status_code = 202
    return {"scene_id": scene.id, "status": "processing"}


def register_ops(app: FastAPI, settings: Settings) -> bool:
    """Register the PU-spending trigger, except in production.

    Returns whether it was registered. Mirrors devtools.register_devtools so
    both gates are decidable without a database.
    """
    if settings.is_production:
        return False
    app.include_router(ops_router)
    return True


register_ops(app, settings)


SCENES_QUERY = text(
    """
    SELECT s.id, s.name, s.roi, s.sensed_at, s.platform, s.status, s.processed_at, s.error,
           ST_AsGeoJSON(s.footprint) AS footprint,
           s.imaged_bbox,
           s.overview_png IS NOT NULL AS has_overview,
           s.chance_match_rate, s.recall_large_total, s.recall_large_detected,
           count(d.id) FILTER (WHERE NOT d.on_land) AS detection_count,
           count(d.id) FILTER (WHERE d.is_dark) AS dark_count,
           count(d.id) FILTER (WHERE d.match_state = 'indeterminate') AS indeterminate_count,
           count(d.id) FILTER (WHERE d.on_land) AS land_count
    FROM sar_scenes s
    LEFT JOIN sar_detections d ON d.scene_id = s.id
    WHERE s.roi = :roi
    GROUP BY s.id
    ORDER BY s.sensed_at DESC
    LIMIT :limit
    """
)


@app.get("/api/scenes", response_model=list[schemas.Scene])
async def list_scenes(
    session: Annotated[AsyncSession, Depends(get_session)],
    roi: str = Query(default="north_taiwan"),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict]:
    roi_obj = _resolve_roi(roi)
    rows = (
        await session.execute(SCENES_QUERY, {"roi": roi_obj.name, "limit": limit})
    ).mappings().all()
    return [
        # `error` is arbitrary exception text; `failure_reason` is what ships. See failures.py.
        {k: v for k, v in r.items() if k != "error"}
        | {
            "footprint": json.loads(r["footprint"]),
            "failure_reason": failures.classify(r["error"]),
        }
        for r in rows
    ]


DETECTIONS_QUERY = text(
    """
    SELECT d.id,
           ST_Y(d.location::geometry) AS lat,
           ST_X(d.location::geometry) AS lon,
           d.confidence, d.confidence_bucket, d.is_dark, d.match_state, d.on_land,
           d.matched_mmsi, d.match_distance_m, d.match_time_delta_s, d.dark_margin_m,
           d.candidate_mmsi,
           m.ship_name, m.ship_type, m.callsign,
           -- Named separately from ship_name: an indeterminate detection is not
           -- identified, so the candidate's name is a lead, never an identity.
           c.ship_name AS candidate_name
    FROM sar_detections d
    LEFT JOIN ship_metadata m ON m.mmsi = d.matched_mmsi
    LEFT JOIN ship_metadata c ON c.mmsi = d.candidate_mmsi
    WHERE d.scene_id = :scene_id
      AND (:include_land OR NOT d.on_land)
    ORDER BY d.confidence DESC
    """
)


@app.get("/api/scenes/{scene_id}/detections", response_model=list[schemas.Detection])
async def scene_detections(
    scene_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    # Land-masked hits are rocks and shore structures, not vessels. Kept
    # queryable so the buffer can be audited without a DB shell — widening
    # LAND_MASK_BUFFER_M eventually starts eating berthed ships, and this is
    # how you see it happen.
    include_land: bool = Query(default=False),
) -> list[dict]:
    exists = (
        await session.execute(
            text("SELECT 1 FROM sar_scenes WHERE id = :id"), {"id": scene_id}
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail=f"unknown scene {scene_id!r}")
    rows = (
        await session.execute(
            DETECTIONS_QUERY, {"scene_id": scene_id, "include_land": include_land}
        )
    ).mappings().all()
    # Dark, indeterminate and survey detections matched no vessel, so they have
    # no MMSI and correctly carry no flag.
    return [_with_flag(dict(r), r["matched_mmsi"]) for r in rows]


@app.get(
    "/api/scenes/{scene_id}/overview.png",
    summary="Downsampled SAR chip the analysis ran on; drape over the scene's imaged_bbox",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def scene_overview(
    scene_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    if_modified_since: Annotated[str | None, Header()] = None,
) -> Response:
    row = (
        await session.execute(
            text("SELECT overview_png, processed_at FROM sar_scenes WHERE id = :id"),
            {"id": scene_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown scene {scene_id!r}")
    overview_png, processed_at = row
    if overview_png is None:
        raise HTTPException(
            status_code=404, detail=f"no imagery stored for scene {scene_id!r}"
        )
    # Not `immutable`: a scene_id's imagery is *usually* fixed once analyzed,
    # but scripts/dev_reset.py deletes and re-fetches the same scene_id by
    # design (see CLAUDE.md), so this URL can legitimately serve different
    # bytes later. `Last-Modified` (from `processed_at`, which changes exactly
    # when the image does) lets a long cache stay both cheap and correct: a
    # cache hit costs nothing for `max-age`, and revalidation past that costs
    # one timestamp comparison (a 304, no image bytes) rather than silently
    # serving stale content for the rest of the day.
    # processed_at is NULL for a scene still `processing`, or one that failed
    # after its overview was stored (kept for debugging, see SarCoverageTooLow)
    # — fall back to "now" so those rows just never validate as cached rather
    # than crashing format_datetime on None.
    last_modified = format_datetime(processed_at or datetime.now(timezone.utc), usegmt=True)
    headers = {"Cache-Control": "public, max-age=86400", "Last-Modified": last_modified}
    if if_modified_since == last_modified:
        return Response(status_code=304, headers=headers)
    return Response(content=overview_png, media_type="image/png", headers=headers)


@app.get(
    "/api/analysis/next-pass",
    response_model=schemas.NextPass,
    summary="Latest and expected Sentinel-1 pass times for an ROI (free)",
)
async def analysis_next_pass(
    session: Annotated[AsyncSession, Depends(get_session)],
    roi: str = Query(default="north_taiwan"),
) -> dict:
    roi_obj = _resolve_roi(roi)
    info = await pipeline.next_pass_info(roi_obj)
    last_processed = (
        await session.execute(
            text(
                "SELECT max(processed_at) FROM sar_scenes "
                "WHERE roi = :roi AND status = 'processed'"
            ),
            {"roi": roi_obj.name},
        )
    ).scalar()
    return info | {"last_processed_at": last_processed}


# One grouped query for all regions, read per request rather than cached by the
# scheduler: a region that just finished must stop saying "never analyzed"
# immediately, not at its next sweep.
LAST_PROCESSED_BY_ROI = text(
    "SELECT roi, max(processed_at) AS last_processed_at FROM sar_scenes "
    "WHERE status = 'processed' GROUP BY roi"
)

MOST_RECENT_ANALYSIS = text(
    """
    SELECT s.roi, s.sensed_at, s.processed_at,
           count(d.id) FILTER (WHERE NOT d.on_land) AS detection_count,
           count(d.id) FILTER (WHERE d.is_dark) AS dark_count
    FROM sar_scenes s
    LEFT JOIN sar_detections d ON d.scene_id = s.id
    WHERE s.status = 'processed'
    GROUP BY s.id
    ORDER BY s.processed_at DESC
    LIMIT 1
    """
)


@app.get(
    "/api/analysis/schedule",
    response_model=schemas.Schedule,
    summary="Upcoming automatic analyses across every region, and PU spent this month (free)",
)
async def analysis_schedule(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    last_processed = {
        r.roi: r.last_processed_at
        for r in (await session.execute(LAST_PROCESSED_BY_ROI)).all()
    }
    recent = (await session.execute(MOST_RECENT_ANALYSIS)).mappings().first()
    return {
        # Distinguishes "empty, never started" from "empty, still warming up".
        "scheduler": scheduler.status(),
        # Empty until the scheduler's first sweep lands, or whenever it is
        # disabled — the client renders that state rather than guessing.
        "regions": scheduler.snapshot(last_processed, datetime.now(tz=timezone.utc)),
        # Null until the first analysis completes.
        "most_recent": (
            dict(recent)
            | {
                "label": ROIS[recent["roi"]].label,
                "mode": ROIS[recent["roi"]].mode,
            }
            if recent and recent["roi"] in ROIS
            else None
        ),
        "month_to_date_pu": await pipeline.month_to_date_pu(session),
        "pu_monthly_ceiling": settings.pu_monthly_ceiling,
    }


# Last, so every API route is matched before the SPA catch-all sees a request.
mount_spa(app)
