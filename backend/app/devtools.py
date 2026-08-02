"""Developer reset tools: scenes, AIS, and the PU ledger.

Two front-ends share the operations below: `scripts/dev_reset.py` (local, talks
to the database directly, needs no key) and an `/api/dev` router that exists
only outside production.

Why the router can't leak into production, twice over:

1. `register_devtools` is a no-op unless `settings.devtools_available`, so the
   routes are never constructed — absent from `app.routes` and from the OpenAPI
   schema, not merely guarded.
2. Every route still depends on `require_devtools_key`, which re-checks the
   environment at request time and answers 404 (never 401/403 — a production
   caller learns nothing about what might exist here).

`Settings` refuses to construct at all when ENV=production and DEVTOOLS_ENABLED
is true, so the unsafe combination cannot even boot.

Resets re-spend PU by design. Deleting a scene row makes `scheduler.decide` see
`status=None` -> "new pass", so the next sweep re-fetches and re-pays for that
imagery within SCHEDULER_INTERVAL_SECONDS. That is the point of a reset; set
SCHEDULER_ENABLED=false when you don't want it.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import pipeline
from app.config import Settings
from app.database import get_session
from app.rois import ROIS, get_roi
from app.sar import PU_MONTHLY_BUDGET, estimate_pu, plan_fetch_grid
from app.security import check_admin_key

log = logging.getLogger(__name__)

# Detections cascade via the sar_detections -> sar_scenes FK; AIS is untouched.
DELETE_SCENES_ALL = text("DELETE FROM sar_scenes")
DELETE_SCENES_BY_ROI = text("DELETE FROM sar_scenes WHERE roi = :roi")
DELETE_SCENE_BY_ID = text("DELETE FROM sar_scenes WHERE id = :scene_id")

# Which ROIs a delete would affect, read before the delete so the reported
# re-spend reflects what was actually there.
SCENE_ROIS_ALL = text("SELECT DISTINCT roi FROM sar_scenes")
SCENE_ROIS_BY_ROI = text("SELECT DISTINCT roi FROM sar_scenes WHERE roi = :roi")
SCENE_ROIS_BY_ID = text("SELECT DISTINCT roi FROM sar_scenes WHERE id = :scene_id")

DELETE_AIS_POSITIONS = text("DELETE FROM ais_positions")
# Cleared alongside positions: the vessels endpoints LEFT JOIN this table, so
# leaving it behind surfaces names and types for vessels that no longer exist.
DELETE_SHIP_METADATA = text("DELETE FROM ship_metadata")

# `spent_at >= date_trunc('month', now())` is exactly the predicate
# pipeline.MONTH_TO_DATE_PU reads, so the number this moves is the number the
# ceiling checks.
DELETE_PU_MONTH = text(
    "DELETE FROM pu_ledger WHERE spent_at >= date_trunc('month', now()) "
    "AND (CAST(:roi AS text) IS NULL OR roi = CAST(:roi AS text))"
)
DELETE_PU_ALL = text(
    "DELETE FROM pu_ledger WHERE (CAST(:roi AS text) IS NULL OR roi = CAST(:roi AS text))"
)

PU_BY_ROI = text(
    """
    SELECT roi,
           sum(pu) AS pu,
           count(*) AS entries,
           max(spent_at) AS last_spent_at
    FROM pu_ledger
    WHERE spent_at >= date_trunc('month', now())
    GROUP BY roi
    ORDER BY sum(pu) DESC
    """
)
PU_RECENT = text(
    "SELECT roi, scene_id, pu, spent_at FROM pu_ledger ORDER BY spent_at DESC LIMIT :limit"
)
PU_TOTALS = text("SELECT coalesce(sum(pu), 0) AS pu, count(*) AS entries FROM pu_ledger")

AIS_RESET_NOTE = (
    "Fused ROIs stop analyzing until the AIS buffer refills: find_target_scene "
    "refuses a scene with no AIS in the ROI, and fusion additionally needs "
    "FUSION_MAX_TIME_DELTA_HOURS of buffer before the acquisition."
)
PU_RESET_NOTE = (
    "This clears our ledger, not Copernicus's meter. The real monthly quota does "
    "not come back; only this deployment's view of what it has spent."
)


def _respend_pu(roi_names: list[str]) -> float:
    """PU the scheduler will re-spend re-fetching the deleted ROIs' next pass."""
    total = 0.0
    for name in roi_names:
        roi = ROIS.get(name)
        if roi is not None:
            total += estimate_pu(plan_fetch_grid(roi.sar_bbox))
    return round(total, 1)


