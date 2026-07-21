"""Convert a SAR ship dataset (HRSID, LS-SSDD, SSDD) to YOLO layout.

Two source formats:
    --format coco   HRSID / SSDD — COCO json per split
    --format voc    LS-SSDD-v1.0 — PASCAL VOC XML per image + ImageSets manifests

Output structure (what ultralytics expects):
    <out>/images/train/*.jpg    <out>/labels/train/*.txt
    <out>/images/val/*.jpg      <out>/labels/val/*.txt

Every label line is `0 cx cy w h` (single class: ship, normalized coords).
"""

import argparse
import json
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


def coco_bbox_to_yolo(
    bbox: tuple[float, float, float, float], img_w: float, img_h: float
) -> tuple[float, float, float, float]:
    """COCO [x_min, y_min, width, height] (pixels) → YOLO (cx, cy, w, h) normalized."""
    x, y, w, h = bbox
    return ((x + w / 2) / img_w, (y + h / 2) / img_h, w / img_w, h / img_h)


def voc_box_to_yolo(
    xmin: float, ymin: float, xmax: float, ymax: float, img_w: float, img_h: float
) -> tuple[float, float, float, float]:
    """VOC corner box (pixels, inclusive) → YOLO (cx, cy, w, h) normalized."""
    return (
        (xmin + xmax) / 2 / img_w,
        (ymin + ymax) / 2 / img_h,
        (xmax - xmin) / img_w,
        (ymax - ymin) / img_h,
    )


def write_label(path: Path, lines: list[str]) -> None:
    """Write one YOLO label file. Ship-free images get a genuinely empty file.

    Ultralytics reads an empty label as a background image. Writing a lone
    newline instead also works but produces thousands of not-quite-empty files
    on LS-SSDD, where most sub-images hold no ship.
    """
    path.write_text("\n".join(lines) + "\n" if lines else "")


def convert_split(coco_json: Path, images_dir: Path, out_root: Path, split: str) -> tuple[int, int]:
    """Copy images and write YOLO labels for one COCO split. Returns (converted, missing)."""
    data = json.loads(coco_json.read_text())
    images = {img["id"]: img for img in data["images"]}
    lines_by_image: dict[int, list[str]] = {img_id: [] for img_id in images}
    for ann in data["annotations"]:
        img = images[ann["image_id"]]
        cx, cy, w, h = coco_bbox_to_yolo(ann["bbox"], img["width"], img["height"])
        lines_by_image[ann["image_id"]].append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

    img_out = out_root / "images" / split
    lbl_out = out_root / "labels" / split
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    converted = missing = 0
    for img_id, img in images.items():
        src = images_dir / img["file_name"]
        if not src.exists():
            missing += 1
            continue
        shutil.copy2(src, img_out / img["file_name"])
        write_label(lbl_out / (Path(img["file_name"]).stem + ".txt"), lines_by_image[img_id])
        converted += 1
    return converted, missing


