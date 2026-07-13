"""Convert a COCO-annotated SAR ship dataset (HRSID, LS-SSDD, SSDD) to YOLO layout.

Output structure (what ultralytics expects):
    <out>/images/train/*.jpg    <out>/labels/train/*.txt
    <out>/images/val/*.jpg      <out>/labels/val/*.txt

Every label line is `0 cx cy w h` (single class: ship, normalized coords).
"""

import argparse
import json
import shutil
from pathlib import Path


def coco_bbox_to_yolo(
    bbox: tuple[float, float, float, float], img_w: float, img_h: float
) -> tuple[float, float, float, float]:
    """COCO [x_min, y_min, width, height] (pixels) → YOLO (cx, cy, w, h) normalized."""
    x, y, w, h = bbox
    return ((x + w / 2) / img_w, (y + h / 2) / img_h, w / img_w, h / img_h)


def convert_split(coco_json: Path, images_dir: Path, out_root: Path, split: str) -> tuple[int, int]:
    """Copy images and write YOLO labels for one split. Returns (converted, missing)."""
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
        label_path = lbl_out / (Path(img["file_name"]).stem + ".txt")
        label_path.write_text("\n".join(lines_by_image[img_id]) + "\n")
        converted += 1
    return converted, missing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True, help="directory with all source images (e.g. HRSID_JPG/JPEGImages)")
    parser.add_argument("--train-json", type=Path, required=True, help="COCO json for the train split (e.g. annotations/train2017.json)")
    parser.add_argument("--val-json", type=Path, required=True, help="COCO json for the val split (e.g. annotations/test2017.json)")
    parser.add_argument("--out", type=Path, required=True, help="output dataset root (e.g. /content/datasets/hrsid)")
    args = parser.parse_args()

    for split, coco_json in (("train", args.train_json), ("val", args.val_json)):
        converted, missing = convert_split(coco_json, args.images, args.out, split)
        print(f"{split}: {converted} images converted, {missing} missing from {args.images}")
        if missing:
            raise SystemExit(f"aborting: {missing} {split} images referenced in {coco_json} were not found")


if __name__ == "__main__":
    main()
