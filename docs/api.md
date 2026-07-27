# API Reference

Base URL (local): `http://localhost:8000`
Swagger: `http://localhost:8000/docs`
Base URL via Vite proxy: `http://localhost:5173` (proxies `/api` → backend)

All responses are JSON except `GET /api/scenes/{id}/overview.png`, which returns
`image/png`. Timestamps are ISO-8601 UTC.

---

## `GET /api/health`

Liveness check plus per-source health.

**Response**

```json
{
  "status": "ok",
  "sources": {
    "ais": {
      "state": "connected",
      "last_message_at": "2026-07-11T20:51:07.412Z",
      "lag_seconds": 1.2,
      "connected_since": "2026-07-11T20:49:50.679Z",
      "reconnect_count": 0,
      "error_count": 0,
      "last_error": null
    },
    "sar_sentinel1": { "state": "disconnected", "...": "..." }
  }
}
```

`state` is `connected`, `disconnected`, `error`, or `stale` (connected but no
message for `SOURCE_STALE_AFTER_SECONDS`). `sar_sentinel1` connects only while
an analysis is fetching imagery.

---

## `GET /api/rois`

The predefined Regions of Interest. Every AIS endpoint takes `?roi=<name>`;
ingestion always covers all of them simultaneously (switching ROI is a view filter).

| Field      | Type   | Description          |
|------------|--------|----------------------|
| `name`     | string | Machine key          |
| `label`    | string | Human-readable label |
| `ais_bbox` | [min_lon, min_lat, max_lon, max_lat] | Area subscribed on AISStream; also the area AIS endpoints filter to |
| `sar_bbox` | [min_lon, min_lat, max_lon, max_lat] | Area imaged and clipped to. Always inside `ais_bbox` |
| `mode`     | `"fused"` \| `"survey"` | Whether detections here can be called dark |

```json
[
  { "name": "singapore_strait", "label": "Singapore Strait",
    "ais_bbox": [103.55, 1.03, 104.10, 1.35], "sar_bbox": [103.55, 1.03, 104.10, 1.35], "mode": "fused" },
  { "name": "gulf_of_finland",  "label": "Gulf of Finland (shadow-fleet corridor)",
    "ais_bbox": [24.50, 59.20, 28.60, 60.30], "sar_bbox": [24.60, 59.55, 27.40, 60.05], "mode": "fused" },
  { "name": "hormuz_strait",    "label": "Strait of Hormuz (TSS)",
    "ais_bbox": [55.90, 26.15, 56.75, 26.85], "sar_bbox": [56.15, 26.35, 56.65, 26.70], "mode": "survey" }
]
```

> **Why two boxes.** AISStream is terrestrial, so coverage follows coastlines and
> the subscription box has to stay wide and coastal — it is free. The pixel fetch
> is the only thing that costs PU, so its box stays small and on water. They used
> to be one field, pulling in opposite directions.
>
> **`mode`.** `fused` regions have verified AIS, so an unmatched detection is
> genuinely dark; analysis is refused (409) if the AIS buffer is empty rather than
> producing all-dark false positives. `survey` regions (Hormuz, Kerch, Somalia…)
> have no receiver coverage at all: fusion is skipped, `is_dark` stays `null`, and
> detections mean "a vessel was here", never "a vessel is running dark". See
> [ais-coverage.md](ais-coverage.md).

---

## `GET /api/vessels`

Latest AIS position per vessel inside an ROI, within a trailing
`VESSEL_ACTIVE_MINUTES` (default 30 min) window ending at `at`.

**Query parameters**

| Param | Type            | Default            | Description                          |
|-------|-----------------|--------------------|--------------------------------------|
| `roi` | string          | `singapore_strait` | ROI name; unknown name → 400         |
| `at`  | ISO-8601 string | now                | Positions as of this UTC moment. The frontend sets this to a SAR scene's `sensed_at` to show vessels at acquisition time. |

