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

# Matches models.py StatusMessage.title — keeps the badge compact next to the
# stats it sits alongside in the status bar.
MAX_TITLE_LENGTH = 24

_ROW_ID = 1

_UNSET = {"active": False, "message": None, "level": "warning", "title": None, "updated_at": None}

_SELECT = text(
    "SELECT message, level, title, active, updated_at FROM status_message WHERE id = :id"
)
_UPSERT = text(
    """
    INSERT INTO status_message (id, message, level, title, active, updated_at)
    VALUES (:id, :message, :level, :title, true, now())
    ON CONFLICT (id) DO UPDATE
    SET message = EXCLUDED.message, level = EXCLUDED.level, title = EXCLUDED.title,
        active = true, updated_at = now()
    """
)
_TOGGLE = text(
    "UPDATE status_message SET active = NOT active, updated_at = now() "
    "WHERE id = :id AND message IS NOT NULL "
    "RETURNING message, level, title, active, updated_at"
)
_CLEAR = text(
    "UPDATE status_message SET message = NULL, level = 'warning', title = NULL, "
    "active = false, updated_at = now() WHERE id = :id"
)


def validate_post(message: str, level: str, title: str | None = None) -> None:
    """Pure precondition check, run before any DB write."""
    if not message or not message.strip():
        raise ValueError("message must not be blank")
    if level not in VALID_LEVELS:
        raise ValueError(f"level must be one of {VALID_LEVELS}, got {level!r}")
    if title is not None:
        if not title.strip():
            raise ValueError("title must not be blank")
        if len(title.strip()) > MAX_TITLE_LENGTH:
            raise ValueError(f"title must be at most {MAX_TITLE_LENGTH} characters")


async def get_status(session: AsyncSession) -> dict:
    row = (await session.execute(_SELECT, {"id": _ROW_ID})).mappings().first()
    return dict(row) if row is not None else dict(_UNSET)


async def post(session: AsyncSession, message: str, level: str, title: str | None = None) -> dict:
    validate_post(message, level, title)
    await session.execute(
        _UPSERT,
        {
            "id": _ROW_ID,
            "message": message.strip(),
            "level": level,
            "title": title.strip() if title else None,
        },
    )
    return await get_status(session)


async def toggle(session: AsyncSession) -> dict:
    row = (await session.execute(_TOGGLE, {"id": _ROW_ID})).mappings().first()
    return dict(row) if row is not None else dict(_UNSET)


async def clear(session: AsyncSession) -> dict:
    await session.execute(_CLEAR, {"id": _ROW_ID})
    return dict(_UNSET)


# `create_all` adds the status_message table on a fresh DB but never a column
# to one that already exists — same pattern as fusion.apply_schema /
# landmask.apply_schema. A no-op once `title` is already there.
async def apply_schema(conn) -> None:
    await conn.execute(text("ALTER TABLE status_message ADD COLUMN IF NOT EXISTS title VARCHAR(24)"))
