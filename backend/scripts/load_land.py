"""Load coastline polygons into PostGIS and re-run the land mask. Spends 0 PU.

The mask is geometric, so this touches no imagery and no model: it reads the
detection points already in `sar_detections` and re-flags them. Re-running
*detection* would cost a fresh pixel fetch (the full-resolution chip is never
persisted), but re-running the *mask* is free and can be repeated as often as
you like while tuning the buffer.

Source data — OSM land polygons, any **WGS84 / EPSG:4326** shapefile from:

    https://osmdata.openstreetmap.de/data/land-polygons.html

Both the "complete" and "split" WGS84 builds work; the Mercator (3857) ones do
not — the loader assumes lon/lat degrees and will find no overlap. "complete" is
what this was tested against. "split" pre-chops the continents into ~1° tiles,
so it avoids the 183 MB Eurasia polygon and loads faster, but the difference is
about a minute either way and not worth re-downloading for.

GSHHG full-resolution shorelines work too and are ~7x smaller, but are coarser
in exactly the places that matter here — harbour walls, breakwaters, the rocky
inlets around Musandam. OSM is worth the download.

Only geometry overlapping some ROI's sar_bbox is inserted, clipped to it, so
the table lands at a few MB regardless of the source size and the deployed
image carries no shapefile.

    cd backend
    DATABASE_URL=postgresql+asyncpg://dvd:dvd@localhost:5432/dvd \\
        .venv/bin/python scripts/load_land.py ~/Downloads/land-polygons-complete-4326/land_polygons.shp

    # retune the buffer against what is already loaded — no shapefile needed
    ... .venv/bin/python scripts/load_land.py --buffer-m 100
    ... .venv/bin/python scripts/load_land.py --buffer-m 100 --dry-run

DATABASE_URL is read from backend/.env if set there, so in practice the env var
above is only needed when running against a different database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

# `app` lives one level up from scripts/. Added here so the script runs from any
# working directory without the caller having to export PYTHONPATH; pytest gets
# the same path from `pythonpath = ["."]` in pyproject.toml.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.landmask import apply_schema, mark_land_detections  # noqa: E402
from app.rois import ROIS, Bbox  # noqa: E402

# Inserted one statement at a time, not batched. The size distribution is
# violently skewed: of the ~370 polygons that survive the ROI filter, the median
# is 0.7 KB but the largest is Eurasia at 183 MB and the runner-up 28 MB.
# Batching by count would hand PostGIS a single ~216 MB statement; one at a time
# bounds each statement by the largest polygon, which is known to work (~43 s for
# Eurasia, the whole load ~1 min).
PROGRESS_EVERY = 50

INSERT_CLIPPED = text(
    """
    INSERT INTO land_polygons (geom)
    SELECT ST_Multi(clipped)::geography
    FROM (
        SELECT ST_Intersection(
                   ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326)),
                   ST_GeomFromText(:clip, 4326)
               ) AS clipped
    ) t
    -- A polygon whose bbox overlaps can still meet the box only along an edge;
    -- keep areal intersections only.
    WHERE NOT ST_IsEmpty(clipped) AND ST_Dimension(clipped) = 2
    """
)


def bboxes_overlap(a: Bbox, b: Bbox) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def clip_multipolygon_wkt(bboxes: list[Bbox]) -> str:
    """Every sar_bbox as one MULTIPOLYGON — the region worth keeping land for."""
    parts = []
    for min_lon, min_lat, max_lon, max_lat in bboxes:
        ring = (
            f"({min_lon} {min_lat},{max_lon} {min_lat},{max_lon} {max_lat},"
            f"{min_lon} {max_lat},{min_lon} {min_lat})"
        )
        parts.append(f"({ring})")
    return f"MULTIPOLYGON({','.join(parts)})"


def read_overlapping(shapefile: Path, bboxes: list[Bbox]) -> list[str]:
    """GeoJSON for shapes whose bbox meets any ROI box. Filters before the DB.

    The source has ~800k polygons worldwide; all but a few thousand are nowhere
    near an ROI, and pyshp's per-shape bbox makes discarding them cheap.
    """
    try:
        import shapefile as pyshp  # pyshp
    except ImportError:
        sys.exit(
            "pyshp not installed — pip install -r requirements-dev.txt "
            "(or pip install pyshp)"
        )

    out: list[str] = []
    with pyshp.Reader(str(shapefile)) as reader:
        total = len(reader)
        for i, shape in enumerate(reader.iterShapes()):
            if i % 100_000 == 0:
                print(f"  scanned {i:,}/{total:,} shapes, kept {len(out):,}", flush=True)
            if any(bboxes_overlap(tuple(shape.bbox), b) for b in bboxes):
                out.append(json.dumps(shape.__geo_interface__))
    return out


async def load(shapefile: Path, bboxes: list[Bbox]) -> int:
    print(f"reading {shapefile} …", flush=True)
    geojsons = read_overlapping(shapefile, bboxes)
    print(f"{len(geojsons):,} polygons overlap an ROI box", flush=True)
    if not geojsons:
        sys.exit(
            "no land polygons overlap any sar_bbox — wrong shapefile, or it is "
            "not in EPSG:4326 (the WGS84 download, not the Mercator one)"
        )

    clip = clip_multipolygon_wkt(bboxes)
    async with SessionLocal() as session:
        await session.execute(text("TRUNCATE land_polygons"))
        for i, geojson in enumerate(geojsons, start=1):
            await session.execute(INSERT_CLIPPED, {"geojson": geojson, "clip": clip})
            if i % PROGRESS_EVERY == 0 or i == len(geojsons):
                print(f"  inserted {i:,}/{len(geojsons):,}", flush=True)
        await session.commit()
        return (await session.execute(text("SELECT count(*) FROM land_polygons"))).scalar() or 0


BREAKDOWN = text(
    """
    SELECT s.roi,
           count(*) FILTER (WHERE d.on_land) AS masked,
           count(*) AS total
    FROM sar_detections d
    JOIN sar_scenes s ON s.id = d.scene_id
    GROUP BY s.roi
    ORDER BY s.roi
    """
)


async def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "shapefile",
        type=Path,
        nargs="?",
        help="land_polygons.shp (EPSG:4326). Omit to re-mask against already-loaded geometry.",
    )
    parser.add_argument(
        "--buffer-m",
        type=float,
        default=settings.land_mask_buffer_m,
        help=f"seaward metres added to the coastline (default {settings.land_mask_buffer_m:g}, "
             "from LAND_MASK_BUFFER_M)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what this buffer would mask, then roll back",
    )
    args = parser.parse_args()

    bboxes = [roi.sar_bbox for roi in ROIS.values()]

    async with engine.begin() as conn:
        await apply_schema(conn)

    if args.shapefile:
        count = await load(args.shapefile, bboxes)
        print(f"\nland_polygons: {count:,} rows clipped to {len(bboxes)} ROI boxes")

    # Re-mask every detection already stored — this is the "rerun on existing
    # analyses" path, and it pulls no new imagery.
    async with SessionLocal() as session:
        masked = await mark_land_detections(session, args.buffer_m)
        rows = (await session.execute(BREAKDOWN)).mappings().all()
        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()

    verb = "would mask" if args.dry_run else "masked"
    print(f"\nbuffer {args.buffer_m:g} m {verb} {masked} detection(s):")
    if not rows:
        print("  (no detections stored yet)")
    for row in rows:
        print(f"  {row['roi']:<22} {row['masked']:>4} / {row['total']:<4} masked")
    if args.dry_run:
        print("\ndry run — rolled back")


if __name__ == "__main__":
    asyncio.run(main())