async def reset_scenes(
    session: AsyncSession,
    *,
    roi: str | None = None,
    scene_id: str | None = None,
    all_scenes: bool = False,
) -> dict:
    """Delete SAR scenes and, by FK cascade, their detections.

    Exactly one selector is required. A missing selector is an error rather
    than an implicit "everything" — a forgotten argument must not wipe the
    table. Does not commit; the caller decides.
    """
    selectors = [roi is not None, scene_id is not None, all_scenes]
    if sum(selectors) != 1:
        raise ValueError("pass exactly one of roi, scene_id, all_scenes")

    if roi is not None:
        get_roi(roi)  # fail fast on a typo rather than silently deleting nothing
        rois_query, params, statement = SCENE_ROIS_BY_ROI, {"roi": roi}, DELETE_SCENES_BY_ROI
    elif scene_id is not None:
        rois_query, params, statement = SCENE_ROIS_BY_ID, {"scene_id": scene_id}, DELETE_SCENE_BY_ID
    else:
        rois_query, params, statement = SCENE_ROIS_ALL, {}, DELETE_SCENES_ALL

    affected = [r[0] for r in (await session.execute(rois_query, params)).all()]
    deleted = (await session.execute(statement, params)).rowcount
    return {
        "scenes_deleted": deleted,
        "rois_affected": affected,
        "projected_pu_respend": _respend_pu(affected),
        "note": (
            "Detections were removed by the sar_scenes FK cascade. The scheduler "
            "treats these passes as new and will re-fetch them on its next sweep."
        ),
    }


async def reset_ais(session: AsyncSession) -> dict:
    """Delete every AIS position and every cached ship identity."""
    positions = (await session.execute(DELETE_AIS_POSITIONS)).rowcount
    metadata = (await session.execute(DELETE_SHIP_METADATA)).rowcount
    return {
        "positions_deleted": positions,
        "ship_metadata_deleted": metadata,
        "note": AIS_RESET_NOTE,
    }


async def pu_status(session: AsyncSession, *, settings: Settings, recent: int = 10) -> dict:
    """Month-to-date PU against the ceiling, with a per-ROI breakdown."""
    month_to_date = await pipeline.month_to_date_pu(session)
    by_roi = (await session.execute(PU_BY_ROI)).mappings().all()
    recent_rows = (await session.execute(PU_RECENT, {"limit": recent})).mappings().all()
    totals = (await session.execute(PU_TOTALS)).mappings().one()
    ceiling = settings.pu_monthly_ceiling
    return {
        "month_to_date_pu": month_to_date,
        "pu_monthly_ceiling": ceiling,
        "pu_monthly_budget": PU_MONTHLY_BUDGET,
        "remaining_under_ceiling": round(ceiling - month_to_date, 1),
        "by_roi": [dict(r) for r in by_roi],
        "recent": [dict(r) for r in recent_rows],
        "all_time_pu": float(totals["pu"]),
        "all_time_entries": totals["entries"],
    }


async def reset_pu(
    session: AsyncSession,
    *,
    roi: str | None = None,
    scope: Literal["month", "all"] = "month",
) -> dict:
    """Delete PU ledger rows. Does not commit; the caller decides."""
    if scope not in ("month", "all"):
        raise ValueError("scope must be 'month' or 'all'")
    if roi is not None:
        get_roi(roi)
    statement = DELETE_PU_MONTH if scope == "month" else DELETE_PU_ALL
    deleted = (await session.execute(statement, {"roi": roi})).rowcount
    return {"entries_deleted": deleted, "scope": scope, "roi": roi, "note": PU_RESET_NOTE}


