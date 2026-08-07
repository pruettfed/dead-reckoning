"""Re-run fusion over stored detections. Spends 0 PU.

Fusion reads only stored detection points and AIS rows, so re-matching is free —
retune the dead-reckoning budget as often as you like. Re-running *detection*
would need a fresh pixel fetch and does cost PU.

    cd backend
    .venv/bin/python scripts/refuse.py                    # every fused scene
    .venv/bin/python scripts/refuse.py --roi north_taiwan
    .venv/bin/python scripts/refuse.py --dry-run          # report, write nothing

Scenes the AIS buffer no longer reaches are reported and skipped, never
re-matched against AIS that does not bracket them.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.fusion import coverage_ok, fuse_scene  # noqa: E402
from app.pipeline import AIS_SPAN_IN_ROI  # noqa: E402
from app.rois import get_roi  # noqa: E402

SCENES = text(
    """
    SELECT s.id, s.name, s.roi, s.sensed_at,
           count(d.id) FILTER (WHERE NOT d.on_land) AS detections
    FROM sar_scenes s
    LEFT JOIN sar_detections d ON d.scene_id = s.id
    WHERE s.status = 'processed'
      AND (CAST(:roi AS text) IS NULL OR s.roi = CAST(:roi AS text))
    GROUP BY s.id
    ORDER BY s.sensed_at DESC
    """
)

# Imported rather than copied: this used to be a second verbatim copy, which is
# exactly the kind of thing that survives a change to the original.


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roi", help="only this ROI (default: all processed scenes)")
    parser.add_argument("--dry-run", action="store_true", help="report, then roll back")
    args = parser.parse_args()

    settings = get_settings()
    if args.roi:
        get_roi(args.roi)  # fail fast on a typo rather than silently matching nothing

    async with SessionLocal() as session:
        scenes = (
            await session.execute(SCENES, {"roi": args.roi})
        ).mappings().all()
        if not scenes:
            print("no processed scenes match" + (f" --roi {args.roi}" if args.roi else ""))
            return 1

        for scene in scenes:
            roi = get_roi(scene["roi"])
            label = f"{scene['roi']}  {scene['sensed_at']:%Y-%m-%d %H:%M}  {scene['detections']} detections"

            if roi.mode != "fused":
                print(f"{label}\n  skipped: survey ROI, never fused\n")
                continue

            min_lon, min_lat, max_lon, max_lat = roi.ais_bbox
            span = (
                await session.execute(
                    AIS_SPAN_IN_ROI,
                    {"min_lon": min_lon, "min_lat": min_lat,
                     "max_lon": max_lon, "max_lat": max_lat},
                )
            ).mappings().one()
            min_ais, max_ais = span["min_time"], span["max_time"]
            if not coverage_ok(
                scene["sensed_at"], min_ais, max_ais, settings.fusion_max_time_delta_hours
            ):
                print(
                    f"{label}\n  skipped: AIS buffer no longer brackets this scene "
                    f"(AIS {min_ais} to {max_ais}) — re-fusing would mark everything dark\n"
                )
                continue

            counts = await fuse_scene(
                session, scene["id"], scene["sensed_at"],
                settings=settings, fused=True,
            )
            chance = counts["chance_match_rate"]
            print(
                f"{label}\n"
                f"  matched {counts['total'] - (counts['dark'] or 0) - (counts['indeterminate'] or 0)}"
                f"  dark {counts['dark']}  indeterminate {counts['indeterminate']}\n"
                f"  chance-match {chance:.1%}"
                f"  ({'discriminating' if counts['discriminating'] else 'NOT DISCRIMINATING, dark calls withheld'})\n"
                f"  large-vessel recall "
                f"{counts['recall_large_detected']}/{counts['recall_large_total']}\n"
                if chance is not None
                else f"{label}\n  chance-match unmeasurable (no detections)\n"
            )

        if args.dry_run:
            await session.rollback()
            print("dry run — rolled back")
        else:
            await session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
