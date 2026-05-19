"""In-memory health state for ingestion sources.

Generic, source-keyed (`"ais"` today, `"optical_*"` later). State is held in a
module-level dict; helpers mutate it, `snapshot()` reads it for `/api/health`.
No I/O, no DB — surviving a restart is intentionally out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import get_settings


@dataclass
class SourceState:
    name: str
    state: str = "disconnected"  # connected | disconnected | error
    last_message_at: datetime | None = None
    connected_since: datetime | None = None
    reconnect_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    has_ever_connected: bool = field(default=False, repr=False)


_STATE: dict[str, SourceState] = {}


def _get_or_create(source: str) -> SourceState:
    if source not in _STATE:
        _STATE[source] = SourceState(name=source)
    return _STATE[source]


def mark_connected(source: str) -> None:
    s = _get_or_create(source)
    s.state = "connected"
    s.connected_since = datetime.now(tz=timezone.utc)
    if s.has_ever_connected:
        s.reconnect_count += 1
    s.has_ever_connected = True


def mark_message(source: str) -> None:
    s = _get_or_create(source)
    s.last_message_at = datetime.now(tz=timezone.utc)


def mark_disconnected(source: str, reason: str | None = None) -> None:
    s = _get_or_create(source)
    s.state = "disconnected"
    s.connected_since = None
    if reason:
        s.last_error = reason


def mark_error(source: str, reason: str) -> None:
    s = _get_or_create(source)
    s.error_count += 1
    s.last_error = reason


def snapshot(
    stale_after: float | None = None,
    _now: datetime | None = None,
) -> dict[str, dict]:
    """Return a JSON-serializable view of all source states.

    `stale_after` overrides the configured threshold (tests pass this explicitly).
    `_now` overrides the wall clock (also for tests).
    """
    if stale_after is None:
        stale_after = get_settings().source_stale_after_seconds
    now = _now or datetime.now(tz=timezone.utc)

    out: dict[str, dict] = {}
    for name, s in _STATE.items():
        lag = (now - s.last_message_at).total_seconds() if s.last_message_at else None
        derived = s.state
        if derived == "connected" and lag is not None and lag > stale_after:
            derived = "stale"
        out[name] = {
            "state": derived,
            "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            "lag_seconds": round(lag, 1) if lag is not None else None,
            "connected_since": s.connected_since.isoformat() if s.connected_since else None,
            "reconnect_count": s.reconnect_count,
            "error_count": s.error_count,
            "last_error": s.last_error,
        }
    return out


def _reset_for_tests() -> None:
    _STATE.clear()
