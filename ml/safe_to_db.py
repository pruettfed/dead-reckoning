"""Calibrate a Sentinel-1 GRD .SAFE product to a VV sigma0-dB GeoTIFF.

SARFish ships raw Level-1 GRD (uint16 amplitude); the pipeline fetches calibrated
backscatter (SIGMA0_ELLIPSOID, rendered as 10*log10(sigma0)). This applies ESA's
radiometric calibration — sigma0 = DN**2 / sigmaNought**2, dB = 10*log10(sigma0) —
and writes {out}/{scene_id}/VV_dB.tif, the exact layout and quantity prepare_xview3.py
consumes (xView3's own VV_dB.tif is the same thing).

    python ml/safe_to_db.py --safe S1A_..._GRDH_...SAFE.zip \
      --labels /content/xview3/labels.csv --out /content/xview3
"""

import argparse
import csv
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np

NODATA_DN = 0  # ESA marks absent samples with 0; calibrate_to_db maps them to NaN.


def find_members(names: list[str]) -> tuple[str, str]:
    """Locate the VV measurement raster and its calibration XML inside a .SAFE."""
    def pick(kind: str, suffix: str) -> str:
        hits = [
            n for n in names
            if f"/{kind}/" in f"/{n}"
            and Path(n).suffix.lower() == suffix
            and "-vv-" in Path(n).name.lower()
        ]
        if kind == "calibration":
            hits = [n for n in hits if Path(n).name.lower().startswith("calibration-")]
        if not hits:
            raise SystemExit(f"no VV {kind} {suffix} found in the .SAFE; members: {names[:6]}")
        return sorted(hits, key=len)[0]

    return pick("measurement", ".tiff"), pick("calibration", ".xml")


def parse_calibration_lut(xml_bytes: bytes) -> tuple[np.ndarray, list[np.ndarray], list[np.ndarray]]:
    """Parse the calibration XML → (lines, pixels_per_vector, sigmaNought_per_vector)."""
    root = ET.fromstring(xml_bytes)
    vectors = root.find("calibrationVectorList")
    if vectors is None or len(vectors) == 0:
        raise SystemExit("calibration XML has no calibrationVectorList")

    lines, pixels, sigmas = [], [], []
    for vector in vectors:
        lines.append(int(vector.findtext("line")))
        pixels.append(np.array([int(v) for v in vector.findtext("pixel").split()], dtype=np.float64))
        sigmas.append(np.array([float(v) for v in vector.findtext("sigmaNought").split()], dtype=np.float64))
    order = np.argsort(lines)
    return (
        np.array(lines, dtype=np.float64)[order],
        [pixels[i] for i in order],
        [sigmas[i] for i in order],
    )


def column_lut(
    lines: np.ndarray, pixels: list[np.ndarray], sigmas: list[np.ndarray], width: int
) -> np.ndarray:
    """(n_vectors, width): each calibration vector's sigmaNought across the full width."""
    full_cols = np.arange(width, dtype=np.float64)
    return np.stack(
        [np.interp(full_cols, pixels[v], sigmas[v]) for v in range(len(lines))]
    ).astype(np.float32)