**Response** — array of vessel snapshots: `mmsi`, `time`, `lat`, `lon`, `sog`,
`cog`, `ship_name`, `ship_type` (AIS code, table below), `callsign`,
`nav_status`. Static fields are `null` until the vessel's `ShipStaticData`
broadcast is seen. `nav_status` is the raw ITU-R M.1371 navigational status
code (0-15) or `null` — the API never translates it; the frontend maps codes
to labels ("at anchor", "moored", etc.) for display.

```bash
curl "http://localhost:8000/api/vessels?roi=north_taiwan&at=2026-07-11T02:14:36Z"
```

---

## `GET /api/vessels/count`

Distinct vessels active in the ROI in the last `VESSEL_ACTIVE_MINUTES`.

| Param | Type   | Default            |
|-------|--------|--------------------|
| `roi` | string | `singapore_strait` |

**Response** `{ "count": 42 }`

---

## `GET /api/vessels/{mmsi}/track`

Position history for one vessel, oldest → newest.

| Param   | Type    | Default | Description                                              |
|---------|---------|---------|----------------------------------------------------------|
| `hours` | integer | 12      | Trailing window; clamped to `24 × AIS_RETENTION_DAYS`.   |

**Response** — array of `time`, `lat`, `lon`, `sog`, `cog`, `ship_name`,
`ship_type`, `callsign`. Empty array if the MMSI has no rows in the window.

---

## `POST /api/analysis/{roi}` 🔒 admin-only

Analyze the newest Sentinel-1 pass over the ROI: fetch pixels (Sentinel Hub
Process API — **spends Processing Units**, ~100 PU per ROI), run YOLOv8 ship
detection, fuse against the AIS buffer, store the results.

**Auth:** header `X-Analysis-Key: <ANALYSIS_API_KEY>`. This endpoint is never
exposed to regular users — analysis spends the operator's PU budget. Users see
results and pass times via the read-only endpoints below.

**Responses**

| Code | Meaning |
|------|---------|
| 202  | Accepted — `{ "scene_id": "...", "status": "processing" }`; poll `GET /api/scenes`. |
| 200  | Scene already processed — served from DB, 0 PU. |
| 400  | Unknown ROI. |
| 401  | Missing/wrong `X-Analysis-Key`. |
| 409  | Analysis already running for this ROI, or no eligible scene. Three causes: no pass in the last 3 days; none inside the AIS buffer (let AIS ingest a few hours first); or no pass imaging ≥85% of the ROI's `sar_bbox` — the swath only clipped it, and fetching would return a black chip. The message names which. All checked before any PU is spent. |
| 503  | `ANALYSIS_API_KEY` unset, CDSE credentials unset, or model checkpoint missing (`backend/models/sar_ship.pt`, see `ml/README.md`). All checked **before** any PU is spent. |

```bash
curl -X POST -H "X-Analysis-Key: $ANALYSIS_API_KEY" \
  http://localhost:8000/api/analysis/singapore_strait
```

---

## `GET /api/scenes`

Analyzed (and in-flight) SAR scenes for an ROI, newest first.

| Param   | Type    | Default            |
|---------|---------|--------------------|
| `roi`   | string  | `singapore_strait` |
| `limit` | integer | 10 (max 50)        |

**Response** — array of scene objects:

| Field             | Description                                        |
|-------------------|----------------------------------------------------|
| `id`              | CDSE product UUID                                  |
| `name`            | Sentinel-1 product name                            |
| `sensed_at`       | Acquisition timestamp — the correlation moment     |
| `platform`        | `S1A` / `S1C` / `S1D`                              |
| `status`          | `processing` / `processed` / `failed`              |
| `processed_at`    | When analysis finished; `null` until then          |
| `error`           | Failure reason when `status = failed`              |
| `footprint`       | GeoJSON polygon of the full Sentinel-1 swath — **not** the imaged area |
| `imaged_bbox`     | `[min_lon, min_lat, max_lon, max_lat]` pixels were actually fetched for; `null` for pre-existing scenes |
| `has_overview`    | Whether `overview.png` is available for this scene |
| `detection_count` | SAR detections, **excluding** land-masked ones      |
| `dark_count`      | Detections with no AIS match; always 0 in `survey` ROIs, where fusion never runs |
| `land_count`      | Detections dropped by the coastline mask (rocks, breakwaters, shore structures) |

