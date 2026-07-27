"""Automatic analysis: sweep every ROI, analyze each new usable pass once.

Users do not request imagery. This task is the only routine path to a pixel
fetch; the admin-gated POST endpoint remains as an ops escape hatch.

Runs in the API process as a third lifespan task alongside AIS ingest and
retention, because analysis already ran here as an asyncio task — the scheduler
only changes *when* it starts. One region at a time: fourteen concurrent YOLO
inferences would exhaust a small container.

Durability comes from the database, not from this module. `sar_scenes.id` is the
CDSE product UUID, so a restart re-polls and skips what is already processed at
zero cost, and `pu_ledger` records every fetch attempt so a failure that already
cost PU is never retried automatically.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import pipeline
from app.config import get_settings
from app.database import SessionLocal
from app.detect import Detector, DetectorUnavailable, load_detector
from app.ingest import sleep_or_stop
from app.rois import ROI, ROIS
from app.sar import SarScene, estimate_pu, plan_fetch_grid, search_scenes

logger = logging.getLogger(__name__)

# Spacing between regions inside one sweep. The catalog is free but shared;
# fourteen back-to-back queries every interval is needlessly rude.
ROI_STAGGER_SECONDS = 2.0

_schedule: dict[str, dict] = {}


@dataclass(frozen=True)
class Decision:
    analyze: bool
    reason: str


def decide(
    status: str | None,
    *,
    has_pu_spend: bool,
    month_to_date_pu: float,
    pu_cost: float,
    ceiling: float,
) -> Decision:
    """Whether to analyze the ROI's newest usable pass.

    `status` is the scene's row state (None when the catalog offers a pass we
    have never seen). `has_pu_spend` says whether a pixel fetch was already
    attempted for it.
    """
    if status == "processed":
        return Decision(False, "already analyzed")
    if status == "processing":
        return Decision(False, "analysis already running")
    if status == "failed" and has_pu_spend:
        # It already cost PU once. Retrying on every sweep would spend the
        # month's budget on a scene that may never succeed; recovering it is a
        # deliberate ops action through the admin endpoint.
        return Decision(False, "previous attempt already spent PU")
    if month_to_date_pu + pu_cost > ceiling:
        return Decision(
            False,
            f"month-to-date PU {month_to_date_pu:.0f} + {pu_cost:.0f} would cross "
            f"the {ceiling:.0f} ceiling",
        )
    return Decision(True, "new pass" if status is None else "retrying a failure that cost nothing")


def schedule_state(
    next_expected_at: datetime | None, *, analyzing: bool, now: datetime
) -> str:
    """How a region's next analysis should read in the UI."""
    if analyzing:
        return "analyzing"
    if next_expected_at is None:
        # estimate_next_pass needs three distinct passes to take a median.
        return "unknown"
    if next_expected_at <= now:
        # The estimate is cached from the last sweep, and GRD publication lags
        # acquisition by hours either way.
        return "awaiting_publication"
    return "scheduled"


def _row(roi: ROI, *, scenes: list[SarScene], now: datetime) -> dict:
    """The catalog-derived facts for a region. Deliberately excludes anything
    the database knows — see `snapshot`."""
    latest = max((s.sensed_at for s in scenes), default=None)
    expected = pipeline.estimate_next_pass([s.sensed_at for s in scenes], now)
    return {
        "name": roi.name,
        "label": roi.label,
        "mode": roi.mode,
        "latest_scene_sensed_at": latest.isoformat() if latest else None,
        "next_expected_at": expected.isoformat() if expected else None,
    }


def recent_scenes(scenes: list[SarScene], now: datetime) -> list[SarScene]:
    """The shorter window `find_target_scene` searches for itself.

    The sweep fetches NEXT_PASS_LOOKBACK_DAYS so the interval estimate has enough
    passes to take a median, but handing that whole list to the trigger decision
    would widen it: a survey ROI accepts the newest covering pass with no AIS
    bracket to bound it, so it would start analyzing fortnight-old imagery.
    """
    cutoff = now - timedelta(days=pipeline.SEARCH_WINDOW_DAYS)
    return [s for s in scenes if s.sensed_at >= cutoff]


