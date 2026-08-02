import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models  # noqa: F401  (registers models on Base.metadata)
from app import fusion, landmask, pipeline, scheduler, sources
from app.config import get_settings
from app.database import Base, engine, get_session
from app.detect import DetectorUnavailable, load_detector
from app.ingest import run_ingest, run_retention
from app.rois import ROI, ROIS, get_roi
from app.scheduler import run_scheduler

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


REAP_INTERRUPTED = text(
    "UPDATE sar_scenes SET status = 'failed', error = 'interrupted by restart' "
    "WHERE status = 'processing'"
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await landmask.apply_schema(conn)
        await fusion.apply_schema(conn)
        # In-flight analyses live only in `pipeline._in_flight`, so any restart
        # orphans their rows. Nothing is retrying them; say so.
        await conn.execute(REAP_INTERRUPTED)
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


app = FastAPI(title="Dark Vessel Detection API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "sources": sources.snapshot()}


@app.get("/api/rois")
async def list_rois() -> list[dict]:
    return [
        {
            "name": roi.name,
            "label": roi.label,
            "blurb": roi.blurb,
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


@app.get("/api/vessels/count", summary="Count vessels with a position update in the given ROI within VESSEL_ACTIVE_MINUTES")
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


@app.get("/api/vessels")
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
    return [dict(r) for r in rows]


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


@app.get("/api/vessels/{mmsi}/track")
async def vessel_track(
    mmsi: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    hours: int = Query(default=12, ge=1),
) -> list[dict]:
    max_hours = 24 * settings.ais_retention_days
    hours = min(hours, max_hours)
    rows = (
        await session.execute(TRACK_QUERY, {"mmsi": mmsi, "hours": hours})
    ).mappings().all()
    return [dict(r) for r in rows]


def check_admin_key(provided: str | None, configured: str | None) -> None:
    if not configured:
        raise HTTPException(status_code=503, detail="analysis disabled: ANALYSIS_API_KEY not configured")
    if not provided or not secrets.compare_digest(provided, configured):
        raise HTTPException(status_code=401, detail="invalid or missing X-Analysis-Key header")


async def require_analysis_key(
    x_analysis_key: Annotated[str | None, Header()] = None,
) -> None:
    check_admin_key(x_analysis_key, settings.analysis_api_key)


def _resolve_roi(roi: str) -> ROI:
    try:
        return get_roi(roi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post(
    "/api/analysis/{roi}",
    dependencies=[Depends(require_analysis_key)],
    summary="Admin-only: analyze the latest Sentinel-1 pass over an ROI (spends PU)",
)
async def trigger_analysis(roi: str, response: Response) -> dict:
    roi_obj = _resolve_roi(roi)
    if pipeline.is_in_flight(roi_obj.name):
        raise HTTPException(status_code=409, detail=f"analysis already running for {roi_obj.name!r}")
    if not (settings.cdse_client_id and settings.cdse_client_secret):
        raise HTTPException(status_code=503, detail="CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not configured")
    try:
        detector = await asyncio.to_thread(
            load_detector, settings.sar_model_path, settings.detection_conf_threshold
        )
    except DetectorUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    try:
        scene, status = await pipeline.find_target_scene(roi_obj)
    except pipeline.NoEligibleScene as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if status == "processed":
        return {"scene_id": scene.id, "status": "processed"}  # cached result, 0 PU
    pipeline.start_analysis(roi_obj, scene, detector)
    response.status_code = 202
    return {"scene_id": scene.id, "status": "processing"}


# Detections cascade via the sar_detections → sar_scenes FK; AIS is untouched.
RESET_ANALYSES = text("DELETE FROM sar_scenes")


@app.delete(
    "/api/analyses",
    dependencies=[Depends(require_analysis_key)],
    summary="Admin-only: delete every SAR scene and detection (AIS data is kept)",
)
async def reset_analyses(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    if pipeline.any_in_flight():
        raise HTTPException(status_code=409, detail="an analysis is running; wait for it to finish")
    result = await session.execute(RESET_ANALYSES)
    await session.commit()
    return {"scenes_deleted": result.rowcount}


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


@app.get("/api/scenes")
async def list_scenes(
    session: Annotated[AsyncSession, Depends(get_session)],
    roi: str = Query(default="north_taiwan"),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[dict]:
    roi_obj = _resolve_roi(roi)
    rows = (
        await session.execute(SCENES_QUERY, {"roi": roi_obj.name, "limit": limit})
    ).mappings().all()
    return [dict(r) | {"footprint": json.loads(r["footprint"])} for r in rows]


DETECTIONS_QUERY = text(
    """
    SELECT d.id,
           ST_Y(d.location::geometry) AS lat,
           ST_X(d.location::geometry) AS lon,
           d.confidence, d.confidence_bucket, d.is_dark, d.match_state, d.on_land,
           d.matched_mmsi, d.match_distance_m, d.match_time_delta_s, d.dark_margin_m,
           d.candidate_mmsi,
           m.ship_name, m.ship_type, m.callsign
    FROM sar_detections d
    LEFT JOIN ship_metadata m ON m.mmsi = d.matched_mmsi
    WHERE d.scene_id = :scene_id
      AND (:include_land OR NOT d.on_land)
    ORDER BY d.confidence DESC
    """
)


@app.get("/api/scenes/{scene_id}/detections")
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
    return [dict(r) for r in rows]


@app.get(
    "/api/scenes/{scene_id}/overview.png",
    summary="Downsampled SAR chip the analysis ran on; drape over the scene's imaged_bbox",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def scene_overview(
    scene_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    row = (
        await session.execute(
            text("SELECT overview_png FROM sar_scenes WHERE id = :id"), {"id": scene_id}
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown scene {scene_id!r}")
    if row[0] is None:
        raise HTTPException(
            status_code=404, detail=f"no imagery stored for scene {scene_id!r}"
        )
    # A scene's imagery never changes once analyzed.
    return Response(
        content=row[0],
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@app.get("/api/analysis/next-pass", summary="Latest and expected Sentinel-1 pass times for an ROI (free)")
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
