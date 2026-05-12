"""AISStream WebSocket consumer.

Long-running background task that opens a single WebSocket to AISStream,
subscribes to the active ROI's bounding box, decodes PositionReport messages,
and batches inserts into PostGIS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable

import websockets
from sqlalchemy import text

from app.ais import (
    ParsedPosition,
    ParsedShipMetadata,
    build_subscribe_message,
    parse_position_report,
    parse_ship_static_data,
)
from app.config import get_settings
from app.database import SessionLocal
from app.models import AISPosition, ShipMetadata
from app.rois import get_roi

log = logging.getLogger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
BATCH_SIZE = 100
FLUSH_INTERVAL_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
RETENTION_INTERVAL_SECONDS = 3600.0  # hourly prune 


async def run_ingest(stop: asyncio.Event) -> None:
    """Connect to AISStream, subscribe to the active ROI, persist positions.

    Reconnects with exponential backoff on any failure. Exits cleanly when
    `stop` is set.
    """
    settings = get_settings()
    if not settings.aisstream_api_key:
        log.warning("AISSTREAM_API_KEY not set; AIS ingest disabled")
        return

    roi = get_roi(settings.active_roi)
    sub_msg = json.dumps(build_subscribe_message(settings.aisstream_api_key, roi))

    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(AISSTREAM_URL) as ws:
                await ws.send(sub_msg)
                log.info("connected; subscribed (%s)", roi.name)
                backoff = 1.0
                await _consume(ws, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("AIS ingest error: %s; reconnecting in %.1fs", exc, backoff)
            if await _sleep_or_stop(stop, backoff):
                return
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


async def _consume(ws: websockets.WebSocketClientProtocol, stop: asyncio.Event) -> None:
    buffer: list[ParsedPosition] = []
    last_flush = time.monotonic()

    while not stop.is_set():
        timeout = max(0.05, FLUSH_INTERVAL_SECONDS - (time.monotonic() - last_flush))
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            if buffer:
                await _flush(buffer)
                buffer = []
            last_flush = time.monotonic()
            continue

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        msg_type = msg.get("MessageType")
        if msg_type == "PositionReport":
            pos = parse_position_report(msg)
            if pos is not None:
                # log.debug("position mmsi=%d lat=%.4f lon=%.4f sog=%s", pos.mmsi, pos.lat, pos.lon, pos.sog)
                buffer.append(pos)
        elif msg_type == "ShipStaticData":
            meta = parse_ship_static_data(msg)
            if meta is not None:
                # log.debug("upserted ship metadata mmsi=%d name=%r callsign=%r", meta.mmsi, meta.ship_name, meta.callsign)
                await _upsert_ship_metadata(meta)

        if len(buffer) >= BATCH_SIZE:
            await _flush(buffer)
            buffer = []
            last_flush = time.monotonic()

    if buffer:
        await _flush(buffer)


async def _upsert_ship_metadata(meta: ParsedShipMetadata) -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO ship_metadata (mmsi, ship_name, ship_type, callsign, last_updated)
                VALUES (:mmsi, :ship_name, :ship_type, :callsign, :last_updated)
                ON CONFLICT (mmsi) DO UPDATE
                    SET ship_name    = EXCLUDED.ship_name,
                        ship_type    = EXCLUDED.ship_type,
                        callsign     = EXCLUDED.callsign,
                        last_updated = EXCLUDED.last_updated
                    WHERE EXCLUDED.last_updated > ship_metadata.last_updated
                """
            ),
            {
                "mmsi": meta.mmsi,
                "ship_name": meta.ship_name,
                "ship_type": meta.ship_type,
                "callsign": meta.callsign,
                "last_updated": meta.time,
            },
        )
        await session.commit()


async def _flush(rows: Iterable[ParsedPosition]) -> None:
    payload = [_to_orm(r) for r in rows]
    if not payload:
        return
    async with SessionLocal() as session:
        session.add_all(payload)
        await session.commit()
    log.info("flushed %d rows", len(payload))


def _to_orm(pos: ParsedPosition) -> AISPosition:
    return AISPosition(
        mmsi=pos.mmsi,
        time=pos.time,
        # GeoAlchemy2 accepts EWKT strings for Geography columns.
        location=f"SRID=4326;POINT({pos.lon} {pos.lat})",
        sog=pos.sog,
        cog=pos.cog,
        true_heading=pos.true_heading,
        nav_status=pos.nav_status,
    )


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """Sleep up to `seconds`, returning True if `stop` fired during the wait."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def run_retention(stop: asyncio.Event) -> None:
    """Hourly prune of AIS rows older than `AIS_RETENTION_DAYS`."""
    settings = get_settings()
    days = settings.ais_retention_days
    while not stop.is_set():
        try:
            async with SessionLocal() as session:
                result = await session.execute(
                    text(
                        "DELETE FROM ais_positions "
                        "WHERE time < now() - make_interval(days => :d)"
                    ),
                    {"d": days},
                )
                await session.commit()
                deleted = result.rowcount or 0
            log.info("pruned %d rows older than %d days", deleted, days)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("retention prune failed: %s", exc)
        if await _sleep_or_stop(stop, RETENTION_INTERVAL_SECONDS):
            return
