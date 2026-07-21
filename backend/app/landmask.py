"""Land masking for SAR detections.

A YOLO hit on a rock, a breakwater or a shoreline building is not a vessel, and
no amount of retraining reliably teaches that — the discriminating fact is
geographic, not radiometric. So it is decided geometrically: `land_polygons`
holds coastline geometry clipped to the ROI boxes, and every detection inside it
(optionally plus a seaward buffer) is flagged `on_land`.

Flagged, not deleted, and that is deliberate. Re-running *detection* would mean
re-fetching pixels — the full-resolution chip is never persisted, only the
downsampled overview — so it costs PU. Re-running the *mask* is a pure
recompute over stored detection points, so retuning `LAND_MASK_BUFFER_M` is one
UPDATE at zero cost. `scripts/load_land.py` does exactly that across every
scene already in the database.

Buffer defaults to 0 (strictly inside land). Widening it starts eating real
vessels: ships berthed alongside a quay in Singapore Strait, or anchored off
Fujairah, sit within a few hundred metres of the coastline. That said, the
pipeline fetches with SIGMA0_ELLIPSOID rather than terrain correction, so the
mountainous ROIs (musandam_stage, hormuz_strait) carry genuine positional drift
near shore and may want a small buffer. Measure before widening: the script
reports what each value would mask.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# `create_all` adds the missing `land_polygons` table on its own, but it will
# never add a column to a table that already exists — and there is no Alembic
# here. This runs on every boot (see main.lifespan) so a database with
# detections already in it picks the mask up without being wiped.
ADD_ON_LAND_COLUMN = text(
    "ALTER TABLE sar_detections "
    "ADD COLUMN IF NOT EXISTS on_land BOOLEAN NOT NULL DEFAULT false"
)

# ST_DWithin on geography takes metres, so buffer 0 degenerates to plain
# containment and one query covers both cases. The GIST index carries it.
#
# scene_id is CAST explicitly: asyncpg infers parameter types from context and
# a bare NULL in `:scene_id IS NULL` gives it nothing to work from.
MARK_LAND = text(
    """
    UPDATE sar_detections d
    SET on_land = EXISTS (
        SELECT 1 FROM land_polygons l
        WHERE ST_DWithin(l.geom, d.location, :buffer_m)
    )
    WHERE (CAST(:scene_id AS text) IS NULL OR d.scene_id = CAST(:scene_id AS text))
    """
)

LAND_POLYGON_COUNT = text("SELECT count(*) FROM land_polygons")


async def apply_schema(conn) -> None:
    """Add `on_land` to an existing sar_detections. Safe to run repeatedly."""
    await conn.execute(ADD_ON_LAND_COLUMN)


async def land_loaded(session: AsyncSession) -> bool:
    """Whether any coastline geometry has been loaded (scripts/load_land.py)."""
    return bool((await session.execute(LAND_POLYGON_COUNT)).scalar())


async def mark_land_detections(
    session: AsyncSession, buffer_m: float, *, scene_id: str | None = None
) -> int:
    """Flag detections falling on land. `scene_id=None` recomputes every scene.

    Returns the number now flagged. A no-op returning 0 when no coastline has
    been loaded — an empty `land_polygons` would otherwise silently clear the
    flag on every detection and look like the mask had run correctly.
    """
    if not await land_loaded(session):
        return 0
    await session.execute(MARK_LAND, {"buffer_m": buffer_m, "scene_id": scene_id})
    return (
        await session.execute(
            text(
                "SELECT count(*) FROM sar_detections WHERE on_land AND "
                "(CAST(:scene_id AS text) IS NULL OR scene_id = CAST(:scene_id AS text))"
            ),
            {"scene_id": scene_id},
        )
    ).scalar() or 0
