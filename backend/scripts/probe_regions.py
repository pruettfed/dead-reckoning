"""Measure usable Sentinel-1 passes and PU cost per ROI. Spends 0 PU.

Catalog search is free, so this is safe to run any time and is the only honest
way to price the registry. Two numbers matter and they are not the same:

  passes  — acquisitions whose swath touches sar_bbox at all
  usable  — those whose mosaicked footprint covers >= MIN_FOOTPRINT_COVERAGE of
            it. A pass that merely clips the corner costs full PU and returns a
            black chip, so only usable passes should be budgeted for.

Needs PostGIS for the geometry (reuses the pipeline's own coverage query, so the
threshold can never drift from what analysis enforces). Run with the stack up:

    cd backend
    DATABASE_URL=postgresql+asyncpg://dvd:dvd@localhost:5432/dvd \\
        .venv/bin/python scripts/probe_regions.py
    ... scripts/probe_regions.py 56.15,26.35,56.65,26.70   # price a candidate box

Three failure modes this catches that estimating cannot:
  - Open ocean gets no IW coverage at all (0 passes).
  - An open-water corridor gets grazed, never imaged (Gulf of Aden: median 3%).
  - Shrinking below the inter-track spacing drops passes off a cliff.
"""

from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timedelta, timezone

import httpx

from app.database import SessionLocal
from app.pipeline import MIN_FOOTPRINT_COVERAGE, footprint_coverage
from app.rois import ROIS, Bbox
from app.sar import PU_MONTHLY_BUDGET, SarScene, estimate_pu, plan_fetch_grid, search_scenes

DAYS = 30
SAME_PASS_SECONDS = 1200  # one pass yields several GRDH slices sharing a timestamp


def group_passes(scenes: list[SarScene]) -> list[list[SarScene]]:
    ordered = sorted(scenes, key=lambda s: s.sensed_at)
    groups: list[list[SarScene]] = []
    for scene in ordered:
        if groups and (scene.sensed_at - groups[-1][-1].sensed_at).total_seconds() <= SAME_PASS_SECONDS:
            groups[-1].append(scene)
        else:
            groups.append([scene])
    return groups


def size_km(bbox: Bbox) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    mid = math.radians((min_lat + max_lat) / 2)
    return (
        f"{(max_lon - min_lon) * 111.32 * math.cos(mid):.0f}"
        f"x{(max_lat - min_lat) * 111.32:.0f}"
    )


async def probe(session, client: httpx.AsyncClient, label: str, bbox: Bbox) -> dict:
    now = datetime.now(tz=timezone.utc)
    scenes = await search_scenes(bbox, now - timedelta(days=DAYS), now, client=client)
    groups = group_passes(scenes)

    usable, best_covs = 0, []
    for group in groups:
        # The pipeline walks candidates newest-first and takes the first that
        # qualifies, so a pass counts if any of its slices works as the anchor.
        best = 0.0
        for anchor in group:
            best = max(best, await footprint_coverage(session, group, anchor, bbox))
            if best >= MIN_FOOTPRINT_COVERAGE:
                break
        best_covs.append(best)
        usable += best >= MIN_FOOTPRINT_COVERAGE

    pu = estimate_pu(plan_fetch_grid(bbox))
    best_covs.sort()
    return {
        "label": label,
        "passes": len(groups),
        "usable": usable,
        "median_cov": best_covs[len(best_covs) // 2] if best_covs else 0.0,
        "pu": pu,
        "pu_month": usable * pu,
        "size": size_km(bbox),
    }


async def main() -> int:
    args = sys.argv[1:]
    if args:
        targets = [(a, tuple(float(v) for v in a.split(","))) for a in args]
    else:
        targets = [(f"{n} [{r.mode}]", r.sar_bbox) for n, r in ROIS.items()]

    async with SessionLocal() as session, httpx.AsyncClient(timeout=60.0) as client:
        results = [await probe(session, client, *t) for t in targets]

    print(
        f"{'roi':28} {'passes':>6} {'usable':>6} {'median':>7} "
        f"{'PU/pass':>8} {'PU/mo':>7}  {'km':>9}"
    )
    print("-" * 82)
    for r in sorted(results, key=lambda r: -r["pu_month"]):
        print(
            f"{r['label']:28} {r['passes']:>6} {r['usable']:>6} "
            f"{r['median_cov'] * 100:>6.0f}% {r['pu']:>8.0f} {r['pu_month']:>7.0f}  {r['size']:>9}"
        )

    total = sum(r["pu_month"] for r in results)
    print("-" * 82)
    print(
        f"{'TOTAL':28} {'':>6} {'':>6} {'':>7} {'':>8} {total:>7.0f}  "
        f"({total / PU_MONTHLY_BUDGET * 100:.0f}% of {PU_MONTHLY_BUDGET:,})"
    )

    if not args:
        stale = [
            (name, r["usable"], roi.passes_per_month)
            for (name, roi), r in zip(ROIS.items(), results)
            if r["usable"] != roi.passes_per_month
        ]
        if stale:
            print("\npasses_per_month in rois.py differs from measured (update it):")
            for name, measured, declared in stale:
                print(f"  {name}: measured {measured} usable, declared {declared}")

        weak = [(name, r) for (name, _), r in zip(ROIS.items(), results) if r["usable"] < 5]
        if weak:
            print("\nfew usable passes — consider repositioning or dropping:")
            for name, r in weak:
                print(f"  {name}: {r['usable']}/{r['passes']} usable, median {r['median_cov']*100:.0f}%")

    if total > PU_MONTHLY_BUDGET:
        print(f"\nOVER BUDGET by {total - PU_MONTHLY_BUDGET:,.0f} PU/month")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
