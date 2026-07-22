"""Sentinel-1 SAR access via the Copernicus Data Space Ecosystem (CDSE).

Pipeline (per ROI, per pass):
  1. catalog search  → Sentinel-1 IW GRDH scenes covering the ROI (free, no auth)
  2. pixel fetch     → Sentinel Hub Process API returns calibrated, orthorectified,
                       dB-scaled UINT8 chips of the ROI for one specific pass
  3. detection       → app.detect (YOLOv8 fine-tuned on a SAR ship dataset)

Credit budget: 30,000 Processing Units (PU) / month.
  - Catalog search (`search_scenes`) hits the free CDSE OData catalogue (0 PU).
  - Pixel fetch (`fetch_scene_pixels`) is the only PU-spending step: an ROI is
    fetched as a grid of ≤2400 px tiles at 10 m/px. Cost scales with ROI area
    (VV band + orthorectification, 8-bit output) at roughly grid_px / 393k PU
    — ~55 PU for Singapore Strait up to ~720 PU for the larger northern boxes;
    see `estimate_pu`, logged before every fetch. A GRD/COG download backend
    (0 PU) can replace it later behind the same signature. Never drive
    discovery through the Copernicus Browser — its rendering also consumes PU.

Auth: pixel fetch uses CDSE OAuth2 client credentials (`CDSE_CLIENT_ID` /
`CDSE_CLIENT_SECRET`); catalog search needs none.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import string
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
from PIL import Image

from app.config import get_settings

log = logging.getLogger(__name__)

CDSE_ODATA_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
SH_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# IW Interferometric Wide-swath, GRD High-res, dual-pol VV+VH — the detected,
# analysis-ready product for ship detection over coastal water.
DEFAULT_PRODUCT = "IW_GRDH_1SDV"

TARGET_M_PER_PX = 10.0
MAX_TILE_PX = 2400  # Process API caps output at 2500 px per side

# dB window scaled onto 0–255: sea surface reads dark, ship hulls saturate bright.
DB_MIN = -25.0
DB_MAX = 0.0

# Sentinel Hub speckle filter applied to linear σ⁰ before the evalscript sees it
# (e.g. {"type": "LEE", "windowSizeX": 3, "windowSizeY": 3}). None = off, the
# production default; the bench sweeps candidates through the same declared seam.
SPECKLE_FILTER: dict | None = None

EVALSCRIPT = string.Template(
    """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["VV", "dataMask"] }],
    output: { bands: 1, sampleType: "UINT8" },
  };
}
function evaluatePixel(sample) {
  if (sample.dataMask === 0) return [0];
  var db = 10 * Math.log10(Math.max(sample.VV, 1e-10));
  var scaled = (db - $db_min) / ($db_max - $db_min);
  return [255 * Math.max(0, Math.min(1, scaled))];
}
"""
).substitute(db_min=DB_MIN, db_max=DB_MAX)


class SarCredentialsMissing(RuntimeError):
    """CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not configured."""


@dataclass(frozen=True)
class SarScene:
    """One Sentinel-1 acquisition product covering (part of) an ROI."""

    id: str
    name: str
    sensed_at: datetime          # acquisition start (UTC) — the correlation timestamp
    footprint_wkt: str | None    # actual imaged polygon; clip dark/not-dark to this
    platform: str                # "S1A" | "S1C" | "S1D"
    is_cog: bool                 # cloud-optimized GeoTIFF variant (windowed reads)


@dataclass(frozen=True)
class FetchTile:
    """One Process API request within the shared ROI pixel grid."""

    bbox: tuple[float, float, float, float]
    width: int
    height: int
    x_off: int
    y_off: int


@dataclass(frozen=True)
class FetchGrid:
    width: int
    height: int
    tiles: tuple[FetchTile, ...]


@dataclass(frozen=True)
class SarChip:
    """Stitched single-band uint8 image of an ROI; row 0 is the north edge."""

    pixels: np.ndarray
    bbox: tuple[float, float, float, float]
    width: int
    height: int


OVERVIEW_MAX_PX = 2048


def chip_overview_png(chip: SarChip, max_px: int = OVERVIEW_MAX_PX) -> bytes:
    """Downsampled PNG of a chip, for map display over its `bbox` extent.

    Max-pools rather than averaging. A ship is a handful of bright pixels on dark
    water, and at these reduction factors averaging erases exactly the returns the
    detections are drawn on. Pads (never crops) to a whole number of blocks so the
    output still spans the full bbox and the overlay stays registered.
    """
    pixels = chip.pixels
    factor = max(1, math.ceil(max(chip.width, chip.height) / max_px))
    if factor > 1:
        pad_h = -chip.height % factor
        pad_w = -chip.width % factor
        if pad_h or pad_w:
            pixels = np.pad(pixels, ((0, pad_h), (0, pad_w)), mode="edge")
        h, w = pixels.shape
        pixels = pixels.reshape(h // factor, factor, w // factor, factor).max(axis=(1, 3))
    buf = io.BytesIO()
    Image.fromarray(pixels, mode="L").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _bbox_to_polygon_wkt(bbox: tuple[float, float, float, float]) -> str:
    """(min_lon, min_lat, max_lon, max_lat) → closed POLYGON WKT (lon lat order)."""
    min_lon, min_lat, max_lon, max_lat = bbox
    ring = [
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
        (min_lon, min_lat),
    ]
    coords = ",".join(f"{lon} {lat}" for lon, lat in ring)
    return f"POLYGON(({coords}))"


def _parse_scene(item: dict) -> SarScene:
    name: str = item["Name"]
    start = item["ContentDate"]["Start"].replace("Z", "+00:00")
    footprint = item.get("Footprint")
    return SarScene(
        id=item["Id"],
        name=name,
        sensed_at=datetime.fromisoformat(start).astimezone(timezone.utc),
        footprint_wkt=footprint if isinstance(footprint, str) else None,
        platform=name[:3],
        is_cog=name.endswith("_COG.SAFE"),
    )


async def search_scenes(
    bbox: tuple[float, float, float, float],
    start: datetime,
    end: datetime,
    *,
    product: str = DEFAULT_PRODUCT,
    client: httpx.AsyncClient | None = None,
) -> list[SarScene]:
    """List Sentinel-1 scenes intersecting `bbox` between `start` and `end`.

    Hits the free, unauthenticated CDSE OData catalogue (0 PU). Returns scenes
    ordered by acquisition time. Each scene's `sensed_at` is the timestamp to
    correlate AIS against; `footprint_wkt` is the polygon to clip results to.
    """
    polygon = _bbox_to_polygon_wkt(bbox)
    filt = (
        "Collection/Name eq 'SENTINEL-1' "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{polygon}') "
        f"and ContentDate/Start gt {start.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S.000Z} "
        f"and ContentDate/Start lt {end.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%S.000Z} "
        f"and contains(Name,'{product}')"
    )
    params = {
        "$filter": filt,
        "$orderby": "ContentDate/Start asc",
        "$select": "Id,Name,ContentDate,Footprint",
        "$top": "200",
    }

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.get(CDSE_ODATA_URL, params=params)
        resp.raise_for_status()
        return [_parse_scene(item) for item in resp.json().get("value", [])]
    finally:
        if owns_client:
            await client.aclose()


def _split_spans(total: int, max_px: int) -> list[tuple[int, int]]:
    """Split `total` pixels into contiguous near-equal spans of ≤ `max_px`."""
    count = math.ceil(total / max_px)
    base, extra = divmod(total, count)
    spans: list[tuple[int, int]] = []
    start = 0
    for i in range(count):
        size = base + (1 if i < extra else 0)
        spans.append((start, start + size))
        start += size
    return spans


def plan_fetch_grid(
    bbox: tuple[float, float, float, float],
    m_per_px: float = TARGET_M_PER_PX,
    max_px: int = MAX_TILE_PX,
) -> FetchGrid:
    """Lay one pixel grid over `bbox` and split it into Process-API-sized tiles.

    All tiles share the grid, so stitching them back preserves a single linear
    bbox↔pixel transform (row 0 = north edge).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = math.radians((min_lat + max_lat) / 2)
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = m_per_deg_lat * math.cos(mid_lat)

    width = max(1, round((max_lon - min_lon) * m_per_deg_lon / m_per_px))
    height = max(1, round((max_lat - min_lat) * m_per_deg_lat / m_per_px))
    lon_per_px = (max_lon - min_lon) / width
    lat_per_px = (max_lat - min_lat) / height

    tiles = []
    for y0, y1 in _split_spans(height, max_px):
        for x0, x1 in _split_spans(width, max_px):
            tiles.append(
                FetchTile(
                    bbox=(
                        min_lon + x0 * lon_per_px,
                        max_lat - y1 * lat_per_px,
                        min_lon + x1 * lon_per_px,
                        max_lat - y0 * lat_per_px,
                    ),
                    width=x1 - x0,
                    height=y1 - y0,
                    x_off=x0,
                    y_off=y0,
                )
            )
    return FetchGrid(width=width, height=height, tiles=tuple(tiles))


