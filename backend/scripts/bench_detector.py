"""Score checkpoints / windows / speckle filters on cached chips. Spends 0 PU.

Reads a chip fetched once by fetch_chip.py plus its AIS sidecar. The chip was bought at
the wide window (-35, +5 dB), so `render()` restretches it to any narrower candidate and
runs the production detector verbatim (load_detector + run_detection) — real tiling, NMS,
imgsz=800. Scores detections against the frozen AIS snapshot with a read-only ST_DWithin
query; also prints a low-threshold confidence histogram (a fat pile below 0.25 on real AIS
means the fix is a threshold, not a retrain). Needs PostGIS up; no CDSE creds, no PU.

    cd backend
    DATABASE_URL=postgresql+asyncpg://dvd:dvd@localhost:5432/dvd \\
        .venv/bin/python scripts/bench_detector.py \\
            --chip data/chips/singapore_strait_20260715T113045.npy \\
            --weights models/sar_ship.pt models/xview3_s.pt \\
            --conf 0.05 --save-images data/chips/annotated
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import app.* when run as a script

import numpy as np
from sqlalchemy import text

from app.config import get_settings
from app.detect import load_detector, run_detection
from app.landmask import land_loaded
from app.sar import SarChip

# Sub-threshold histogram bins: the 0.10-0.25 span is where a compressed-but-present
# confidence distribution hides.
HIST_EDGES = (0.0, 0.10, 0.15, 0.20, 0.25, 0.40, 0.70, 1.01)

# Marker colours by confidence bucket, for --save-images.
BUCKET_COLOR = {"high": (0, 255, 0), "medium": (255, 210, 0), "low": (255, 70, 70)}


def save_annotated(arr: np.ndarray, dets, meta: dict, out_path: Path) -> None:
    """Write the rendered chip with a confidence-coloured ring on each detection
    (lon/lat centroid → pixel via the chip's linear mapping). Full resolution."""
    from PIL import Image, ImageDraw

    min_lon, min_lat, max_lon, max_lat = meta["bbox"]
    w, h = meta["width"], meta["height"]
    img = Image.fromarray(arr, mode="L").convert("RGB")
    draw = ImageDraw.Draw(img)
    r = max(8, round(min(w, h) / 250))  # visible without swamping small chips
    for d in dets:
        col = (d.lon - min_lon) / (max_lon - min_lon) * w
        row = (max_lat - d.lat) / (max_lat - min_lat) * h
        draw.ellipse([col - r, row - r, col + r, row + r],
                     outline=BUCKET_COLOR.get(d.bucket, (0, 255, 0)), width=3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def load_chip(npy_path: str | Path) -> tuple[np.ndarray, dict]:
    """Load a calibration chip and its .json sidecar (must sit beside the .npy)."""
    npy_path = Path(npy_path)
    pixels = np.load(npy_path)
    meta = json.loads(npy_path.with_suffix(".json").read_text())
    return pixels, meta


def _db_from_uint8(pixels: np.ndarray, meta: dict) -> np.ndarray:
    """Invert the calibration encoding: valid data 1-255 → dB, nodata 0 → just below."""
    lo_cal, hi_cal = meta["db_min"], meta["db_max"]
    u = pixels.astype(np.float64)
    return lo_cal + ((u - 1.0) / 254.0) * (hi_cal - lo_cal)


def _db_to_uint8(db: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Scale dB onto 0-255 for the given window — matches production EVALSCRIPT."""
    scaled = np.clip((db - lo) / (hi - lo), 0.0, 1.0)
    return np.rint(255.0 * scaled).astype(np.uint8)


def restretch(pixels: np.ndarray, meta: dict, *, lo: float, hi: float) -> np.ndarray:
    """Recover the (lo, hi) render from the wide-window chip. Requires the sidecar's
    LO <= lo < hi <= HI; a window outside it can't be recovered (caller error)."""
    LO, HI = meta["db_min"], meta["db_max"]
    if not (LO <= lo < hi <= HI):
        raise ValueError(
            f"window ({lo}, {hi}) must lie within calibration window ({LO}, {HI})"
        )
    return _db_to_uint8(_db_from_uint8(pixels, meta), lo, hi)


def _box_mean_axis(a: np.ndarray, size: int, axis: int) -> np.ndarray:
    """Uniform box mean along one axis via an integral (cumsum) image, edge-padded."""
    r0 = size // 2
    r1 = size - 1 - r0
    pad = [(r0, r1) if ax == axis else (0, 0) for ax in range(a.ndim)]
    cs = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
    cs = np.concatenate([np.zeros_like(np.take(cs, [0], axis=axis)), cs], axis=axis)
    n = a.shape[axis]
    upper = np.take(cs, np.arange(size, size + n), axis=axis)
    lower = np.take(cs, np.arange(0, n), axis=axis)
    return (upper - lower) / size


def _box_mean(a: np.ndarray, size: int) -> np.ndarray:
    return _box_mean_axis(_box_mean_axis(a, size, 0), size, 1)


def lee_filter(linear: np.ndarray, size: int) -> np.ndarray:
    """Classic Lee speckle filter on linear σ⁰ — an offline stand-in for Sentinel Hub's
    speckleFilter, for ranking candidate window/LEE settings before a confirming fetch."""
    mean = _box_mean(linear, size)
    var = np.maximum(_box_mean(linear * linear, size) - mean * mean, 0.0)
    overall = float(linear.var())
    weights = var / (var + overall + 1e-12)
    return mean + weights * (linear - mean)


def render(
    pixels: np.ndarray, meta: dict, *, lo: float, hi: float, lee: int
) -> np.ndarray:
    """Chip → uint8 at window (lo, hi), optionally LEE-filtered in the linear domain."""
    if lee and lee > 1:
        db = _db_from_uint8(pixels, meta)
        linear = np.power(10.0, db / 10.0)
        linear = lee_filter(linear, lee)
        db = 10.0 * np.log10(np.maximum(linear, 1e-10))
        return _db_to_uint8(db, lo, hi)
    return restretch(pixels, meta, lo=lo, hi=hi)


_SCORE = text(
    """
    WITH det AS (
        SELECT ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography AS g
        FROM unnest(CAST(:det_lon AS float8[]), CAST(:det_lat AS float8[])) AS t(lon, lat)
    ),
    ais AS (
        SELECT ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography AS g
        FROM unnest(CAST(:ais_lon AS float8[]), CAST(:ais_lat AS float8[])) AS t(lon, lat)
    )
    SELECT
        (SELECT count(*) FROM det d
           WHERE EXISTS (SELECT 1 FROM ais a WHERE ST_DWithin(d.g, a.g, :max_distance_m)))
            AS matched_det,
        (SELECT count(*) FROM ais a
           WHERE NOT EXISTS (SELECT 1 FROM det d WHERE ST_DWithin(a.g, d.g, :max_distance_m)))
            AS missed_ais
    """
)

_ON_LAND = text(
    """
    SELECT count(*)
    FROM unnest(CAST(:det_lon AS float8[]), CAST(:det_lat AS float8[])) AS t(lon, lat)
    WHERE EXISTS (
        SELECT 1 FROM land_polygons l
        WHERE ST_DWithin(l.geom, ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography, 0)
    )
    """
)


async def score(session, dets, meta: dict, *, max_distance_m: float) -> dict:
    """Match detections against the sidecar's AIS snapshot in PostGIS (read-only).

    Returns matched (the ranking metric), missed_ais (recall diagnostic), unmatched
    (false alarm or genuine dark vessel), on_land (0 when coastline isn't loaded).
    """
    det_lon = [d.lon for d in dets]
    det_lat = [d.lat for d in dets]
    ais_lon = [a["lon"] for a in meta["ais"]]
    ais_lat = [a["lat"] for a in meta["ais"]]
    row = (
        await session.execute(
            _SCORE,
            {
                "det_lon": det_lon,
                "det_lat": det_lat,
                "ais_lon": ais_lon,
                "ais_lat": ais_lat,
                "max_distance_m": max_distance_m,
            },
        )
    ).mappings().one()
    on_land = 0
    if await land_loaded(session):
        on_land = (
            await session.execute(_ON_LAND, {"det_lon": det_lon, "det_lat": det_lat})
        ).scalar() or 0
    return {
        "total_det": len(dets),
        "total_ais": len(ais_lon),
        "matched": row["matched_det"],
        "missed_ais": row["missed_ais"],
        "unmatched": len(dets) - row["matched_det"],
        "on_land": on_land,
    }


def confidence_histogram(confs: list[float]) -> str:
    counts, _ = np.histogram(confs, bins=HIST_EDGES)
    return " ".join(
        f"[{HIST_EDGES[i]:.2f}-{HIST_EDGES[i + 1]:.2f}) {c}"
        for i, c in enumerate(counts)
    )


def _parse_window(s: str) -> tuple[float, float]:
    lo, hi = (float(v) for v in s.split(","))
    return lo, hi


async def main() -> int:
    from app.database import SessionLocal  # deferred: keeps pure fns import-clean

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chip", required=True)
    parser.add_argument("--weights", nargs="+", required=True)
    parser.add_argument("--window", action="append", type=_parse_window, dest="windows")
    parser.add_argument("--lee", action="append", type=int, dest="lees")
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--save-images", type=Path, metavar="DIR",
                        help="write an annotated PNG per model/window/lee to DIR")
    args = parser.parse_args()
    windows = args.windows or [(-25.0, 0.0)]
    lees = args.lees or [0]

    pixels, meta = load_chip(args.chip)
    max_distance_m = get_settings().match_radius_m

    async with SessionLocal() as session:
        loaded = await land_loaded(session)
    print(
        f"chip {Path(args.chip).name}  {meta['width']}x{meta['height']}  "
        f"roi={meta['roi']}  sensed_at={meta['sensed_at']}\n"
        f"calibration window ({meta['db_min']}, {meta['db_max']})  "
        f"AIS positions={len(meta['ais'])}  land_polygons={'loaded' if loaded else 'NOT loaded'}  "
        f"conf>={args.conf}\n"
    )
    if not meta["ais"]:
        print("WARNING: sidecar has no AIS — matched/missed are meaningless\n")

    for weights in args.weights:
        detector = load_detector(weights, args.conf)
        for lo, hi in windows:
            for lee in lees:
                arr = render(pixels, meta, lo=lo, hi=hi, lee=lee)
                chip = SarChip(
                    pixels=arr, bbox=tuple(meta["bbox"]),
                    width=meta["width"], height=meta["height"],
                )
                dets = run_detection(chip, detector)
                if args.save_images:
                    stem = f"{Path(args.chip).stem}__{Path(weights).stem}__w{lo}_{hi}__lee{lee}"
                    out_png = args.save_images / f"{stem}.png"
                    save_annotated(arr, dets, meta, out_png)
                    print(f"  wrote {out_png}")
                async with SessionLocal() as session:
                    s = await score(session, dets, meta, max_distance_m=max_distance_m)
                print(
                    f"{Path(weights).name}  window=({lo},{hi})  lee={lee}\n"
                    f"  matched={s['matched']}/{s['total_ais']} AIS   "
                    f"missed_ais={s['missed_ais']}   "
                    f"unmatched_det={s['unmatched']} ({s['on_land']} on land)   "
                    f"total_det={s['total_det']}\n"
                    f"  conf hist: {confidence_histogram([d.confidence for d in dets])}\n"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