def parse_voc(xml_path: Path) -> tuple[int, int, list[str]]:
    """Read one VOC annotation → (img_w, img_h, YOLO label lines)."""
    root = ET.parse(xml_path).getroot()
    size = root.find("size")
    img_w = int(float(size.findtext("width")))
    img_h = int(float(size.findtext("height")))

    lines = []
    for obj in root.findall("object"):
        box = obj.find("bndbox")
        cx, cy, w, h = voc_box_to_yolo(
            float(box.findtext("xmin")),
            float(box.findtext("ymin")),
            float(box.findtext("xmax")),
            float(box.findtext("ymax")),
            img_w,
            img_h,
        )
        lines.append(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return img_w, img_h, lines


def index_images(images_dir: Path) -> dict[str, Path]:
    """Map image stem → path, searching recursively.

    LS-SSDD splits its 9,000 sub-images across JPEGImages_sub_train/ and
    JPEGImages_sub_test/ inside JPEGImages_sub/, so point --images at the parent
    and let the walk find them either way.
    """
    if not images_dir.is_dir():
        raise SystemExit(f"--images is not a directory: {images_dir}")
    index = {
        path.stem: path
        for path in sorted(images_dir.rglob("*"))
        if path.suffix.lower() in IMAGE_SUFFIXES
    }
    if not index:
        raise SystemExit(
            f"no images found under {images_dir} (looked recursively for "
            f"{', '.join(IMAGE_SUFFIXES)})"
        )
    return index


def read_ids(manifest: Path) -> list[str]:
    """One image per line (ImageSets/train.txt, ImageSets/test.txt) → bare stems.

    VOC manifests are usually bare stems, but variants ship a file extension or a
    directory prefix. Normalising through Path().stem accepts all three rather
    than silently matching nothing.
    """
    return [
        Path(line.strip()).stem
        for line in manifest.read_text().splitlines()
        if line.strip()
    ]


def convert_voc_split(
    ids: list[str],
    index: dict[str, Path],
    annotations_dir: Path,
    out_root: Path,
    split: str,
    *,
    max_background_frac: float | None = None,
    seed: int = 0,
) -> tuple[int, list[str], int]:
    """Copy images and write YOLO labels for one VOC split.

    Returns (converted, missing_ids, backgrounds_kept).

    `max_background_frac` caps ship-free images as a fraction of the written
    split. LS-SSDD is ~9,000 sub-images against ~6,000 ship instances, so the
    large majority are empty water; training on that ratio makes the objective
    mostly background suppression and biases the model toward low recall, which
    is the failure this retrain exists to fix. Ultralytics recommends ~0-10%
    backgrounds. Positives are always kept in full.
    """
    img_out = out_root / "images" / split
    lbl_out = out_root / "labels" / split
    img_out.mkdir(parents=True, exist_ok=True)
    lbl_out.mkdir(parents=True, exist_ok=True)

    positives: list[tuple[str, list[str]]] = []
    backgrounds: list[tuple[str, list[str]]] = []
    missing: list[str] = []
    for image_id in ids:
        xml_path = annotations_dir / f"{image_id}.xml"
        if image_id not in index or not xml_path.exists():
            missing.append(image_id)
            continue
        _, _, lines = parse_voc(xml_path)
        (positives if lines else backgrounds).append((image_id, lines))

    if max_background_frac is not None:
        # n_bg / (n_pos + n_bg) <= frac  →  n_bg <= frac/(1-frac) * n_pos
        keep = (
            len(backgrounds)
            if max_background_frac >= 1.0
            else int(len(positives) * max_background_frac / (1 - max_background_frac))
        )
        if keep < len(backgrounds):
            backgrounds = random.Random(seed).sample(backgrounds, keep)

    for image_id, lines in positives + backgrounds:
        src = index[image_id]
        shutil.copy2(src, img_out / src.name)
        write_label(lbl_out / f"{image_id}.txt", lines)
    return len(positives) + len(backgrounds), missing, len(backgrounds)


def describe_mismatch(missing: list[str], index: dict[str, Path], annotations_dir: Path) -> str:
    """Explain *why* manifest entries didn't resolve — the useful half of the error."""
    sample = missing[:3]
    no_image = [i for i in sample if i not in index]
    no_xml = [i for i in sample if not (annotations_dir / f"{i}.xml").exists()]
    lines = [f"  first unmatched ids: {sample}"]
    if no_image:
        lines.append(f"  no image found for: {no_image}")
        lines.append(f"  example indexed stems: {sorted(index)[:3]}")
    if no_xml:
        lines.append(f"  no XML found for:   {no_xml} (looked in {annotations_dir})")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("coco", "voc"), default="coco")
    parser.add_argument("--images", type=Path, required=True, help="source images dir (searched recursively for --format voc)")
    parser.add_argument("--out", type=Path, required=True, help="output dataset root (e.g. /content/datasets/lsssdd)")
    parser.add_argument("--train-json", type=Path, help="[coco] COCO json for train (e.g. annotations/train2017.json)")
    parser.add_argument("--val-json", type=Path, help="[coco] COCO json for val (e.g. annotations/test2017.json)")
    parser.add_argument("--annotations", type=Path, help="[voc] dir of per-image XML (e.g. Annotations_sub)")
    parser.add_argument("--train-ids", type=Path, help="[voc] manifest of train stems (e.g. ImageSets/train.txt)")
    parser.add_argument("--val-ids", type=Path, help="[voc] manifest of val stems (e.g. ImageSets/test.txt)")
    parser.add_argument(
        "--max-background-frac",
        type=float,
        default=None,
        help="[voc] cap ship-free images at this fraction of the train split (e.g. 0.15). Train only; val is left intact so numbers stay comparable to published ones.",
    )
    parser.add_argument("--seed", type=int, default=0, help="[voc] seed for the background subsample")
    args = parser.parse_args()

    if args.format == "coco":
        if not (args.train_json and args.val_json):
            raise SystemExit("--format coco requires --train-json and --val-json")
        for split, coco_json in (("train", args.train_json), ("val", args.val_json)):
            converted, missing = convert_split(coco_json, args.images, args.out, split)
            print(f"{split}: {converted} images converted, {missing} missing from {args.images}")
            if missing:
                raise SystemExit(f"aborting: {missing} {split} images referenced in {coco_json} were not found")
        return

    if not (args.annotations and args.train_ids and args.val_ids):
        raise SystemExit("--format voc requires --annotations, --train-ids and --val-ids")
    index = index_images(args.images)
    print(f"indexed {len(index)} images under {args.images}")
    for split, manifest in (("train", args.train_ids), ("val", args.val_ids)):
        ids = read_ids(manifest)
        converted, missing, backgrounds = convert_voc_split(
            ids,
            index,
            args.annotations,
            args.out,
            split,
            max_background_frac=args.max_background_frac if split == "train" else None,
            seed=args.seed,
        )
        print(
            f"{split}: {converted} images converted "
            f"({converted - backgrounds} with ships, {backgrounds} background), "
            f"{len(missing)} missing"
        )
        if missing:
            raise SystemExit(
                f"aborting: {len(missing)}/{len(ids)} {split} images listed in {manifest} "
                f"had no image or XML\n"
                + describe_mismatch(missing, index, args.annotations)
            )
        if converted - backgrounds == 0:
            raise SystemExit(
                f"aborting: every {split} annotation parsed to zero ships. The XML layout "
                f"is probably not <object><bndbox><xmin>… — inspect one:\n"
                f"  head -40 {args.annotations}/{ids[0]}.xml"
            )


if __name__ == "__main__":
    main()
