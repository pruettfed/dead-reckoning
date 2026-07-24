"""YOLOv8 ship detection over stitched SAR chips.

Chips exceed the model's input size, so detection runs on overlapping tiles;
duplicate hits along tile seams are merged with global NMS. All torch work is
synchronous — the pipeline runs `run_detection` in a thread.

Weights come from the ml/ fine-tune runbook (`MODEL_PATH`, default
models/sar_ship.pt). Missing weights or ML deps raise `DetectorUnavailable`
so the API can answer 503 instead of crashing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.sar import SarChip

TILE_SIZE = 800  # HRSID trains at 800x800
TILE_OVERLAP = 160

CONF_HIGH = 0.6
CONF_MEDIUM = 0.25

PixelDetection = tuple[float, float, float, float, float]  # x1, y1, x2, y2, conf


@dataclass(frozen=True)
class GeoDetection:
    lon: float
    lat: float
    confidence: float
    bucket: str


class DetectorUnavailable(RuntimeError):
    """Checkpoint or ML dependencies missing — analysis cannot run."""


class Detector(Protocol):
    def detect_tile(self, tile: np.ndarray) -> list[PixelDetection]: ...


def iter_tiles(
    width: int, height: int, tile: int = TILE_SIZE, overlap: int = TILE_OVERLAP
) -> list[tuple[int, int]]:
    """Top-left offsets of overlapping tiles that exactly cover width x height."""

    def offsets(total: int) -> list[int]:
        if total <= tile:
            return [0]
        stride = tile - overlap
        offs = list(range(0, total - tile, stride))
        offs.append(total - tile)
        return offs

    return [(x, y) for y in offsets(height) for x in offsets(width)]


def pixel_to_lonlat(
    col: float, row: float, bbox: tuple[float, float, float, float], width: int, height: int
) -> tuple[float, float]:
    """Chip pixel → EPSG:4326; row 0 is the chip's north edge."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lon = min_lon + (col / width) * (max_lon - min_lon)
    lat = max_lat - (row / height) * (max_lat - min_lat)
    return lon, lat


def _iou(a: PixelDetection, b: PixelDetection) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def merge_detections(
    detections: list[PixelDetection], iou_threshold: float = 0.5
) -> list[PixelDetection]:
    """Greedy NMS in chip pixel coords — dedupes hits from overlapping tiles."""
    kept: list[PixelDetection] = []
    for det in sorted(detections, key=lambda d: d[4], reverse=True):
        if all(_iou(det, k) < iou_threshold for k in kept):
            kept.append(det)
    return kept


def bucket_confidence(conf: float) -> str:
    if conf >= CONF_HIGH:
        return "high"
    if conf >= CONF_MEDIUM:
        return "medium"
    return "low"


class YoloDetector:
    def __init__(self, model_path: str, conf_threshold: float):
        from ultralytics import YOLO

        self._model = YOLO(model_path)
        self._conf = conf_threshold

    def detect_tile(self, tile: np.ndarray) -> list[PixelDetection]:
        rgb = np.stack([tile] * 3, axis=-1)
        result = self._model.predict(rgb, conf=self._conf, imgsz=TILE_SIZE, verbose=False)[0]
        return [
            (x1, y1, x2, y2, conf)
            for (x1, y1, x2, y2), conf in zip(
                result.boxes.xyxy.tolist(), result.boxes.conf.tolist()
            )
        ]


def load_detector(model_path: str, conf_threshold: float) -> Detector:
    if not os.path.exists(model_path):
        raise DetectorUnavailable(
            f"model checkpoint not found at {model_path!r} — train one via ml/README.md"
        )
    try:
        return YoloDetector(model_path, conf_threshold)
    except ImportError as exc:
        raise DetectorUnavailable(
            f"ML dependencies missing ({exc}) — pip install -r requirements-ml.txt"
        )


def run_detection(chip: SarChip, detector: Detector) -> list[GeoDetection]:
    """Tile the chip, detect, NMS-merge, and map centroids to lon/lat. Blocking."""
    raw: list[PixelDetection] = []
    for x_off, y_off in iter_tiles(chip.width, chip.height):
        tile = chip.pixels[y_off:y_off + TILE_SIZE, x_off:x_off + TILE_SIZE]
        for x1, y1, x2, y2, conf in detector.detect_tile(tile):
            raw.append((x1 + x_off, y1 + y_off, x2 + x_off, y2 + y_off, conf))

    geo: list[GeoDetection] = []
    for x1, y1, x2, y2, conf in merge_detections(raw):
        lon, lat = pixel_to_lonlat((x1 + x2) / 2, (y1 + y2) / 2, chip.bbox, chip.width, chip.height)
        geo.append(GeoDetection(lon=lon, lat=lat, confidence=conf, bucket=bucket_confidence(conf)))
    return geo