def row_lut(lines: np.ndarray, columns: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """(len(rows), width): blend the per-vector column LUTs by image line; ends held flat."""
    rows = np.asarray(rows, dtype=np.float64)
    if len(lines) == 1:
        return np.broadcast_to(columns[0], (len(rows), columns.shape[1])).copy()
    upper = np.clip(np.searchsorted(lines, rows), 1, len(lines) - 1)
    lower = upper - 1
    span = lines[upper] - lines[lower]
    weight = ((rows - lines[lower]) / span)[:, None].astype(np.float32)
    return columns[lower] * (1.0 - weight) + columns[upper] * weight


def expand_lut(
    lines: np.ndarray, pixels: list[np.ndarray], sigmas: list[np.ndarray], height: int, width: int
) -> np.ndarray:
    """Full-resolution sigmaNought grid (column then row interp). For tests; convert()
    interpolates per block so the multi-GB full grid is never materialised."""
    return row_lut(lines, column_lut(lines, pixels, sigmas, width), np.arange(height))


def calibrate_to_db(dn: np.ndarray, sigma_lut: np.ndarray) -> np.ndarray:
    """DN + sigmaNought LUT → sigma0 in dB; DN==0 (nodata) → NaN, so render_db drops it."""
    valid = dn != NODATA_DN
    sigma0 = np.zeros(dn.shape, dtype=np.float32)
    dn_f = dn.astype(np.float32)
    np.divide(dn_f * dn_f, (sigma_lut * sigma_lut).astype(np.float32), out=sigma0, where=valid)
    db = np.full(dn.shape, np.nan, dtype=np.float32)
    np.log10(sigma0, out=db, where=valid & (sigma0 > 0))
    db *= 10.0
    return db


def _read_safe(safe: Path) -> tuple[np.ndarray, bytes]:
    """Read (DN array, calibration XML) from a .SAFE.zip (via /vsizip/, no unzip) or dir."""
    import rasterio

    if safe.is_dir():
        names = [str(p.relative_to(safe)) for p in safe.rglob("*")]
        measurement, calibration = find_members(names)
        with rasterio.open(safe / measurement) as src:
            dn = src.read(1)
        return dn, (safe / calibration).read_bytes()

    with zipfile.ZipFile(safe) as archive:
        names = archive.namelist()
        measurement, calibration = find_members(names)
        calib_bytes = archive.read(calibration)
    with rasterio.open(f"/vsizip/{safe}/{measurement}") as src:
        dn = src.read(1)
    return dn, calib_bytes


def scene_id_for(product_identifier: str, labels_csv: Path) -> str:
    """Map a GRD product identifier → xView3 scene_id via the labels CSV, so the output
    dir name matches what prepare_xview3.py joins on. Falls back to the identifier."""
    stem = Path(product_identifier).name.removesuffix(".SAFE.zip").removesuffix(".SAFE")
    with labels_csv.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if "GRD_product_identifier" not in (reader.fieldnames or []):
            return stem
        for record in reader:
            if Path(record["GRD_product_identifier"]).name.removesuffix(".SAFE") == stem:
                return record["scene_id"]
    raise SystemExit(f"{stem} matched no GRD_product_identifier in {labels_csv}")


# Calibrate one stripe at a time — a full scene as float32 is several GB and OOMs a
# ~12 GB Colab box. Multiple of the 512 tile height so stripes write in order.
BLOCK_ROWS = 2048


def convert(safe: Path, out_dir: Path, scene_id: str, block_rows: int = BLOCK_ROWS) -> Path:
    import rasterio
    from rasterio.windows import Window

    dn, calib_bytes = _read_safe(safe)
    lines, pixels, sigmas = parse_calibration_lut(calib_bytes)
    height, width = dn.shape
    columns = column_lut(lines, pixels, sigmas, width)

    scene_dir = out_dir / scene_id
    scene_dir.mkdir(parents=True, exist_ok=True)
    out_path = scene_dir / "VV_dB.tif"
    with rasterio.open(
        out_path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float32", nodata=float("nan"), compress="deflate", predictor=3,
        tiled=True, blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER",
    ) as dst:
        for r0 in range(0, height, block_rows):
            r1 = min(r0 + block_rows, height)
            lut = row_lut(lines, columns, np.arange(r0, r1))
            dst.write(calibrate_to_db(dn[r0:r1], lut), 1, window=Window(0, r0, width, r1 - r0))
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--safe", type=Path, required=True, help="a GRD .SAFE.zip (or unzipped .SAFE dir)")
    parser.add_argument("--labels", type=Path, required=True, help="GRD label CSV, for the scene_id mapping")
    parser.add_argument("--out", type=Path, required=True, help="output root; writes {out}/{scene_id}/VV_dB.tif")
    args = parser.parse_args()

    scene_id = scene_id_for(args.safe.name, args.labels)
    out_path = convert(args.safe, args.out, scene_id)
    print(f"{args.safe.name} -> {out_path} (scene_id={scene_id})")


if __name__ == "__main__":
    main()
