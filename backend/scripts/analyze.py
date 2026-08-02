"""Force an analysis of an ROI's newest usable Sentinel-1 pass. SPENDS PU.

This is the ops escape hatch. Analysis is normally scheduled (`scheduler.py`);
nothing should routinely call this. It exists because production deliberately
exposes no PU-spending HTTP endpoint — a key reachable from the internet can
spend money, so forcing a run is a shell action instead:

    cd backend
    .venv/bin/python scripts/analyze.py north_taiwan
    .venv/bin/python scripts/analyze.py north_taiwan --yes    # no prompt

    # against a deployed backend
    railway run python scripts/analyze.py north_taiwan

Every free check runs before any spend: ROI validity, CDSE credentials, the
model checkpoint, AIS coverage, and the 85% footprint-coverage guard. The
estimated cost is shown and confirmed before the fetch.

Two guards the scheduler applies are reported here rather than enforced,
because recovering a stuck region is the whole point of this script:
  - a scene that already has a pu_ledger entry would be paid for twice;
  - the run may push month-to-date PU past PU_MONTHLY_CEILING.
Both are printed as warnings in the confirmation prompt. --yes accepts them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.detect import DetectorUnavailable, load_detector  # noqa: E402
from app.rois import get_roi  # noqa: E402
from app.sar import estimate_pu, plan_fetch_grid  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("roi", help="ROI name, e.g. north_taiwan")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = parser.parse_args()

    settings = get_settings()
    try:
        roi = get_roi(args.roi)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not (settings.cdse_client_id and settings.cdse_client_secret):
        print("error: CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not configured", file=sys.stderr)
        return 2
    try:
        detector = await asyncio.to_thread(
            load_detector, settings.sar_model_path, settings.detection_conf_threshold
        )
    except DetectorUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Free: catalog search, AIS coverage gate, footprint coverage.
    try:
        scene, status = await pipeline.find_target_scene(roi)
    except pipeline.NoEligibleScene as exc:
        print(f"nothing to analyze: {exc}")
        return 1

    if status == "processed":
        print(f"{roi.name}: newest usable pass {scene.name} is already analyzed (0 PU)")
        return 0

    pu = estimate_pu(plan_fetch_grid(roi.sar_bbox))
    async with SessionLocal() as session:
        already_paid = await pipeline.scene_has_pu_spend(session, scene.id)
        month_to_date = await pipeline.month_to_date_pu(session)

    print(f"{roi.name}: {scene.name}  sensed {scene.sensed_at:%Y-%m-%d %H:%M}  status {status}")
    print(f"  estimated cost      {pu:.0f} PU")
    print(f"  month to date       {month_to_date:.0f} PU  (ceiling {settings.pu_monthly_ceiling:.0f})")
    if already_paid:
        print("  WARNING: this scene already has a PU ledger entry — you are paying twice")
    if month_to_date + pu > settings.pu_monthly_ceiling:
        print("  WARNING: this run crosses PU_MONTHLY_CEILING, which the scheduler honours")

    if not args.yes and input("  spend these PU? [y/N] ").strip().lower() not in ("y", "yes"):
        print("aborted")
        return 1

    # Await the task rather than fire-and-forget: the process must outlive the run.
    await pipeline.start_analysis(roi, scene, detector)
    async with SessionLocal() as session:
        spent = await pipeline.month_to_date_pu(session)
    print(f"done — month to date now {spent:.0f} PU")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
