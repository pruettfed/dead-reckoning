"""The single operator-posted announcement banner.

Read by GET /api/status-message (main.py); written only by
scripts/status_message.py over a shell — there is deliberately no
authenticated HTTP write path, matching how every other ops action in this
project is a shell action, not an API call with a key (see CLAUDE.md).

Always exactly one row, id=1. `post`/`toggle`/`clear` do not commit; the
caller (the CLI) decides when to commit, same convention as devtools.py's
reset_* functions.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

VALID_LEVELS = ("info", "warning", "critical")

_ROW_ID = 1

_UNSET = {"active": False, "message": None, "level": "warning", "updated_at": None}

_SELECT = text(
    "SELECT message, level, active, updated_at FROM status_message WHERE id = :id"
)
_UPSERT = text(
    """
    INSERT INTO status_message (id, message, level, active, updated_at)
    VALUES (:id, :message, :level, true, now())
    ON CONFLICT (id) DO UPDATE
    SET message = EXCLUDED.message, level = EXCLUDED.level,
        active = true, updated_at = now()
    """
)
_TOGGLE = text(
    "UPDATE status_message SET active = NOT active, updated_at = now() "
    "WHERE id = :id AND message IS NOT NULL "
    "RETURNING message, level, active, updated_at"
)
_CLEAR = text(
    "UPDATE status_message SET message = NULL, level = 'warning', "
    "active = false, updated_at = now() WHERE id = :id"
)


def validate_post(message: str, level: str) -> None:
    """Pure precondition check, run before any DB write."""
    if not message or not message.strip():
        raise ValueError("message must not be blank")
    if level not in VALID_LEVELS:
        raise ValueError(f"level must be one of {VALID_LEVELS}, got {level!r}")


async def get_status(session: AsyncSession) -> dict:
    row = (await session.execute(_SELECT, {"id": _ROW_ID})).mappings().first()
    return dict(row) if row is not None else dict(_UNSET)


async def post(session: AsyncSession, message: str, level: str) -> dict:
    validate_post(message, level)
    await session.execute(_UPSERT, {"id": _ROW_ID, "message": message.strip(), "level": level})
    return await get_status(session)


async def toggle(session: AsyncSession) -> dict:
    row = (await session.execute(_TOGGLE, {"id": _ROW_ID})).mappings().first()
    return dict(row) if row is not None else dict(_UNSET)


async def clear(session: AsyncSession) -> dict:
    await session.execute(_CLEAR, {"id": _ROW_ID})
    return dict(_UNSET)
