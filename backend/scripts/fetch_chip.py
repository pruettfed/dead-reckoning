"""Fetch one Sentinel-1 chip + AIS snapshot for an ROI — the only PU spend.

Buys a pass at a wide dB window (-35, +5) so any narrower candidate window is an exact
offline restretch of the uint8 (see bench_detector.py). Saves a .npy plus a .json sidecar
with the AIS snapshot at the scene's acquisition time — captured now because
ais_retention_days = 2 deletes it 48 h after fetch. Reuses the production path
(find_target_scene coverage guard, estimate_pu, fetch_scene_pixels).

    cd backend
    DATABASE_URL=postgresql+asyncpg://dvd:dvd@localhost:5432/dvd \\
    CDSE_CLIENT_ID=... CDSE_CLIENT_SECRET=... \\
        .venv/bin/python scripts/fetch_chip.py singapore_strait        # prompts before spending
        .venv/bin/python scripts/fetch_chip.py malta_hurds_bank --yes  # no prompt

--no-ais skips the fused-ROI AIS-coverage gate (fetch before AIS has spanned a pass); the
snapshot is then empty, so use it only for detection-count comparison, not matched/missed.
"""

from __future__ import annotations

import asyncio
import json
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import app.* when run as a script

from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.pipeline import find_target_scene
from app.rois import get_roi
from app.sar import SarScene, estimate_pu, fetch_scene_pixels, plan_fetch_grid

# Wide fetch window; narrower candidates restretch offline at 0 PU. Quantization is
# 40 dB / 254 = 0.16 dB, well under the speckle floor.
CALIB_DB_MIN, CALIB_DB_MAX = -35.0, 5.0

NODATA_VALUE = 0  # valid data occupies 1-255; 0 is reserved for nodata.

CHIP_DIR = Path(__file__).resolve().parents[1] / "data" / "chips"  # gitignored


_CALIB_TEMPLATE = string.Template(
    """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV", "dataMask"] }],
    output: { bands: 1, sampleType: "UINT8" },
  };
}
function evaluatePixel(sample) {
  if (sample.dataMask === 0) return [0];
  var db = 10 * Math.log10(Math.max(sample.VV, 1e-10));
  var scaled = (db - $db_min) / ($db_max - $db_min);
  return [1 + 254 * Math.max(0, Math.min(1, scaled))];
}
"""
)


def calibration_evalscript(db_min: float, db_max: float) -> str:
    """Wide-window evalscript: valid data → 1-255, nodata (dataMask 0) → 0."""
    return _CALIB_TEMPLATE.substitute(db_min=db_min, db_max=db_max)


_SNAPSHOT_AIS = text(
    """
    SELECT mmsi,
           time,
           ST_X(location::geometry) AS lon,
           ST_Y(location::geometry) AS lat,
           sog, cog
    FROM ais_positions
    WHERE time BETWEEN CAST(:sensed_at AS timestamptz) - make_interval(secs => :window_s)
                   AND CAST(:sensed_at AS timestamptz) + make_interval(secs => :window_s)
      AND ST_Within(
              location::geometry,
              ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)
          )
    ORDER BY time
    """
)


async def snapshot_ais(
    session,
    bbox: tuple[float, float, float, float],
    sensed_at: datetime,
    window_hours: float,
) -> list[dict]:
    """Every AIS position inside `bbox` within ±`window_hours` of `sensed_at`."""
    min_lon, min_lat, max_lon, max_lat = bbox
    rows = (
        await session.execute(
            _SNAPSHOT_AIS,
            {
                "sensed_at": sensed_at,
                "window_s": window_hours * 3600,
                "min_lon": min_lon,
                "min_lat": min_lat,
                "max_lon": max_lon,
                "max_lat": max_lat,
            },
        )
    ).mappings().all()
    return [
        {
            "mmsi": r["mmsi"],
            "time": r["time"].astimezone(timezone.utc).isoformat(),
            "lon": r["lon"],
            "lat": r["lat"],
            "sog": r["sog"],
            "cog": r["cog"],
        }
        for r in rows
    ]


def save_chip(chip, scene: SarScene, roi_name: str, meta_extra: dict, out_dir: Path) -> Path:
    """Write `{roi}_{sensed_at}.npy` + `.json` sidecar; return the .npy path."""
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{roi_name}_{scene.sensed_at:%Y%m%dT%H%M%S}"
    npy_path = out_dir / f"{stem}.npy"
    np.save(npy_path, chip.pixels)
    meta = {
        "roi": roi_name,
        "scene_id": scene.id,
        "scene_name": scene.name,
        "sensed_at": scene.sensed_at.astimezone(timezone.utc).isoformat(),
        "bbox": list(chip.bbox),
        "width": chip.width,
        "height": chip.height,
        "db_min": CALIB_DB_MIN,
        "db_max": CALIB_DB_MAX,
        "nodata_value": NODATA_VALUE,
        "speckle_filter": None,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        **meta_extra,
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, indent=2))
    return npy_path


async def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    assume_yes = "--yes" in sys.argv[1:]
    require_ais = "--no-ais" not in sys.argv[1:]
    if len(args) != 1:
        print(__doc__)
        return 2
    roi = get_roi(args[0])

    scene, status = await find_target_scene(roi, require_ais=require_ais)
    grid = plan_fetch_grid(roi.sar_bbox)
    pu = estimate_pu(grid)
    print(
        f"{roi.name}: {scene.name}\n"
        f"  sensed_at {scene.sensed_at.isoformat()}  (DB status: {status or 'new'})\n"
        f"  {grid.width}x{grid.height} px in {len(grid.tiles)} tiles  ~{pu:.0f} PU"
    )
    if not assume_yes:
        if input("  spend these PU? [y/N] ").strip().lower() not in ("y", "yes"):
            print("aborted — no PU spent")
            return 1

    settings = get_settings()
    chip = await fetch_scene_pixels(
        scene,
        roi.sar_bbox,
        evalscript=calibration_evalscript(CALIB_DB_MIN, CALIB_DB_MAX),
        speckle_filter=None,
    )
    async with SessionLocal() as session:
        ais = await snapshot_ais(
            session, roi.sar_bbox, scene.sensed_at, settings.fusion_max_time_delta_hours
        )

    path = save_chip(
        chip, scene, roi.name, {"pu_estimate": round(pu, 1), "ais": ais}, CHIP_DIR
    )
    print(f"saved {path}  ({chip.width}x{chip.height}, {len(ais)} AIS positions)")
    if not ais:
        print("  WARNING: no AIS in snapshot — benchmark will have no ground truth")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