> `footprint` and `imaged_bbox` are different rectangles. The footprint is the
> ~250 km product swath; the chip only covers the ROI's `sar_bbox`. Drape imagery
> on `imaged_bbox` — using the footprint will misregister it badly.

---

## `GET /api/scenes/{scene_id}/overview.png`

The downsampled SAR chip the analysis ran on, as `image/png` (grayscale, ≤2048 px
on the long edge, VV backscatter windowed −25…0 dB). Drape it over the scene's
`imaged_bbox`: it is axis-aligned EPSG:4326 with row 0 at the north edge, so it
maps directly onto a Leaflet `ImageOverlay` with no warping.

Downsampling max-pools rather than averages — a ship is a handful of bright
pixels on dark water, and averaging would erase the returns the detections are
drawn on.

| Status | Meaning |
|--------|---------|
| 200 | PNG body; `Cache-Control: public, max-age=86400, immutable` |
| 404 | Unknown scene, or a scene analyzed before imagery retention existed |

---

## `GET /api/scenes/{scene_id}/detections`

All detections for a scene, highest confidence first. 404 for unknown scenes.
Land-masked detections are omitted by default.

| Param          | Type | Default | Description |
|----------------|------|---------|-------------|
| `include_land` | bool | `false` | Also return detections inside the coastline mask. For auditing the mask — widening `LAND_MASK_BUFFER_M` eventually starts masking berthed ships, and this is how to see it. |

| Field                | Description                                            |
|----------------------|--------------------------------------------------------|
| `lat`, `lon`         | Detection centroid                                     |
| `confidence`         | Model confidence 0–1                                   |
| `confidence_bucket`  | `high` (≥0.7) / `medium` (≥0.4) / `low`                |
| `is_dark`            | `true` = no AIS match within 500 m / ±2 h; `null` = not fused (still processing, a `survey` ROI where no AIS exists to judge against, or land-masked) |
| `on_land`            | Inside the coastline mask — not a vessel. Never fused, never counted. Only ever `true` when `include_land=true` |
| `matched_mmsi`       | MMSI of the matched vessel; `null` when dark           |
| `match_distance_m`   | Distance to the matched AIS position                   |
| `match_time_delta_s` | Signed seconds between AIS fix and acquisition         |
| `ship_name`, `ship_type`, `callsign` | Static data of the matched vessel      |

---

## `GET /api/analysis/next-pass`

Free (0 PU) pass timing for an ROI, from the CDSE catalog. Cached 10 minutes.

| Param | Type   | Default            |
|-------|--------|--------------------|
| `roi` | string | `singapore_strait` |

```json
{
  "latest_scene_sensed_at": "2026-07-11T02:14:36Z",
  "next_expected_at": "2026-07-12T02:10:09Z",
  "last_processed_at": null
}
```

`next_expected_at` is the median interval of the last 14 days of passes rolled
forward — `null` when fewer than 3 passes exist (e.g. sparse Black Sea coverage).

---

## AIS ship type codes (common values)

| Code | Category          | Examples                     |
|------|-------------------|------------------------------|
| 30   | Fishing           |                              |
| 31–32 | Towing           |                              |
| 36   | Sailing           |                              |
| 37   | Pleasure craft    |                              |
| 40–49 | High-speed craft |                              |
| 60–69 | Passenger        | Ferries, cruise ships        |
| 70–79 | Cargo            | Container ships, bulk carriers |
| 80–89 | Tanker           | Oil, LNG, chemical tankers   |
| 90–99 | Other            | Military, diving ops, etc.   |

Full table: [ITU-R M.1371-5 Table 53](https://www.itu.int/rec/R-REC-M.1371/en)
