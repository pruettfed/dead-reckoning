"""Report mAP for a trained checkpoint on the validation split."""

import argparse

from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("weights", help="path to best.pt")
    parser.add_argument("--data", default="ml/hrsid.yaml")
    parser.add_argument("--imgsz", type=int, default=800)
    args = parser.parse_args()

    metrics = YOLO(args.weights).val(data=args.data, imgsz=args.imgsz)
    print(f"mAP50: {metrics.box.map50:.4f}  mAP50-95: {metrics.box.map:.4f}")


if __name__ == "__main__":
    main()
