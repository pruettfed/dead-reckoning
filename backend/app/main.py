import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models  # noqa: F401  (registers models on Base.metadata)
from app.config import get_settings
from app.database import Base, engine, get_session
from app.ingest import run_ingest, run_retention
from app.rois import ROIS, get_roi

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
async def health() -> dict[str, str]:
    return {"status": "ok"}


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


@app.get("/api/vessels/count", summary="Count vessels with a position update in the active ROI within VESSEL_ACTIVE_MINUTES")
async def vessel_count(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    roi = get_roi(settings.active_roi)
    min_lon, min_lat, max_lon, max_lat = roi.bbox
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
) -> list[dict]:
    when = at or datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    roi = get_roi(settings.active_roi)
    min_lon, min_lat, max_lon, max_lat = roi.bbox
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
    hours: int = Query(default=72, ge=1),
) -> list[dict]:
    # Returns all stored positions for the vessel over the trailing `hours` window (min 1,
    # max 24 × AIS_RETENTION_DAYS; default 168h). Silently clamps values above the max.
    max_hours = 24 * settings.ais_retention_days
    hours = min(hours, max_hours)
    rows = (
        await session.execute(TRACK_QUERY, {"mmsi": mmsi, "hours": hours})
    ).mappings().all()
    return [dict(r) for r in rows]
