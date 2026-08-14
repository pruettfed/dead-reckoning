"""Explain the scheduler's AIS gate: why regions are held, and until when. 0 PU.

Read-only. Runs the same query and the same pure predicates the scheduler uses,
so what this prints is what the gate decided — not a reconstruction of it.

    cd backend
    DATABASE_URL=postgresql+asyncpg://dvd:dvd@localhost:5432/dvd \\
        .venv/bin/python scripts/ais_health.py
    # in production — `railway run` can't reach db.railway.internal; `railway ssh`
    # executes inside the container itself (no venv there, hence plain `python`)
    railway ssh -- bash -c 'cd /app && python scripts/ais_health.py'

Answers the two questions the runbook asks when nothing is analyzing:
  - Is the AIS stream actually flowing right now, or only historically?
  - Is the fused half held because of a gap, and how long until it clears?

Cross-check against /api/health -> sources.ais.state. They should agree; if
sources says connected while this says silent, the socket is up but the
subscription or the parse is broken.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.rois import ROIS  # noqa: E402
from app.scheduler import _read_ais_health, gap_bucket_count, warmup_gate  # noqa: E402


def _stamp(value) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if value else "—"


async def main() -> int:
    settings = get_settings()
    health, db_now = await _read_ais_health(settings)

    buckets = gap_bucket_count(
        settings.scheduler_warmup_hours, settings.scheduler_ais_gap_minutes
    )
    print(
        f"probing {settings.scheduler_warmup_hours:.0f}h in {buckets} bucket(s) of "
        f"{settings.scheduler_ais_gap_minutes:.0f} min   (db clock {_stamp(db_now)})\n"
    )
    print(f"  oldest AIS      {_stamp(health.min_time)}")
    print(f"  newest AIS      {_stamp(health.max_time)}", end="")
    if health.max_time:
        print(f"   ({(db_now - health.max_time).total_seconds() / 3600:.1f}h ago)")
    else:
        print()
    print(
        "  last gap ended  "
        + (_stamp(health.last_gap_end) if health.last_gap_end else "— (no gap in window)")
    )

    # started_at=db_now models a process that just booted, which is the harshest
    # reading of the survey cap: if survey is ready here, it is ready anywhere.
    gate = warmup_gate(
        health,
        started_at=db_now,
        now=db_now,
        required_hours=settings.scheduler_warmup_hours,
        max_wait_hours=settings.scheduler_warmup_max_hours,
        gap_minutes=settings.scheduler_ais_gap_minutes,
    )
    print(f"\n  fused   {'READY ' if gate.fused_ready else 'HELD  '} {gate.fused_detail}")
    print(f"  survey  {'READY ' if gate.survey_ready else 'HELD  '} {gate.survey_detail}")

    held = [r.name for r in ROIS.values() if not gate.ready(r.mode)]
    print(f"\n  {len(ROIS) - len(held)}/{len(ROIS)} regions would sweep now")
    if held:
        print(f"  held: {', '.join(held)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