# --------------------------------------------------------------------------
# HTTP surface — registered only outside production
# --------------------------------------------------------------------------

def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def require_devtools_key(
    request: Request,
    x_devtools_key: Annotated[str | None, Header()] = None,
) -> None:
    """Second lock: re-check the environment per request, then the key.

    Answers 404 rather than 401/403 when the tools are off, so a production
    caller cannot distinguish "disabled here" from "no such endpoint".
    """
    settings: Settings = request.app.state.settings
    if not settings.devtools_available:
        raise HTTPException(status_code=404, detail="Not Found")
    try:
        check_admin_key(
            x_devtools_key,
            settings.devtools_api_key,
            what="developer tools",
            setting="DEVTOOLS_API_KEY",
            header="X-Devtools-Key",
        )
    except HTTPException:
        # Failed auth against a destructive endpoint is worth a line; nothing
        # else in this app records one.
        log.warning("devtools auth rejected from %s for %s", _client(request), request.url.path)
        raise


router = APIRouter(
    prefix="/api/dev",
    tags=["devtools"],
    dependencies=[Depends(require_devtools_key)],
)


def _guard_in_flight(roi: str | None) -> None:
    running = pipeline.is_in_flight(roi) if roi else pipeline.any_in_flight()
    if running:
        raise HTTPException(
            status_code=409,
            detail="an analysis is running; wait for it to finish or set SCHEDULER_ENABLED=false",
        )


@router.get("/pu", summary="Dev-only: PU spent this month, per ROI, against the ceiling")
async def dev_pu_status(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return await pu_status(session, settings=request.app.state.settings)


@router.delete("/pu", summary="Dev-only: clear PU ledger entries")
async def dev_reset_pu(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    scope: Literal["month", "all"] = Query(default="month"),
    roi: str | None = Query(default=None),
) -> dict:
    try:
        result = await reset_pu(session, roi=roi, scope=scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await session.commit()
    log.warning(
        "devtools reset_pu from %s: scope=%s roi=%s deleted=%s",
        _client(request), scope, roi, result["entries_deleted"],
    )
    return result


@router.delete("/scenes", summary="Dev-only: delete SAR scenes and their detections")
async def dev_reset_scenes(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    roi: str | None = Query(default=None),
    scene_id: str | None = Query(default=None),
    all: bool = Query(default=False, description="delete every scene; required to be explicit"),
) -> dict:
    _guard_in_flight(roi)
    try:
        result = await reset_scenes(session, roi=roi, scene_id=scene_id, all_scenes=all)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await session.commit()
    log.warning(
        "devtools reset_scenes from %s: roi=%s scene_id=%s all=%s deleted=%s respend_pu=%s",
        _client(request), roi, scene_id, all,
        result["scenes_deleted"], result["projected_pu_respend"],
    )
    return result


@router.delete("/ais", summary="Dev-only: delete all AIS positions and ship metadata")
async def dev_reset_ais(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    result = await reset_ais(session)
    await session.commit()
    log.warning(
        "devtools reset_ais from %s: positions=%s metadata=%s",
        _client(request), result["positions_deleted"], result["ship_metadata_deleted"],
    )
    return result


def register_devtools(app: FastAPI, settings: Settings) -> bool:
    """First lock: build the /api/dev routes only when the environment allows.

    Returns whether the router was registered. A missing or short
    DEVTOOLS_API_KEY warns rather than raising — the CLI needs no key, so a
    fresh clone should still boot with the tools simply unavailable over HTTP.
    """
    if settings.is_production:
        return False
    if not settings.devtools_enabled:
        return False
    if not settings.devtools_available:
        log.warning(
            "DEVTOOLS_ENABLED=true but DEVTOOLS_API_KEY is missing or shorter than "
            "32 characters; /api/dev not registered. scripts/dev_reset.py still works."
        )
        return False
    app.include_router(router)
    log.warning("developer reset endpoints registered at /api/dev (ENV=%s)", settings.env)
    return True
