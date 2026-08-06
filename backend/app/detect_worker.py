"""Run YOLO detection in a subprocess that exits when it is done.

The API process must never import torch. The scheduler analyzes ~152 passes a
month (the sum of `passes_per_month` across the registry), each a few minutes —
roughly 1.7% of the month. Loading the detector at boot, as the scheduler used
to, held torch + ultralytics + the model resident for the other 98%: about 1 GB
of a container that otherwise idles near 200 MB, and on usage-metered hosting
that single fact dominated the bill.

Two cheaper fixes do not work, and it is worth recording why:

  * Lazy-loading on first use only moves the problem. At five analyses a day the
    model is resident within hours of boot and stays there.
  * Dropping the reference and collecting does not release it either. Imported
    modules are never unloaded from `sys.modules`, so torch's code and data
    pages stay mapped for the life of the process no matter what happens to the
    model object.

Only a process that exits gives the memory back. So detection runs in a
short-lived `spawn`ed child that imports torch, does the tiling and inference,
returns plain tuples, and dies. `spawn` rather than `fork`: this is called from
an asyncio process holding threads and open asyncpg sockets, none of which are
safe to fork. The child pays a few seconds of import cost per analysis, which is
nothing against a multi-minute run.

Only the inference half moves. Merging and geolocation stay in the parent — they
are numpy and arithmetic, and keeping them here would mean shipping the chip's
geometry across the boundary for no reason.
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
    """Entry point for the detection subprocess. Must stay module-level and
    picklable by name — `spawn` re-imports this module in the child to find it.

    This is the only function in the codebase's request path that causes torch
    to be imported, and it only ever runs in a process that is about to exit.
    """
    detector = load_detector(model_path, conf_threshold)
    return detect_tiles(pixels, detector)


async def run_detection_isolated(chip: SarChip, spec: DetectorSpec) -> list[GeoDetection]:
    """Detect over `chip` in a subprocess, then merge and geolocate here.

    The pool is created and shut down around a single task rather than kept
    alive: one task per process is the entire point, and an explicitly closed
    pool is easier to reason about than `max_tasks_per_child`.
    """
    loop = asyncio.get_running_loop()
    # uint8, single band (see SarChip) — tens of MB for a typical ROI, so the
    # pickle across the boundary costs well under a second against an analysis
    # measured in minutes. shared_memory is the fallback if an ROI ever grows
    # enough for that to stop being true.
    pixels = np.ascontiguousarray(chip.pixels)
    try:
        with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as pool:
            raw = await loop.run_in_executor(
                pool, _detect_in_child, pixels, spec.model_path, spec.conf_threshold
            )
    except BrokenProcessPool as exc:
        # The child died without raising — almost always the OOM killer on a
        # small container, since inference is the memory peak of the whole
        # process. Say so: a bare BrokenProcessPool in `sar_scenes.error` tells
        # an operator nothing, and the pixels have already been paid for.
        raise DetectionSubprocessDied(
            f"detection subprocess died on a {pixels.shape[1]}x{pixels.shape[0]} chip "
            "without reporting an error — most likely killed for memory"
        ) from exc
    logger.debug("detection subprocess returned %d raw boxes", len(raw))
    return geolocate(chip, raw)


__all__ = ["DetectionSubprocessDied", "DetectorUnavailable", "run_detection_isolated"]
