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
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app import pipeline
from app.config import get_settings
from app.database import SessionLocal
from app.detect import DetectorSpec
from app.ingest import sleep_or_stop
from app.rois import ROI, ROIS
from app.sar import SarScene, estimate_pu, plan_fetch_grid, search_scenes

logger = logging.getLogger(__name__)

# Spacing between regions inside one sweep. The catalog is free but shared;
# fourteen back-to-back queries every interval is needlessly rude.
ROI_STAGGER_SECONDS = 2.0

# How often the warm-up gate re-checks the AIS buffer.
WARMUP_POLL_SECONDS = 60.0

# One round trip, four facts, all served by ix_ais_positions_time. min/max come
# back through the planner's endpoint rewrite (InitPlan + backward index-only
# scan + Limit), not an aggregate over the table; the gap scan is exactly
# `bucket_count` LIMIT-1 probes, driven by a LATERAL the planner cannot flatten
# into a hash anti-join over the whole table (a plain correlated NOT EXISTS can,
# because generate_series carries no row statistics).
#
# Buckets are anchored on now() and counted *backwards*, never date_trunc'd.
# generate_series(start, now(), step) emits a trailing bucket whose span runs
# into the future; seconds after each boundary it reads empty and would re-hold
# every fused region for a full warm-up, at every rollover, forever. Counting
# back from now() makes each bucket complete by construction. `secs =>` only:
# exact, and independent of the session TimeZone.
AIS_HEALTH = text(
    """
    WITH bucket AS (
        SELECT now() - make_interval(secs => (k - 1) * CAST(:bucket_seconds AS double precision)) AS ends_at,
               now() - make_interval(secs =>  k      * CAST(:bucket_seconds AS double precision)) AS starts_at
        FROM generate_series(1, CAST(:bucket_count AS integer)) AS k
    )
    SELECT now()                                 AS db_now,
           (SELECT min(time) FROM ais_positions) AS min_ais_time,
           (SELECT max(time) FROM ais_positions) AS max_ais_time,
           (SELECT max(b.ends_at)
              FROM bucket b
              LEFT JOIN LATERAL (
                  SELECT 1 AS hit FROM ais_positions p
                  WHERE p.time >= b.starts_at AND p.time < b.ends_at
                  LIMIT 1
              ) h ON true
             WHERE h.hit IS NULL)                AS last_gap_end
    """
)

_schedule: dict[str, dict] = {}

# ROI names the gate is currently holding, name -> reason. Written once per
# sweep, read per request by `snapshot`. Rebound wholesale rather than mutated
# in place, so a request never observes a half-written gate.
_held: dict[str, str] = {}

# Surfaced on /api/analysis/schedule so a dead scheduler is visible, not just logged.
_status: dict = {"state": "starting", "detail": "scheduler has not started yet"}


def status() -> dict:
    return dict(_status)


