# API Reference

Base URL (local): `http://localhost:8000`
Swagger: `http://localhost:8000/docs`
Base URL via Vite proxy: `http://localhost:5173` (proxies `/api` → backend)

All responses are JSON. Timestamps are ISO-8601 UTC.

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

| Field   | Type   | Description          |
|---------|--------|----------------------|
| `name`  | string | Machine key          |
| `label` | string | Human-readable label |
| `bbox`  | [min_lon, min_lat, max_lon, max_lat] | WGS-84 degrees |

```json
[
  { "name": "singapore_strait",  "label": "Singapore Strait",                        "bbox": [103.55, 1.03, 104.10, 1.35] },
  { "name": "north_taiwan",      "label": "North Taiwan / ECS approaches",           "bbox": [120.70, 25.10, 122.40, 26.30] },
  { "name": "gulf_of_finland",   "label": "Gulf of Finland (shadow-fleet corridor)", "bbox": [24.50, 59.20, 28.60, 60.30] },
  { "name": "skagen_kattegat",   "label": "Skagen Anchorage (Kattegat)",             "bbox": [10.00, 57.30, 11.80, 58.30] },
  { "name": "bosphorus_marmara", "label": "Bosphorus Approaches (Sea of Marmara)",   "bbox": [28.45, 40.72, 29.45, 40.98] },
  { "name": "malta_hurds_bank",  "label": "Hurd Bank (Malta offshore STS)",          "bbox": [14.20, 35.60, 15.00, 36.20] }
]
```

> Every ROI is probe-verified against live AISStream coverage — the narrative
> *and* the receiver network have to line up, or fusion has nothing to correlate
> against. See [ais-coverage.md](ais-coverage.md) for the verification data and
> why regions like Hormuz, the Spratlys, and Kerch were dropped. Analysis in an
> ROI whose AIS buffer is empty is refused (409) rather than producing all-dark
> false positives.

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
`cog`, `ship_name`, `ship_type` (AIS code, table below), `callsign`. Static
fields are `null` until the vessel's `ShipStaticData` broadcast is seen.

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
| 409  | Analysis already running for this ROI, or no eligible scene (none in the last 3 days inside the AIS buffer — let AIS ingest a few hours first). |
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
| `footprint`       | GeoJSON polygon of the imaged area                 |
| `detection_count` | Total SAR detections                               |
| `dark_count`      | Detections with no AIS match                       |

---

## `GET /api/scenes/{scene_id}/detections`

All detections for a scene, highest confidence first. 404 for unknown scenes.

| Field                | Description                                            |
|----------------------|--------------------------------------------------------|
| `lat`, `lon`         | Detection centroid                                     |
| `confidence`         | Model confidence 0–1                                   |
| `confidence_bucket`  | `high` (≥0.7) / `medium` (≥0.4) / `low`                |
| `is_dark`            | `true` = no AIS match within 500 m / ±2 h; `null` = not yet fused |
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
