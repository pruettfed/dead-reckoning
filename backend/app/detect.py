"""YOLOv8 ship detection over stitched SAR chips.

Chips exceed the model's input size, so detection runs on overlapping tiles;
duplicate hits along tile seams are merged with global NMS. All torch work is
synchronous.

This module stays importable without torch: `from ultralytics import YOLO` lives
inside `YoloDetector.__init__`, so the API process can hold `DetectorSpec`,
`iter_tiles` and the merge logic without paying for the model. The API path runs
inference out-of-process via `detect_worker`; `run_detection` here is the
in-process path the CLI tools use.

Weights come from the ml/ fine-tune runbook (`MODEL_PATH`, default
models/sar_ship.pt). Missing weights or ML deps raise `DetectorUnavailable`
so the API can answer 503 instead of crashing.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from app.sar import SarChip, TARGET_M_PER_PX

TILE_SIZE = 800  # HRSID trains at 800x800
TILE_OVERLAP = 160

# Two boxes on one hull do not have to overlap enough for IoU to see it: a hull
# cut by a tile seam is boxed whole in one tile and as a sliver in the next
# (IoU 0.2), and the model splits a single long hull into bow and stern boxes
# that do not overlap at all (IoU 0). Both were producing a second detection
# metres from the first, which fusion then had no vessel left to assign — so it
# read as "unresolved" beside its own match, or as a second dark contact.
#
# So separation is also judged in metres, where the physics lives. Measured over
# every stored detection: 13 have a nearest neighbour ≤ 77 m, the closest
# genuinely distinct pair is 548 m, and the bulk are > 2 km. 150 m sits in that
# gap, at under half the length of the longest hulls afloat.
#
# Vessels themselves do get closer than this — AIS puts 182 pairs under 150 m in
# one skagen_kattegat scene alone, moored and rafted up. The gate is safe not
# because that cannot happen but because the detector does not resolve it: of
# those 182, only 3 were large hulls clear of the land mask, and none produced a
# detection within 500 m. This merges what 10 m/px already blurred into one
# return. Should the detector ever separate two anchored ships at this range,
# this costs the second one — recheck against AIS before widening it.
MIN_SEPARATION_M = 150.0

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


@dataclass(frozen=True)
class DetectorSpec:
    """How to build a detector, without building one.

    The API process passes this around instead of a live `Detector` so that
    torch is never imported into it — see `detect_worker.py`. Small, immutable
    and picklable, so it crosses a process boundary for free.
    """

    model_path: str
    conf_threshold: float


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


def _centre_distance_px(a: PixelDetection, b: PixelDetection) -> float:
    ax, ay = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    bx, by = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return math.hypot(ax - bx, ay - by)


def merge_detections(
    detections: list[PixelDetection],
    iou_threshold: float = 0.5,
    min_separation_m: float = MIN_SEPARATION_M,
) -> list[PixelDetection]:
    """Greedy NMS in chip pixel coords — dedupes hits from overlapping tiles.

    Suppresses on either overlap or centre separation: boxes on one hull can be
    nested, slivered or disjoint, none of which IoU alone catches.
    """
    min_separation_px = min_separation_m / TARGET_M_PER_PX
    kept: list[PixelDetection] = []
    for det in sorted(detections, key=lambda d: d[4], reverse=True):
        if all(
            _iou(det, k) < iou_threshold
            and _centre_distance_px(det, k) >= min_separation_px
            for k in kept
        ):
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


def detect_tiles(pixels: np.ndarray, detector: Detector) -> list[PixelDetection]:
    """Tile the image and detect, in whole-chip pixel coords. Blocking.

    Split out from `run_detection` because this is the only half that needs
    torch: it is what runs in the detection subprocess, while merging and
    geolocation stay in the caller (pure numpy and arithmetic).
    """
    height, width = pixels.shape[:2]
    raw: list[PixelDetection] = []
    for x_off, y_off in iter_tiles(width, height):
        tile = pixels[y_off:y_off + TILE_SIZE, x_off:x_off + TILE_SIZE]
        for x1, y1, x2, y2, conf in detector.detect_tile(tile):
            raw.append((x1 + x_off, y1 + y_off, x2 + x_off, y2 + y_off, conf))
    return raw


def geolocate(chip: SarChip, raw: list[PixelDetection]) -> list[GeoDetection]:
    """NMS-merge chip-space detections and map their centroids to lon/lat."""
    geo: list[GeoDetection] = []
    for x1, y1, x2, y2, conf in merge_detections(raw):
        lon, lat = pixel_to_lonlat((x1 + x2) / 2, (y1 + y2) / 2, chip.bbox, chip.width, chip.height)
        geo.append(GeoDetection(lon=lon, lat=lat, confidence=conf, bucket=bucket_confidence(conf)))
    return geo


def run_detection(chip: SarChip, detector: Detector) -> list[GeoDetection]:
    """Tile the chip, detect, NMS-merge, and map centroids to lon/lat. Blocking.

    In-process, so importing torch here is unavoidable — this is the path the
    CLI tools take (`scripts/analyze.py`, `ml/bench_detector.py`), where a
    resident model is exactly what you want. The API process uses
    `detect_worker.run_detection_isolated` instead.
    """
    return geolocate(chip, detect_tiles(chip.pixels, detector))