def _set_status(state: str, detail: str) -> None:
    _status.update(state=state, detail=detail)


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
    have never seen — including a scene that *was* seen, but whose row was
    deleted, e.g. by `scripts/dev_reset.py`). `has_pu_spend` says whether a
    pixel fetch was already attempted for it, checked against `pu_ledger`
    directly rather than the row, since the row is exactly what a reset
    erases; the ledger is deliberately never touched by a scene reset.
    """
    if status == "processed":
        return Decision(False, "already analyzed")
    if status == "processing":
        return Decision(False, "analysis already running")
    if has_pu_spend:
        # It already cost PU once, whether the row still says so (`failed`) or
        # was deleted entirely (`None` — a dev reset does exactly this, and
        # otherwise looks identical to a genuinely new pass). Retrying on
        # every sweep would spend the month's budget on a scene that may
        # never succeed; recovering it is a deliberate ops action through the
        # admin endpoint.
        return Decision(False, "previous attempt already spent PU")
    if month_to_date_pu + pu_cost > ceiling:
        return Decision(
            False,
            f"month-to-date PU {month_to_date_pu:.0f} + {pu_cost:.0f} would cross "
            f"the {ceiling:.0f} ceiling",
        )
    return Decision(True, "new pass" if status is None else "retrying a failure that cost nothing")


def gap_bucket_count(required_hours: float, bucket_minutes: float) -> int:
    """Complete buckets needed to cover `required_hours`, rounded up.

    Rounded up rather than down: an under-covered window leaves a remainder a
    gap could hide in.
    """
    if required_hours <= 0 or bucket_minutes <= 0:
        return 0
    return math.ceil(required_hours * 60.0 / bucket_minutes)


def warmup_ready(
    min_ais_time: datetime | None,
    *,
    started_at: datetime,
    now: datetime,
    required_hours: float,
    max_wait_hours: float,
) -> tuple[bool, str]:
    """Whether a *survey* ROI may be swept, and why.

    Measured from the oldest AIS fix rather than process start, so a redeploy
    onto a populated database is ready immediately. The cap releases regions
    even with no AIS at all, since AISSTREAM_API_KEY may be unset — which is
    correct for survey ROIs, who skip fusion. Fused ROIs go through
    `warmup_gate` instead, which has no cap.
    """
    if required_hours <= 0:
        # Compose sets SCHEDULER_WARMUP_HOURS: 0 and leaves the cap at 8h. The
        # depth branch below is skipped on an empty database, so without this
        # short-circuit local dev blocks on the cap for eight hours.
        return True, "AIS warm-up disabled (SCHEDULER_WARMUP_HOURS=0)"
    if min_ais_time is not None:
        depth_h = (now - min_ais_time).total_seconds() / 3600
        if depth_h >= required_hours:
            return True, f"AIS buffer {depth_h:.1f}h deep"
    waited_h = (now - started_at).total_seconds() / 3600
    if waited_h >= max_wait_hours:
        return True, (
            f"starting without a full AIS buffer: waited {waited_h:.1f}h, "
            f"the {max_wait_hours:.0f}h cap"
        )
    if min_ais_time is None:
        return False, f"no AIS recorded yet ({waited_h:.1f}h of {max_wait_hours:.0f}h cap)"
    depth_h = (now - min_ais_time).total_seconds() / 3600
    return False, f"AIS buffer {depth_h:.1f}h deep, need {required_hours:.0f}h"


@dataclass(frozen=True)
class AisHealth:
    """What the database says about the AIS stream.

    Plain values only, so every rule below stays a pure function the test suite
    can exercise with no database. `last_gap_end` is the end of the newest empty
    bucket inside the probed window — the instant continuity provably resumed —
    or None when the whole window is covered.
    """

    min_time: datetime | None
    max_time: datetime | None
    last_gap_end: datetime | None


@dataclass(frozen=True)
class WarmupGate:
    """Per-mode verdict. Fused and survey ROIs are gated on different facts."""

    fused_ready: bool
    survey_ready: bool
    fused_detail: str
    survey_detail: str

    def ready(self, mode: str) -> bool:
        return self.survey_ready if mode == "survey" else self.fused_ready


def _fused_ready(
    health: AisHealth,
    *,
    now: datetime,
    required_hours: float,
    gap_minutes: float,
) -> tuple[bool, str]:
    """Whether a fused ROI may be swept. Deliberately uncapped.

    A fused ROI analyzed against a stale AIS buffer does not merely fail — it
    matches nothing, measures a 0% chance-match rate on empty water, reads as
    "discriminating", and calls every vessel dark. Waiting costs a delay;
    proceeding costs PU and produces a false dark-fleet report. So there is no
    cap here: with AIS down, fused regions wait however long it takes.
    """
    if required_hours <= 0:
        return True, "AIS gate disabled (SCHEDULER_WARMUP_HOURS=0)"
    if health.max_time is None:
        return False, "no AIS recorded at all"
    silent_h = (now - health.max_time).total_seconds() / 3600
    if silent_h > gap_minutes / 60:
        # Also the guard that fires when the stream dies mid-flight, which is
        # what re-holds fused regions on the next sweep.
        return False, f"AIS silent for {silent_h:.1f}h"
    # No gap in the probed window means continuity reaches at least as far back
    # as it was probed, which is exactly `required_hours`.
    continuous_since = health.last_gap_end or now - timedelta(hours=required_hours)
    continuous_h = (now - continuous_since).total_seconds() / 3600
    if continuous_h >= required_hours:
        return True, f"AIS continuous for {required_hours:.0f}h"
    return False, (
        f"AIS continuous only {continuous_h:.1f}h since a gap ended "
        f"{continuous_since.isoformat()}, need {required_hours:.0f}h"
    )


def warmup_gate(
    health: AisHealth,
    *,
    started_at: datetime,
    now: datetime,
    required_hours: float,
    max_wait_hours: float,
    gap_minutes: float,
) -> WarmupGate:
    """The sweep gate, split by ROI mode.

    Survey delegates to `warmup_ready` rather than reimplementing it, so the two
    paths cannot drift and the capped survey contract stays provably unchanged.
    """
    fused_ready, fused_detail = _fused_ready(
        health, now=now, required_hours=required_hours, gap_minutes=gap_minutes
    )
    survey_ready, survey_detail = warmup_ready(
        health.min_time,
        started_at=started_at,
        now=now,
        required_hours=required_hours,
        max_wait_hours=max_wait_hours,
    )
    return WarmupGate(
        fused_ready=fused_ready,
        survey_ready=survey_ready,
        fused_detail=fused_detail,
        survey_detail=survey_detail,
    )


def schedule_state(
    next_expected_at: datetime | None,
    *,
    analyzing: bool,
    now: datetime,
    warming_up: bool = False,
) -> str:
    """How a region's next analysis should read in the UI."""
    if analyzing:
        return "analyzing"
    if warming_up:
        # Don't show a countdown for work the scheduler is deliberately holding.
        return "warming_up"
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
    held = _held  # bound once: the sweep may rebind it mid-comprehension
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
                # Per region, not global: with AIS down the survey half runs
                # normally while the fused half is held.
                warming_up=row["name"] in held,
            ),
        }
        for row in _schedule.values()
    ]
    return sorted(
        rows, key=lambda r: (r["next_expected_at"] is None, r["next_expected_at"] or "")
    )


