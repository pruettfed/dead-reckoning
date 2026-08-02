"""Developer reset tools: SAR scenes, AIS data, and the PU ledger.

Talks to the database directly, so it needs no API key and works regardless of
ENV or DEVTOOLS_ENABLED — those gate only the /api/dev HTTP surface.

    cd backend
    .venv/bin/python scripts/dev_reset.py pu --show
    .venv/bin/python scripts/dev_reset.py pu --scope month
    .venv/bin/python scripts/dev_reset.py scenes --roi north_taiwan
    .venv/bin/python scripts/dev_reset.py scenes --scene-id <uuid>
    .venv/bin/python scripts/dev_reset.py scenes --all --dry-run
    .venv/bin/python scripts/dev_reset.py ais

Deleting scenes re-spends PU: the scheduler sees the pass as new and re-fetches
it within SCHEDULER_INTERVAL_SECONDS. That is intended. Set
SCHEDULER_ENABLED=false first when you don't want the re-fetch.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.devtools import pu_status, reset_ais, reset_pu, reset_scenes  # noqa: E402


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _print_pu_status(status: dict) -> None:
    print(
        f"month-to-date {status['month_to_date_pu']:.0f} PU"
        f"  /  ceiling {status['pu_monthly_ceiling']:.0f}"
        f"  /  budget {status['pu_monthly_budget']:.0f}"
    )
    print(f"remaining under ceiling: {status['remaining_under_ceiling']:.0f} PU")
    print(f"all time: {status['all_time_pu']:.0f} PU over {status['all_time_entries']} entries\n")

    if status["by_roi"]:
        print("this month by ROI:")
        for row in status["by_roi"]:
            print(
                f"  {row['roi']:<24} {row['pu']:>8.0f} PU"
                f"  {row['entries']:>3} entries  last {row['last_spent_at']:%Y-%m-%d %H:%M}"
            )
    else:
        print("no PU spent this month")

    if status["recent"]:
        print("\nmost recent entries:")
        for row in status["recent"]:
            print(
                f"  {row['spent_at']:%Y-%m-%d %H:%M}  {row['roi']:<24}"
                f" {row['pu']:>6.0f} PU  {row['scene_id']}"
            )


async def _run_pu(args: argparse.Namespace) -> int:
    settings = get_settings()
    async with SessionLocal() as session:
        if args.show:
            _print_pu_status(await pu_status(session, settings=settings))
            return 0

        target = f"scope={args.scope}" + (f" roi={args.roi}" if args.roi else "")
        if not _confirm(f"Delete PU ledger entries ({target})?", args.yes):
            print("aborted")
            return 1
        result = await reset_pu(session, roi=args.roi, scope=args.scope)
        if args.dry_run:
            await session.rollback()
            print(f"dry run — would delete {result['entries_deleted']} entries, rolled back")
            return 0
        await session.commit()
        print(f"deleted {result['entries_deleted']} PU ledger entries")
        print(f"note: {result['note']}")
    return 0


async def _run_scenes(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        if args.all:
            target = "every scene"
        elif args.roi:
            target = f"scenes for ROI {args.roi}"
        else:
            target = f"scene {args.scene_id}"
        if not _confirm(f"Delete {target} and their detections?", args.yes):
            print("aborted")
            return 1

        result = await reset_scenes(
            session, roi=args.roi, scene_id=args.scene_id, all_scenes=args.all
        )
        verb = "would delete" if args.dry_run else "deleted"
        print(f"{verb} {result['scenes_deleted']} scenes (detections cascade)")
        if result["rois_affected"]:
            print(f"ROIs affected: {', '.join(result['rois_affected'])}")
            print(
                f"the scheduler will re-fetch these, re-spending about "
                f"{result['projected_pu_respend']:.0f} PU"
            )
        if args.dry_run:
            await session.rollback()
            print("dry run — rolled back")
        else:
            await session.commit()
    return 0


async def _run_ais(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        if not _confirm("Delete all AIS positions and ship metadata?", args.yes):
            print("aborted")
            return 1
        result = await reset_ais(session)
        verb = "would delete" if args.dry_run else "deleted"
        print(
            f"{verb} {result['positions_deleted']} AIS positions and "
            f"{result['ship_metadata_deleted']} ship metadata rows"
        )
        if args.dry_run:
            await session.rollback()
            print("dry run — rolled back")
        else:
            await session.commit()
            print(f"note: {result['note']}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pu = subparsers.add_parser("pu", help="view or clear the PU ledger")
    pu.add_argument("--show", action="store_true", help="print the budget and exit")
    pu.add_argument("--scope", choices=("month", "all"), default="month")
    pu.add_argument("--roi", help="only this ROI")
    pu.set_defaults(func=_run_pu)

    scenes = subparsers.add_parser("scenes", help="delete SAR scenes and their detections")
    # Exactly one selector: a bare invocation must not mean "everything".
    selector = scenes.add_mutually_exclusive_group(required=True)
    selector.add_argument("--roi", help="every scene for this ROI")
    selector.add_argument("--scene-id", help="one scene by id")
    selector.add_argument("--all", action="store_true", help="every scene in the database")
    scenes.set_defaults(func=_run_scenes)

    ais = subparsers.add_parser("ais", help="delete all AIS positions and ship metadata")
    ais.set_defaults(func=_run_ais)

    for sub in (pu, scenes, ais):
        sub.add_argument("--dry-run", action="store_true", help="report, then roll back")
        sub.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    args = parser.parse_args()
    try:
        return await args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