# Process API Processing-Unit cost model, per
# documentation.dataspace.copernicus.eu/APIs/SentinelHub/Overview/ProcessingUnit.html:
PU_PIXELS_PER_UNIT = 512 * 512
PU_BANDS_FACTOR = 1 / 3   # VV only; dataMask is excluded from the band count
PU_ORTHO_FACTOR = 2.0     # processing.orthorectify = True

PU_MONTHLY_BUDGET = 30_000

# The Process API mosaics every acquisition in this window around the chosen
# scene, so the imagery actually returned is the *union* of the slices in it —
# not one slice's footprint. `pipeline.footprint_coverage` reuses these to
# predict coverage before spending PU; they must stay in sync.
PROCESS_WINDOW_BACK = timedelta(minutes=1)
PROCESS_WINDOW_FWD = timedelta(minutes=10)


def estimate_pu(grid: FetchGrid) -> float:
    return (
        grid.width * grid.height / PU_PIXELS_PER_UNIT
        * PU_BANDS_FACTOR
        * PU_ORTHO_FACTOR
    )


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_process_request(
    scene: SarScene,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    *,
    evalscript: str = EVALSCRIPT,
    speckle_filter: dict | None = SPECKLE_FILTER,
) -> dict:
    """Process API body for one tile of `scene` clipped to `bbox`.

    The timeRange brackets this scene's acquisition only — the correlation is
    single-snapshot, so passes are never mosaicked.

    `evalscript` and `speckle_filter` default to the production pair (`EVALSCRIPT`,
    `SPECKLE_FILTER`) so the bench can reuse this tiling seam without duplicating it.
    `speckle_filter` runs on linear σ⁰ before the evalscript and is only emitted
    when non-None.
    """
    processing: dict = {
        "orthorectify": True,
        "backCoeff": "SIGMA0_ELLIPSOID",
    }
    if speckle_filter is not None:
        processing["speckleFilter"] = speckle_filter
    return {
        "input": {
            "bounds": {
                "bbox": list(bbox),
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": "sentinel-1-grd",
                    "dataFilter": {
                        "timeRange": {
                            "from": _iso_z(scene.sensed_at - PROCESS_WINDOW_BACK),
                            "to": _iso_z(scene.sensed_at + PROCESS_WINDOW_FWD),
                        },
                        "acquisitionMode": "IW",
                        "polarization": "DV",
                        "resolution": "HIGH",
                        "mosaickingOrder": "mostRecent",
                    },
                    "processing": processing,
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [
                {"identifier": "default", "format": {"type": "image/png"}}
            ],
        },
        "evalscript": evalscript,
    }


_token_cache: dict = {"token": None, "expires_at": 0.0}


async def _get_token(client: httpx.AsyncClient) -> str:
    settings = get_settings()
    if not settings.cdse_client_id or not settings.cdse_client_secret:
        raise SarCredentialsMissing("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET not configured")
    if _token_cache["token"] and time.monotonic() < _token_cache["expires_at"]:
        return _token_cache["token"]
    resp = await client.post(
        SH_TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.cdse_client_id,
            "client_secret": settings.cdse_client_secret,
        },
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = time.monotonic() + payload.get("expires_in", 600) - 60
    return _token_cache["token"]


async def fetch_scene_pixels(
    scene: SarScene,
    bbox: tuple[float, float, float, float],
    *,
    client: httpx.AsyncClient | None = None,
    evalscript: str = EVALSCRIPT,
    speckle_filter: dict | None = SPECKLE_FILTER,
) -> SarChip:
    """Fetch `bbox` from `scene` as one stitched uint8 chip. Spends PU (see estimate_pu).

    `evalscript` / `speckle_filter` pass through to `build_process_request` per tile,
    defaulting to the production pair; the bench overrides them at 0 change to prod.
    """
    grid = plan_fetch_grid(bbox)
    log.info(
        "fetching %s: %dx%d px in %d tiles (~%.0f PU)",
        scene.name, grid.width, grid.height, len(grid.tiles), estimate_pu(grid),
    )
    chip = np.zeros((grid.height, grid.width), dtype=np.uint8)
    semaphore = asyncio.Semaphore(4)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=120.0)
    try:
        token = await _get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        async def fetch_tile(tile: FetchTile) -> None:
            body = build_process_request(
                scene, tile.bbox, tile.width, tile.height,
                evalscript=evalscript, speckle_filter=speckle_filter,
            )
            async with semaphore:
                resp = await client.post(SH_PROCESS_URL, json=body, headers=headers)
                resp.raise_for_status()
            image = Image.open(io.BytesIO(resp.content)).convert("L")
            chip[tile.y_off:tile.y_off + tile.height, tile.x_off:tile.x_off + tile.width] = np.asarray(image)

        await asyncio.gather(*(fetch_tile(tile) for tile in grid.tiles))
        return SarChip(pixels=chip, bbox=bbox, width=grid.width, height=grid.height)
    finally:
        if owns_client:
            await client.aclose()