def _parse(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


async def _sweep_roi(
    roi: ROI, spec: DetectorSpec, ceiling: float, *, held: str | None = None
) -> None:
    """Refresh one region's schedule row and analyze its newest pass if due.

    `held` is the gate's reason when this region is warming up. The catalog row
    is still refreshed first — it costs nothing extra and keeps the countdown
    live, so the UI shows "AIS warm-up" beside the pass it is about to miss
    rather than a blank row.
    """
    now = datetime.now(tz=timezone.utc)
    # One catalog call serves both jobs: the pass-interval estimate wants the
    # longer lookback, the trigger decision wants the shorter one.
    scenes = await search_scenes(
        roi.sar_bbox, now - timedelta(days=pipeline.NEXT_PASS_LOOKBACK_DAYS), now
    )
    _schedule[roi.name] = _row(roi, scenes=scenes, now=now)

    if held is not None:
        logger.debug("%s: held — %s", roi.name, held)
        return

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
    await pipeline.start_analysis(roi, scene, spec)


async def _read_ais_health(settings) -> tuple[AisHealth, datetime]:
    """The gate's database half. Returns plain values and the DB's own clock.

    `db_now` travels with the reading so every comparison in `warmup_gate` is
    DB-clock against DB-clock, and container clock drift cannot reach the gate.
    """
    async with SessionLocal() as session:
        row = (
            await session.execute(
                AIS_HEALTH,
                {
                    "bucket_seconds": settings.scheduler_ais_gap_minutes * 60.0,
                    "bucket_count": gap_bucket_count(
                        settings.scheduler_warmup_hours,
                        settings.scheduler_ais_gap_minutes,
                    ),
                },
            )
        ).mappings().one()
    health = AisHealth(
        min_time=row["min_ais_time"],
        max_time=row["max_ais_time"],
        last_gap_end=row["last_gap_end"],
    )
    return health, row["db_now"]


def _apply_gate(gate: WarmupGate, settings) -> dict[str, str]:
    """Publish the gate: rebuild `_held` and describe the fleet in `_status`."""
    global _held
    held = {
        roi.name: (gate.survey_detail if roi.mode == "survey" else gate.fused_detail)
        for roi in ROIS.values()
        if not gate.ready(roi.mode)
    }
    _held = held
    if len(held) == len(ROIS):
        _set_status("warming_up", gate.fused_detail)
    elif held:
        _set_status(
            "running",
            f"{len(ROIS) - len(held)} regions every "
            f"{settings.scheduler_interval_seconds:.0f}s; "
            f"{len(held)} fused regions HELD — {gate.fused_detail}",
        )
    else:
        _set_status(
            "running",
            f"{len(ROIS)} regions every {settings.scheduler_interval_seconds:.0f}s",
        )
    return held


async def run_scheduler(stop: asyncio.Event) -> None:
    """Sweep every ROI on an interval, analyzing new passes as they appear."""
    settings = get_settings()
    if not settings.scheduler_enabled:
        _set_status("disabled", "SCHEDULER_ENABLED=false")
        logger.info("scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    if not (settings.cdse_client_id and settings.cdse_client_secret):
        _set_status("idle", "CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not configured")
        logger.warning("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not set — scheduler idle")
        return
    # Presence check only — loading the model here would import torch into the
    # API process. Detection runs in a subprocess instead (detect_worker.py).
    if not os.path.exists(settings.sar_model_path):
        detail = f"model checkpoint not found at {settings.sar_model_path!r}"
        _set_status("idle", detail)
        logger.warning("scheduler idle: %s — train one via ml/README.md", detail)
        return
    spec = DetectorSpec(
        model_path=settings.sar_model_path,
        conf_threshold=settings.detection_conf_threshold,
    )

    logger.info(
        "scheduler running: %d regions every %.0fs, PU ceiling %.0f",
        len(ROIS),
        settings.scheduler_interval_seconds,
        settings.pu_monthly_ceiling,
    )
    # DB clock, so the survey cap survives container drift. First reading wins,
    # so the cap is measured from the first successful gate, not from a restart
    # that happened to land during an outage.
    started_at: datetime | None = None
    while not stop.is_set():
        # The gate is re-read every sweep, not once at boot: an AIS outage that
        # starts mid-flight has to re-hold the fused regions, and its recovery
        # has to start a fresh warm-up rather than resume the old one.
        try:
            health, db_now = await _read_ais_health(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Fail closed, and stay alive. This used to run outside any try, so
            # a transient database error killed the scheduler task for good.
            _set_status("warming_up", f"AIS health check failed: {exc}")
            logger.warning("scheduler gate could not read AIS health: %s", exc)
            if await sleep_or_stop(stop, WARMUP_POLL_SECONDS):
                return
            continue

        started_at = started_at or db_now
        gate = warmup_gate(
            health,
            started_at=started_at,
            now=db_now,
            required_hours=settings.scheduler_warmup_hours,
            max_wait_hours=settings.scheduler_warmup_max_hours,
            gap_minutes=settings.scheduler_ais_gap_minutes,
        )
        held = _apply_gate(gate, settings)
        if len(held) == len(ROIS):
            # Nothing to do for anyone: skip the catalog calls entirely and
            # re-check on the short poll, so a cold boot still starts within a
            # minute of AIS arriving rather than a full sweep interval later.
            logger.info("scheduler holding all regions: %s", gate.fused_detail)
            if await sleep_or_stop(stop, WARMUP_POLL_SECONDS):
                return
            continue
        if held and (db_now - started_at) > timedelta(hours=settings.scheduler_warmup_max_hours):
            # Correct, but it must not be quiet: with AIS down these regions
            # never analyze, and nothing else says so.
            logger.warning(
                "scheduler has held %d fused region(s) for over %.0fh: %s",
                len(held),
                settings.scheduler_warmup_max_hours,
                gate.fused_detail,
            )

        for roi in ROIS.values():
            if stop.is_set():
                return
            try:
                await _sweep_roi(
                    roi, spec, settings.pu_monthly_ceiling, held=held.get(roi.name)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One region's failure must not stop the sweep.
                logger.warning("scheduler sweep failed for %s: %s", roi.name, exc)
            if await sleep_or_stop(stop, ROI_STAGGER_SECONDS):
                return
        if await sleep_or_stop(stop, settings.scheduler_interval_seconds):
            return
