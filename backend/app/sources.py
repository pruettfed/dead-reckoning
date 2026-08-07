# Keep track of state of connections for DB, AIS
from __future__ import annotations

import re
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


# asyncpg/SQLAlchemy echo the DSN's user:password on connection errors.
_URL_CREDENTIALS = re.compile(r"(?<=://)[^/\s@]+:[^/\s@]+(?=@)")


def redact(reason: str) -> str:
    """Strip configured secrets out of an error string.

    Reached by /api/health's last_error and sar_scenes.error, both public.
    Longest-first so one secret containing another leaves no fragment.
    """
    settings = get_settings()
    secrets = sorted(
        (
            s
            for s in (
                settings.aisstream_api_key,
                settings.cdse_client_secret,
                settings.cdse_client_id,
                settings.analysis_api_key,
                settings.devtools_api_key,
                settings.database_url,
            )
            if s
        ),
        key=len,
        reverse=True,
    )
    for secret in secrets:
        if secret in reason:
            reason = reason.replace(secret, "***")
    # Also catch a rewritten URL that no longer matches settings.database_url verbatim.
    return _URL_CREDENTIALS.sub("***:***", reason)


_redact = redact  # private alias the rest of the module already used


def mark_disconnected(source: str, reason: str | None = None) -> None:
    s = _get_or_create(source)
    s.state = "disconnected"
    s.connected_since = None
    if reason:
        s.last_error = _redact(reason)


def mark_error(source: str, reason: str, *, state: str | None = None) -> None:
    """Record a failure. `state`, if given, also overwrites the source's state.

    Left `None` by callers (like the AIS DB-flush retry) whose failure says
    nothing about connection health. Pipeline failures pass it explicitly.
    """
    s = _get_or_create(source)
    s.error_count += 1
    s.last_error = _redact(reason)
    if state is not None:
        s.state = state


def snapshot(
    stale_after: float | None = None,
    _now: datetime | None = None,
    *,
    include_last_error: bool | None = None,
) -> dict[str, dict]:
    """Return a JSON-serializable view of all source states.

    `stale_after` overrides the configured threshold (tests pass this explicitly).
    `_now` overrides the wall clock (also for tests).
    `include_last_error` defaults to "not in production" — nothing renders it there.
    """
    settings = get_settings()
    if stale_after is None:
        stale_after = settings.source_stale_after_seconds
    if include_last_error is None:
        include_last_error = not settings.is_production
    now = _now or datetime.now(tz=timezone.utc)

    out: dict[str, dict] = {}
    for name, s in _STATE.items():
        lag = (now - s.last_message_at).total_seconds() if s.last_message_at else None
        derived = s.state
        if derived == "connected":
            # How long the source has been quiet. Normally that is the lag since
            # the last message — but a connection that has never delivered one
            # has no lag to measure, and that is the purest silent failure of
            # all. Fall back to the connect instant so it cannot read "live"
            # indefinitely on a socket that only ever handshook.
            silent_for = lag
            if silent_for is None and s.connected_since is not None:
                silent_for = (now - s.connected_since).total_seconds()
            if silent_for is not None and silent_for > stale_after:
                derived = "stale"
        out[name] = {
            "state": derived,
            "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            "lag_seconds": round(lag, 1) if lag is not None else None,
            "connected_since": s.connected_since.isoformat() if s.connected_since else None,
            "reconnect_count": s.reconnect_count,
            "error_count": s.error_count,  # kept in prod even when last_error is withheld
        }
        if include_last_error:
            out[name]["last_error"] = redact(s.last_error) if s.last_error else None
    return out


def _reset_for_tests() -> None:
    _STATE.clear()