def snapshot(last_processed: dict[str, datetime], now: datetime) -> list[dict]:
    """Schedule rows, soonest pass first, regions with no estimate last.

    Only the catalog facts are cached from the last sweep; `last_processed_at`
    and `state` are derived here, per request. Caching those was the bug: a
    region that finished analyzing kept reporting "analyzing" and "never
    analyzed" until its next sweep, up to SCHEDULER_INTERVAL_SECONDS later,
    while the rest of the API already knew better.

    Empty until the first sweep finishes — the API reports that rather than
    fanning out fourteen catalog calls on a cold page load.
    """
    rows = [
        row
        | {
            "last_processed_at": (
                last_processed[row["name"]].isoformat()
                if row["name"] in last_processed
                else None
            ),
            "state": schedule_state(
                _parse(row["next_expected_at"]),
                analyzing=pipeline.is_in_flight(row["name"]),
                now=now,
            ),
        }
        for row in _schedule.values()
    ]
    return sorted(
        rows, key=lambda r: (r["next_expected_at"] is None, r["next_expected_at"] or "")
    )


def _parse(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


async def _sweep_roi(roi: ROI, detector: Detector, ceiling: float) -> None:
    """Refresh one region's schedule row and analyze its newest pass if due."""
    now = datetime.now(tz=timezone.utc)
    # One catalog call serves both jobs: the pass-interval estimate wants the
    # longer lookback, the trigger decision wants the shorter one.
    scenes = await search_scenes(
        roi.sar_bbox, now - timedelta(days=pipeline.NEXT_PASS_LOOKBACK_DAYS), now
    )
    _schedule[roi.name] = _row(roi, scenes=scenes, now=now)

    try:
        scene, status = await pipeline.find_target_scene(
            roi, scenes=recent_scenes(scenes, now)
        )
    except pipeline.NoEligibleScene as exc:
        # Routine: roughly half of all passes only graze the box.
        logger.debug("%s: %s", roi.name, exc)
        return

    async with SessionLocal() as session:
        spent = await pipeline.scene_has_pu_spend(session, scene.id)
        month_to_date = await pipeline.month_to_date_pu(session)
    decision = decide(
        status,
        has_pu_spend=spent,
        month_to_date_pu=month_to_date,
        pu_cost=estimate_pu(plan_fetch_grid(roi.sar_bbox)),
        ceiling=ceiling,
    )
    if not decision.analyze:
        logger.debug("%s: skipping %s — %s", roi.name, scene.name, decision.reason)
        return

    logger.info("%s: analyzing %s (%s)", roi.name, scene.name, decision.reason)
    # Awaited, not fire-and-forget: the sweep is deliberately serial. No row
    # bookkeeping around it — `snapshot` reads `is_in_flight` and the database
    # live, so the region reports "analyzing" and then its new "analyzed" time
    # without this function having to remember to say so.
    await pipeline.start_analysis(roi, scene, detector)


async def run_scheduler(stop: asyncio.Event) -> None:
    """Sweep every ROI on an interval, analyzing new passes as they appear."""
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    if not (settings.cdse_client_id and settings.cdse_client_secret):
        logger.warning("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not set — scheduler idle")
        return
    try:
        detector = await asyncio.to_thread(
            load_detector, settings.sar_model_path, settings.detection_conf_threshold
        )
    except DetectorUnavailable as exc:
        logger.warning("scheduler idle: %s", exc)
        return

    logger.info(
        "scheduler running: %d regions every %.0fs, PU ceiling %.0f",
        len(ROIS),
        settings.scheduler_interval_seconds,
        settings.pu_monthly_ceiling,
    )
    while not stop.is_set():
        for roi in ROIS.values():
            if stop.is_set():
                return
            try:
                await _sweep_roi(roi, detector, settings.pu_monthly_ceiling)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One region's failure must not stop the sweep.
                logger.warning("scheduler sweep failed for %s: %s", roi.name, exc)
            if await sleep_or_stop(stop, ROI_STAGGER_SECONDS):
                return
        if await sleep_or_stop(stop, settings.scheduler_interval_seconds):
            return
