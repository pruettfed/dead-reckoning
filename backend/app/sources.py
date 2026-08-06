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


# user:password inside any URL — asyncpg and SQLAlchemy both echo the DSN in
# connection errors, and DATABASE_URL is the one secret that arrives already
# embedded in a string we did not build.
_URL_CREDENTIALS = re.compile(r"(?<=://)[^/\s@]+:[^/\s@]+(?=@)")


def redact(reason: str) -> str:
    """Strip configured secrets out of an error string.

    Error text reaches unauthenticated readers by two routes — `last_error` on
    /api/health, and `sar_scenes.error` — so it must stay useful without
    carrying credentials. The AISStream key travels inside the WebSocket
    subscribe payload and the database password inside the DSN; any exception
    echoing either would publish it. Scrub at this one choke point rather than
    suppressing the fields.

    Ordered longest-first so a secret that contains another (or a shared
    prefix) cannot leave a fragment of itself behind.
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
    # The full DSN is redacted above, but asyncpg also reports host/port/user
    # separately and SQLAlchemy rewrites the URL it echoes, so neither
    # necessarily matches settings.database_url verbatim.
    return _URL_CREDENTIALS.sub("***:***", reason)


# Kept as the private name the rest of the module already used.
_redact = redact


def mark_disconnected(source: str, reason: str | None = None) -> None:
    s = _get_or_create(source)
    s.state = "disconnected"
    s.connected_since = None
    if reason:
        s.last_error = _redact(reason)


def mark_error(source: str, reason: str) -> None:
    s = _get_or_create(source)
    s.error_count += 1
    s.last_error = _redact(reason)


def snapshot(
    stale_after: float | None = None,
    _now: datetime | None = None,
    *,
    include_last_error: bool | None = None,
) -> dict[str, dict]:
    """Return a JSON-serializable view of all source states.

    `stale_after` overrides the configured threshold (tests pass this explicitly).
    `_now` overrides the wall clock (also for tests).

    `include_last_error` defaults to "not in production". The field is scrubbed
    by `redact`, but scrubbing is a list of known secrets and the strings are
    arbitrary exception text — CDSE request URLs, SQL fragments, file paths.
    No client renders it, so production publishes the state without it and
    keeps the detail in the logs, where it belongs.
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
        if derived == "connected" and lag is not None and lag > stale_after:
            derived = "stale"
        out[name] = {
            "state": derived,
            "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            "lag_seconds": round(lag, 1) if lag is not None else None,
            "connected_since": s.connected_since.isoformat() if s.connected_since else None,
            "reconnect_count": s.reconnect_count,
            # A count is a health signal; the text is a debugging aid. Keeping
            # the count in production means the UI can still show that
            # something is wrong without publishing what.
            "error_count": s.error_count,
        }
        if include_last_error:
            out[name]["last_error"] = redact(s.last_error) if s.last_error else None
    return out


def _reset_for_tests() -> None:
    _STATE.clear()
