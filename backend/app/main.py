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
from app import pipeline, sources
from app.config import get_settings
from app.database import Base, engine, get_session
from app.detect import DetectorUnavailable, load_detector
from app.ingest import run_ingest, run_retention
from app.rois import ROI, ROIS, get_roi

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sources.mark_disconnected(pipeline.SOURCE)  # list the SAR source in /api/health from boot
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(run_ingest(stop), name="ais-ingest"),
        asyncio.create_task(run_retention(stop), name="ais-retention"),
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
        {"name": roi.name, "label": roi.label, "bbox": list(roi.bbox)}
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
    roi: str = Query(default="singapore_strait"),
) -> dict:
    try:
        roi_obj = get_roi(roi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    min_lon, min_lat, max_lon, max_lat = roi_obj.bbox
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
        m.ship_name,
        m.ship_type,
        m.callsign
    FROM ais_positions p
    LEFT JOIN ship_metadata m ON m.mmsi = p.mmsi
    WHERE p.time <= :at
      AND p.time > :at - make_interval(mins => :minutes)
      AND ST_Within(
          p.location::geometry,
          ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
      )
    ORDER BY p.mmsi, p.time DESC
    """
)


@app.get("/api/vessels")
async def list_vessels(
    session: Annotated[AsyncSession, Depends(get_session)],
    at: datetime | None = Query(default=None, description="ISO-8601; defaults to now (UTC)"),
    roi: str = Query(default="singapore_strait"),
) -> list[dict]:
    try:
        roi_obj = get_roi(roi)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    when = at or datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    min_lon, min_lat, max_lon, max_lat = roi_obj.bbox
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


SCENES_QUERY = text(
    """
    SELECT s.id, s.name, s.roi, s.sensed_at, s.platform, s.status, s.processed_at, s.error,
           ST_AsGeoJSON(s.footprint) AS footprint,
           count(d.id) AS detection_count,
           count(d.id) FILTER (WHERE d.is_dark) AS dark_count
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
    roi: str = Query(default="singapore_strait"),
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
           d.confidence, d.confidence_bucket, d.is_dark,
           d.matched_mmsi, d.match_distance_m, d.match_time_delta_s,
           m.ship_name, m.ship_type, m.callsign
    FROM sar_detections d
    LEFT JOIN ship_metadata m ON m.mmsi = d.matched_mmsi
    WHERE d.scene_id = :scene_id
    ORDER BY d.confidence DESC
    """
)


@app.get("/api/scenes/{scene_id}/detections")
async def scene_detections(
    scene_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict]:
    exists = (
        await session.execute(
            text("SELECT 1 FROM sar_scenes WHERE id = :id"), {"id": scene_id}
        )
    ).scalar()
    if not exists:
        raise HTTPException(status_code=404, detail=f"unknown scene {scene_id!r}")
    rows = (
        await session.execute(DETECTIONS_QUERY, {"scene_id": scene_id})
    ).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/analysis/next-pass", summary="Latest and expected Sentinel-1 pass times for an ROI (free)")
async def analysis_next_pass(
    session: Annotated[AsyncSession, Depends(get_session)],
    roi: str = Query(default="singapore_strait"),
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
