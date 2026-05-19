"""AIS message parsing and AISStream subscription helpers (pure, no I/O)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.rois import ROI


@dataclass(frozen=True)
class ParsedPosition:
    """A decoded AIS PositionReport, ready to be turned into an AISPosition row."""

    mmsi: int
    time: datetime
    lat: float
    lon: float
    sog: float | None
    cog: float | None
    true_heading: int | None
    nav_status: int | None


@dataclass(frozen=True)
class ParsedShipMetadata:
    """Decoded AIS ShipStaticData — upserted into ship_metadata by MMSI."""

    mmsi: int
    time: datetime
    ship_name: str | None
    ship_type: int | None
    callsign: str | None


# AISStream timestamps look like:
#   "2024-08-30 13:24:32.987532323 +0000 UTC"
# Python's fromisoformat handles up to microseconds and won't accept the
# " UTC" suffix or nanosecond precision, so we normalize first.
_AISSTREAM_TIME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T]"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<frac>\d+))?"
    r"\s*(?P<tz>[+-]\d{4})?"
)


def _parse_aisstream_time(raw: str) -> datetime:
    """Parse AISStream's Go-formatted UTC timestamps."""
    m = _AISSTREAM_TIME.match(raw)
    if not m:
        raise ValueError(f"unrecognized AISStream time: {raw!r}")
    micro = (m.group("frac") or "")[:6].ljust(6, "0") if m.group("frac") else "000000"
    iso = f"{m.group('date')}T{m.group('time')}.{micro}+00:00"
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


def parse_position_report(msg: dict[str, Any]) -> ParsedPosition | None:
    """Decode an AISStream PositionReport envelope.

    Returns None for messages we don't care about or that are missing required
    fields. The WebSocket consumer should treat None as "skip silently".
    """
    if msg.get("MessageType") != "PositionReport":
        return None

    meta = msg.get("MetaData") or {}
    report = ((msg.get("Message") or {}).get("PositionReport")) or {}

    mmsi = meta.get("MMSI") or report.get("UserID")
    lat = meta.get("latitude")
    lon = meta.get("longitude")
    raw_time = meta.get("time_utc")
    if mmsi is None or lat is None or lon is None or not raw_time:
        return None

    try:
        when = _parse_aisstream_time(raw_time)
    except ValueError:
        return None

    return ParsedPosition(
        mmsi=int(mmsi),
        time=when,
        lat=float(lat),
        lon=float(lon),
        sog=_optional_float(report.get("Sog")),
        cog=_optional_float(report.get("Cog")),
        true_heading=_optional_int(report.get("TrueHeading")),
        nav_status=_optional_int(report.get("NavigationalStatus")),
    )


def parse_ship_static_data(msg: dict[str, Any]) -> ParsedShipMetadata | None:
    """Decode an AISStream ShipStaticData envelope.

    Returns None for non-ShipStaticData messages or those missing required fields.
    """
    if msg.get("MessageType") != "ShipStaticData":
        return None

    meta = msg.get("MetaData") or {}
    static = ((msg.get("Message") or {}).get("ShipStaticData")) or {}

    mmsi = meta.get("MMSI") or static.get("UserID")
    raw_time = meta.get("time_utc")
    if mmsi is None or not raw_time:
        return None

    try:
        when = _parse_aisstream_time(raw_time)
    except ValueError:
        return None

    # Name appears in both MetaData.ShipName and Message.ShipStaticData.Name;
    # the inner field tends to be more consistently present.
    name = static.get("Name") or meta.get("ShipName")
    name = name.strip() if isinstance(name, str) else None

    return ParsedShipMetadata(
        mmsi=int(mmsi),
        time=when,
        ship_name=name or None,
        ship_type=_optional_int(static.get("Type")),
        callsign=(static.get("CallSign") or "").strip() or None,
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_subscribe_message(api_key: str, rois: Iterable[ROI]) -> dict[str, Any]:
    """Build the AISStream subscribe payload for one or more ROIs.

    AISStream expects bounding-box corners as [[south-west], [north-east]] in
    [latitude, longitude] order — the opposite of GeoJSON.
    """
    boxes = [
        [[min_lat, min_lon], [max_lat, max_lon]]
        for roi in rois
        for min_lon, min_lat, max_lon, max_lat in (roi.bbox,)
    ]
    return {
        "APIKey": api_key,
        "BoundingBoxes": boxes,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
