"""Chip xView3/SARFish dB GeoTIFFs into a YOLO training set at the pipeline's rendering.

Renders each VV_dB.tif through the same dB->uint8 window production uses (see render_db),
so the model trains on pixels identical in kind to inference. Labels are points; boxes
are used where supplied and synthesised from vessel_length_m otherwise — box precision
barely matters since detect.py collapses each prediction to a centroid.

    python ml/prepare_xview3.py --scenes /content/xview3 \
      --labels /content/xview3/labels.csv --out /content/datasets/xview3
"""

import argparse
import csv
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Keep in sync with DB_MIN / DB_MAX in backend/app/sar.py — the production evalscript
# scales this dB window onto 0-255. test_prepare_xview3.py asserts they match.
DB_MIN = -25.0
DB_MAX = 0.0

TILE_SIZE = 800          # matches TILE_SIZE in backend/app/detect.py
M_PER_PX = 10.0          # matches TARGET_M_PER_PX in backend/app/sar.py

# Box synthesis for labels with no supplied extent. A vessel's length in pixels is
# length_m / 10; the box is square because xView3 gives no orientation or beam.
DEFAULT_BOX_PX = 8.0     # ~80 m, near the median labelled vessel
MIN_BOX_PX = 4.0         # below this YOLO has nothing to latch onto

KEEP_CONFIDENCE = ("HIGH", "MEDIUM")


@dataclass(frozen=True)
class Label:
    """One xView3 detection in scene pixel coordinates."""

    row: float
    col: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2
    keep: bool                              # False → chip is unusable, see filter_label


def _float(value: str | None) -> float:
    """xView3 CSVs use empty strings and 'nan' interchangeably for missing."""
    if value is None or value.strip() == "":
        return math.nan
    try:
        return float(value)
    except ValueError:
        return math.nan


def _bool(value: str | None) -> bool | None:
    text = (value or "").strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def synthesise_box(
    row: float, col: float, length_m: float, *, default_px: float = DEFAULT_BOX_PX
) -> tuple[float, float, float, float]:
    """Square box centred on the detection, sized from vessel length where known."""
    side = default_px if math.isnan(length_m) else max(length_m / M_PER_PX, MIN_BOX_PX)
    half = side / 2
    return (col - half, row - half, col + half, row + half)


def parse_label(record: dict[str, str]) -> Label:
    """One CSV row → a Label, using the supplied box when xView3 provides one."""
    row = _float(record.get("detect_scene_row"))
    col = _float(record.get("detect_scene_column"))
    top, left = _float(record.get("top")), _float(record.get("left"))
    bottom, right = _float(record.get("bottom")), _float(record.get("right"))

    if not any(math.isnan(v) for v in (top, left, bottom, right)):
        box = (left, top, right, bottom)
    else:
        box = synthesise_box(row, col, _float(record.get("vessel_length_m")))

    is_vessel = _bool(record.get("is_vessel"))
    confidence = (record.get("confidence") or "").strip().upper()
    return Label(row=row, col=col, box=box, keep=is_vessel is True and confidence in KEEP_CONFIDENCE)


def load_labels(csv_path: Path) -> dict[str, list[Label]]:
    """Group labels by scene_id, dropping rows with no usable pixel location."""
    by_scene: dict[str, list[Label]] = {}
    with csv_path.open(newline="") as handle:
        for record in csv.DictReader(handle):
            label = parse_label(record)
            if math.isnan(label.row) or math.isnan(label.col):
                continue
            by_scene.setdefault(record["scene_id"], []).append(label)
    return by_scene


def render_db(window: np.ndarray, *, db_min: float = DB_MIN, db_max: float = DB_MAX) -> np.ndarray:
    """dB float raster → uint8, matching sar.py's evalscript; NaN (nodata) → 0."""
    scaled = (window - db_min) / (db_max - db_min)
    scaled = np.where(np.isfinite(window), np.clip(scaled, 0.0, 1.0), 0.0)
    return (scaled * 255).astype(np.uint8)


def labels_in_window(
    labels: list[Label], x_off: int, y_off: int, size: int
) -> tuple[list[str], bool]:
    """YOLO lines for labels centred in this window, plus a reject flag.

    Rejects the whole chip if it holds any filtered-out detection (low-confidence, or a
    non-vessel like a platform) — training on that bright, box-less target would teach
    the model to suppress the returns it should find.
    """
    lines: list[str] = []
    for label in labels:
        if not (x_off <= label.col < x_off + size and y_off <= label.row < y_off + size):
            continue
        if not label.keep:
            return [], True
        x1, y1, x2, y2 = label.box
        x1, x2 = np.clip([x1 - x_off, x2 - x_off], 0, size)
        y1, y2 = np.clip([y1 - y_off, y2 - y_off], 0, size)
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        lines.append(
            f"0 {(x1 + x2) / 2 / size:.6f} {(y1 + y2) / 2 / size:.6f} "
            f"{(x2 - x1) / size:.6f} {(y2 - y1) / size:.6f}"
        )
    return lines, False


def iter_windows(width: int, height: int, size: int):
    """Non-overlapping chip grid, last row/column flush against the far edge."""
    for y in _offsets(height, size):
        for x in _offsets(width, size):
            yield x, y


def _offsets(total: int, size: int) -> list[int]:
    if total <= size:
        return [0]
    offsets = list(range(0, total - size + 1, size))
    if offsets[-1] != total - size:
        offsets.append(total - size)
    return offsets


