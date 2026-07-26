"""AISStream WebSocket consumer.

Long-running background task that opens a single WebSocket to AISStream,
subscribes to every ROI's bounding box, decodes PositionReport messages,
and batches inserts into PostGIS. DB write failures retry locally so a
transient hiccup doesn't tear down the WebSocket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable

import websockets
from sqlalchemy import text

from app import sources
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
from app.rois import ROIS

log = logging.getLogger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
BATCH_SIZE = 100
FLUSH_INTERVAL_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
RETENTION_INTERVAL_SECONDS = 3600.0  # hourly prune
# Delays between flush retries on transient DB failure. A 200ms Postgres hiccup
# shouldn't cost us a WebSocket reconnect.
FLUSH_RETRY_BACKOFFS = [0.1, 0.5, 2.0]
WS_PING_INTERVAL_SECONDS = 20
WS_PING_TIMEOUT_SECONDS = 20


async def run_ingest(stop: asyncio.Event) -> None:
    """Connect to AISStream, subscribe to all ROIs, persist positions.

    Reconnects with exponential backoff on any failure. Exits cleanly when
    `stop` is set.
    """
    settings = get_settings()
    if not settings.aisstream_api_key:
        log.warning("AISSTREAM_API_KEY not set; AIS ingest disabled")
        return

    sub_msg = json.dumps(build_subscribe_message(settings.aisstream_api_key, ROIS.values()))

    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(
                AISSTREAM_URL,
                ping_interval=WS_PING_INTERVAL_SECONDS,
                ping_timeout=WS_PING_TIMEOUT_SECONDS,
            ) as ws:
                await ws.send(sub_msg)
                sources.mark_connected("ais")
                log.info("connected; subscribed to %d ROIs", len(ROIS))
                backoff = 1.0
                await _consume(ws, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sources.mark_disconnected("ais", reason=str(exc))
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
                sources.mark_message("ais")
                buffer.append(pos)
        elif msg_type == "ShipStaticData":
            meta = parse_ship_static_data(msg)
            if meta is not None:
                sources.mark_message("ais")
                await _upsert_ship_metadata(meta)

        if len(buffer) >= BATCH_SIZE:
            await _flush(buffer)
            buffer = []
            last_flush = time.monotonic()

    if buffer:
        await _flush(buffer)


async def _run_with_retry(operation, label: str) -> bool:
    """Run an async DB operation with bounded retries.

    `operation` is a no-arg coroutine factory — called fresh on each attempt so
    a new SessionLocal is opened (the previous one may be in a poisoned state).
    Returns True on success, False if all attempts failed (batch is dropped).
    """
    attempts = len(FLUSH_RETRY_BACKOFFS) + 1
    for attempt in range(attempts):
        try:
            await operation()
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt == attempts - 1:
                log.error("%s failed after %d attempts: %s", label, attempts, exc)
                sources.mark_error("ais", f"{label}: {exc}")
                return False
            delay = FLUSH_RETRY_BACKOFFS[attempt]
            log.warning("%s attempt %d failed: %s; retrying in %.1fs", label, attempt + 1, exc, delay)
            await asyncio.sleep(delay)
    return False


async def _upsert_ship_metadata(meta: ParsedShipMetadata) -> None:
    async def write() -> None:
        async with SessionLocal() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO ship_metadata (mmsi, ship_name, ship_type, callsign, last_updated)
                    VALUES (:mmsi, :ship_name, :ship_type, :callsign, :last_updated)
                    ON CONFLICT (mmsi) DO UPDATE
                        SET ship_name    = COALESCE(EXCLUDED.ship_name, ship_metadata.ship_name),
                            ship_type    = COALESCE(EXCLUDED.ship_type, ship_metadata.ship_type),
                            callsign     = COALESCE(EXCLUDED.callsign, ship_metadata.callsign),
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

    await _run_with_retry(write, f"ship_metadata upsert mmsi={meta.mmsi}")


async def _flush(rows: Iterable[ParsedPosition]) -> None:
    parsed = list(rows)
    if not parsed:
        return

    async def write() -> None:
        # Build fresh ORM instances each attempt — objects attached to a failed
        # session become detached and reusing them across attempts is fragile.
        async with SessionLocal() as session:
            session.add_all(_to_orm(p) for p in parsed)
            await session.commit()

    if await _run_with_retry(write, f"flush of {len(parsed)} rows"):
        log.info("flushed %d rows", len(parsed))


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
