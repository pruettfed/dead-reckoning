"""Fine-tune YOLOv8 on a SAR ship dataset. Run on a GPU (Colab T4: ~1-2 h).

See ml/README.md for the full Colab runbook.
"""

import argparse

from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="ml/hrsid.yaml")
    parser.add_argument("--model", default="yolov8n.pt", help="base checkpoint to fine-tune")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=800, help="HRSID images are 800x800")
    parser.add_argument("--batch", type=int, default=16)
    args = parser.parse_args()

    model = YOLO(args.model)
    results = model.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch)
    print(f"\nbest checkpoint: {results.save_dir}/weights/best.pt")
    print("copy it to backend/models/sar_ship.pt to enable detection")


if __name__ == "__main__":
    main()
