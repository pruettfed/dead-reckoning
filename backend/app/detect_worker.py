"""Run YOLO detection in a subprocess that exits when it is done.

The API process must never import torch: modules never unload, and lazy
loading only defers residency to the first analysis, so a resident model stays
resident for the ~98% of the month nothing is running. Only a process exit
frees it. `spawn`, not `fork` — this is called from an asyncio process holding
threads and open asyncpg sockets.

Merging and geolocation stay in the parent; only inference needs torch.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context

import numpy as np

from app.detect import (
    DetectorSpec,
    DetectorUnavailable,
    GeoDetection,
    PixelDetection,
    detect_tiles,
    geolocate,
    load_detector,
)
from app.sar import SarChip

logger = logging.getLogger(__name__)


class DetectionSubprocessDied(RuntimeError):
    """The detection child exited without returning a result."""


def _detect_in_child(
    pixels: np.ndarray, model_path: str, conf_threshold: float
) -> list[PixelDetection]:
    """Entry point for the subprocess — must stay module-level for `spawn` to pickle it."""
    detector = load_detector(model_path, conf_threshold)
    return detect_tiles(pixels, detector)


async def run_detection_isolated(chip: SarChip, spec: DetectorSpec) -> list[GeoDetection]:
    """Detect over `chip` in a subprocess, then merge and geolocate here.

    The pool is created and torn down around a single task — one process per
    analysis is the point.
    """
    loop = asyncio.get_running_loop()
    # uint8, single band — a cheap pickle against a multi-minute analysis.
    pixels = np.ascontiguousarray(chip.pixels)
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
            raw = await loop.run_in_executor(
                pool, _detect_in_child, pixels, spec.model_path, spec.conf_threshold
            )
    except BrokenProcessPool as exc:
        # Usually the OOM killer; say so rather than leaving a bare exception.
        raise DetectionSubprocessDied(
            f"detection subprocess died on a {pixels.shape[1]}x{pixels.shape[0]} chip "
            "without reporting an error — most likely killed for memory"
        ) from exc
    logger.debug("detection subprocess returned %d raw boxes", len(raw))
    return geolocate(chip, raw)


__all__ = ["DetectionSubprocessDied", "DetectorUnavailable", "run_detection_isolated"]