def chip_scene(
    scene_dir: Path,
    labels: list[Label],
    out_root: Path,
    split: str,
    *,
    vv_name: str,
    size: int,
    max_invalid_frac: float,
) -> tuple[list[str], list[str]]:
    """Write every usable chip of one scene. Returns (positive_stems, background_stems)."""
    import rasterio

    img_out = out_root / "images" / split
    lbl_out = out_root / "labels" / split
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    positives: list[str] = []
    backgrounds: list[str] = []
    with rasterio.open(scene_dir / vv_name) as src:
        for x, y in iter_windows(src.width, src.height, size):
            window = src.read(1, window=rasterio.windows.Window(x, y, size, size))
            if window.shape != (size, size):
                continue
            invalid = ~np.isfinite(window)
            if invalid.mean() > max_invalid_frac:
                continue
            lines, rejected = labels_in_window(labels, x, y, size)
            if rejected:
                continue
            stem = f"{scene_dir.name}_{y}_{x}"
            _write_png(img_out / f"{stem}.png", render_db(window))
            (lbl_out / f"{stem}.txt").write_text("\n".join(lines) + "\n" if lines else "")
            (positives if lines else backgrounds).append(stem)
    return positives, backgrounds


def _write_png(path: Path, pixels: np.ndarray) -> None:
    """PNG, not JPEG: production chips reach the detector as raw uint8 arrays, so
    JPEG ringing around bright hulls would be a domain gap we can avoid for free."""
    from PIL import Image

    Image.fromarray(pixels, mode="L").save(path, optimize=True)


def drop_backgrounds(
    backgrounds: list[str], n_positive: int, out_root: Path, split: str, frac: float, seed: int
) -> int:
    """Delete surplus ship-free chips. Returns how many were kept."""
    if frac >= 1.0:
        return len(backgrounds)
    keep = int(n_positive * frac / (1 - frac)) if frac > 0 else 0
    if keep >= len(backgrounds):
        return len(backgrounds)
    doomed = random.Random(seed).sample(backgrounds, len(backgrounds) - keep)
    for stem in doomed:
        (out_root / "images" / split / f"{stem}.png").unlink(missing_ok=True)
        (out_root / "labels" / split / f"{stem}.txt").unlink(missing_ok=True)
    return keep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenes", type=Path, required=True, help="directory of per-scene subdirectories, each named by scene_id")
    parser.add_argument("--labels", type=Path, required=True, help="xView3 label CSV (validation.csv / train.csv)")
    parser.add_argument("--out", type=Path, required=True, help="output dataset root (e.g. /content/datasets/xview3)")
    parser.add_argument("--vv-name", default="VV_dB.tif", help="per-scene VV raster filename")
    parser.add_argument("--chip", type=int, default=TILE_SIZE, help="chip size in pixels")
    parser.add_argument("--val-scenes", type=int, default=3, help="hold out this many scenes for val — split is by scene, never by chip, or near-identical chips leak across the split")
    parser.add_argument("--max-background-frac", type=float, default=0.15, help="cap ship-free chips at this fraction of the train split")
    parser.add_argument("--max-invalid-frac", type=float, default=0.5, help="skip chips with more than this fraction of non-finite pixels")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if not args.scenes.is_dir():
        raise SystemExit(f"--scenes is not a directory: {args.scenes}")
    by_scene = load_labels(args.labels)
    scene_dirs = sorted(d for d in args.scenes.iterdir() if d.is_dir() and (d / args.vv_name).exists())
    if not scene_dirs:
        raise SystemExit(
            f"no scene directories under {args.scenes} contain {args.vv_name}\n"
            f"  subdirectories seen: {[d.name for d in sorted(args.scenes.iterdir()) if d.is_dir()][:5]}"
        )
    labelled = [d for d in scene_dirs if d.name in by_scene]
    print(f"{len(scene_dirs)} scenes on disk, {len(labelled)} with labels in {args.labels}")
    if not labelled:
        raise SystemExit(
            "no scene directory name matched a scene_id in the labels\n"
            f"  scene dirs: {[d.name for d in scene_dirs[:3]]}\n"
            f"  label scene_ids: {sorted(by_scene)[:3]}"
        )

    random.Random(args.seed).shuffle(labelled)
    val = labelled[: args.val_scenes]
    train = labelled[args.val_scenes :]
    if not train:
        raise SystemExit(f"--val-scenes {args.val_scenes} leaves no training scenes ({len(labelled)} available)")

    for split, dirs in (("train", train), ("val", val)):
        positives: list[str] = []
        backgrounds: list[str] = []
        for scene_dir in dirs:
            pos, bg = chip_scene(
                scene_dir,
                by_scene[scene_dir.name],
                args.out,
                split,
                vv_name=args.vv_name,
                size=args.chip,
                max_invalid_frac=args.max_invalid_frac,
            )
            positives += pos
            backgrounds += bg
            print(f"  {scene_dir.name}: {len(pos)} with ships, {len(bg)} background")
        kept = (
            drop_backgrounds(backgrounds, len(positives), args.out, split, args.max_background_frac, args.seed)
            if split == "train"
            else len(backgrounds)
        )
        print(f"{split}: {len(dirs)} scenes → {len(positives)} chips with ships, {kept} background")
        if not positives:
            raise SystemExit(f"no {split} chip contains a ship — check --labels matches these scenes")


if __name__ == "__main__":
    main()
