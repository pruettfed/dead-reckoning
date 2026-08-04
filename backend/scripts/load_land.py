"""Load coastline polygons into PostGIS and re-run the land mask. Spends 0 PU.

The mask is geometric, so this touches no imagery and no model: it reads the
detection points already in `sar_detections` and re-flags them. Re-running
*detection* would cost a fresh pixel fetch (the full-resolution chip is never
persisted), but re-running the *mask* is free and can be repeated as often as
you like while tuning the buffer.

Source data — OSM land polygons, WGS84 / EPSG:4326, from
https://osmdata.openstreetmap.de/data/land-polygons.html . `--download` fetches
the "split" build automatically (continents pre-chopped to ~1° tiles, avoiding
the 183 MB Eurasia polygon that the "complete" build streams as one shape;
~900 MB, ~1 min on a fast link). Pass a local .shp instead if you already have
one — GSHHG shorelines work too but are coarser in exactly the places that
matter here (harbour walls, breakwaters, the rocky inlets around Musandam).

Only geometry overlapping some ROI's sar_bbox is inserted, clipped to it, so
`land_polygons` lands at a few MB regardless of source size. Every load also
exports that clipped result to a small GeoJSON file (`--export`, default
`backend/land/land_polygons.geojson`) meant to be committed: `landmask.
load_bundled_polygons` loads it back in automatically on boot when
`land_polygons` is empty, so a fresh deploy has coastline data with no
manual step and no 900 MB download in the deploy path. The buffer
(`LAND_MASK_BUFFER_M`, from `.env` by default) is not baked into that file —
it only affects the runtime mark step below, so retuning it never requires
re-exporting.

    cd backend
    .venv/bin/python scripts/load_land.py --download

    # or against a shapefile you already have
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
import tempfile
import zipfile
from pathlib import Path

import httpx
from sqlalchemy import text

# `app` lives one level up from scripts/. Added here so the script runs from any
# working directory without the caller having to export PYTHONPATH; pytest gets
# the same path from `pythonpath = ["."]` in pyproject.toml.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.landmask import apply_schema, mark_land_detections  # noqa: E402
from app.rois import ROIS, Bbox  # noqa: E402

# "split" build: same coverage as "complete", chopped to ~1° tiles so no single
# shape is the 183 MB Eurasia polygon.
DOWNLOAD_URL = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"

# Must match `landmask.BUNDLED_GEOJSON_PATH`. Committed to the repo so a fresh
# deploy boots with coastline data already loaded, instead of needing this
# script run by hand against production. Lives under `app/` (not a sibling
# `land/`) because the Dockerfile only `COPY app ./app` into the image — a
# path outside `app/` would never reach a built container at all.
DEFAULT_EXPORT_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "land" / "land_polygons.geojson"
)

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


def download_shapefile(dest_dir: Path) -> Path:
    """Fetch and unzip the OSM WGS84 land polygons build into `dest_dir`.

    No persistent cache — a fresh download is a ~1 min tax for a script that
    is run rarely (once for a new deploy target, occasionally to refresh
    coastline data), and skipping cache invalidation logic outweighs that.
    """
    zip_path = dest_dir / "land-polygons-split-4326.zip"
    print(f"downloading {DOWNLOAD_URL} …", flush=True)
    with httpx.stream(
        "GET", DOWNLOAD_URL, follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=None),
    ) as resp:
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    print(f"unzipping {zip_path.stat().st_size / 1e6:.0f} MB …", flush=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    shapefiles = list(dest_dir.rglob("*.shp"))
    if not shapefiles:
        sys.exit("downloaded archive contained no .shp file")
    return shapefiles[0]


EXPORT_QUERY = text("SELECT ST_AsGeoJSON(geom::geometry) FROM land_polygons ORDER BY id")


async def export_geojson(path: Path) -> int:
    """Dump the clipped `land_polygons` rows to a GeoJSON FeatureCollection.

    Meant to be committed — `landmask.load_bundled_polygons` reads this file
    back in on boot when the table is empty, which is how a deploy gets
    coastline data without running this script (or its 900 MB download)
    against production.
    """
    async with SessionLocal() as session:
        rows = (await session.execute(EXPORT_QUERY)).scalars().all()
    features = [{"type": "Feature", "properties": {}, "geometry": json.loads(g)} for g in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return len(features)


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
        help="local land_polygons.shp (EPSG:4326). Omit (with --download, or alone) "
             "to re-mask against already-loaded geometry.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="fetch the OSM land polygons shapefile automatically instead of a local path",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=DEFAULT_EXPORT_PATH,
        help=f"where to write the clipped GeoJSON for committing (default {DEFAULT_EXPORT_PATH}); "
             "only written when polygons are (re)loaded, i.e. a shapefile is given or --download is used",
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
    if args.shapefile and args.download:
        sys.exit("pass either a shapefile path or --download, not both")

    bboxes = [roi.sar_bbox for roi in ROIS.values()]

    async with engine.begin() as conn:
        await apply_schema(conn)

    with tempfile.TemporaryDirectory() as tmp:
        if args.download:
            args.shapefile = download_shapefile(Path(tmp))
        if args.shapefile:
            count = await load(args.shapefile, bboxes)
            print(f"\nland_polygons: {count:,} rows clipped to {len(bboxes)} ROI boxes")
            exported = await export_geojson(args.export)
            print(
                f"exported {exported:,} rows to {args.export} — commit this file so a "
                "fresh deploy boots with coastline data already loaded"
            )

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
