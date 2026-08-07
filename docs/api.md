# API Reference

Base URL (local): `http://localhost:8000`
Swagger: `http://localhost:8000/docs` — served only when `ENV` is not `production`
Base URL via Vite proxy: `http://localhost:5173` (proxies `/api` → backend)

Two route groups do not exist in production at all: `POST /api/analysis/{roi}`
and `/api/dev/*`. Both answer 404 there, with or without a valid key, and neither
appears in the schema. Everything else is public and read-only.

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
  { "name": "north_taiwan",     "label": "North Taiwan",
    "ais_bbox": [120.70, 24.90, 122.40, 26.30], "sar_bbox": [120.90, 24.95, 122.20, 25.60], "mode": "fused" },
  { "name": "gulf_of_finland",  "label": "Gulf of Finland",
    "ais_bbox": [24.50, 59.20, 28.60, 60.40], "sar_bbox": [25.20, 59.45, 27.60, 60.28], "mode": "fused" },
  { "name": "hormuz_strait",    "label": "Strait of Hormuz",
    "ais_bbox": [55.65, 26.00, 56.95, 27.00], "sar_bbox": [55.95, 26.15, 56.85, 26.85], "mode": "survey" }
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
`VESSEL_ACTIVE_MINUTES` (default 240 min / 4h) window ending at `at`.

**Query parameters**

| Param | Type            | Default            | Description                          |
|-------|-----------------|--------------------|--------------------------------------|
| `roi` | string          | `north_taiwan`      | ROI name; unknown name → 400         |
| `at`  | ISO-8601 string | now                | Positions as of this UTC moment. The frontend sets this to a SAR scene's `sensed_at` to show vessels at acquisition time. |

**Response** — array of vessel snapshots: `mmsi`, `time`, `lat`, `lon`, `sog`,
`cog`, `ship_name`, `ship_type` (AIS code, table below), `callsign`,
`flag_iso2`, `flag_country`, `nav_status`. Static fields are `null` until the
vessel's `ShipStaticData` broadcast is seen. `nav_status` is the raw ITU-R
M.1371 navigational status code (0-15) or `null` — the API never translates it;
the frontend maps codes to labels ("at anchor", "moored", etc.) for display.

`flag_iso2` / `flag_country` are **not broadcast over AIS** — no AIS message
carries a flag state. They are derived from the MMSI's ITU-R M.585 MID (its
first three digits) and so are available immediately, without waiting for a
static-data broadcast. Both are `null` when the MMSI is not a plain nine-digit
ship station: craft associated with a parent ship (`98MID…`), aids to navigation
(`99MID…`), SAR aircraft (`111MID…`) and SART/MOB/EPIRB beacons
(`970`/`972`/`974…`) have no flag state and are never guessed at.

```bash
curl "http://localhost:8000/api/vessels?roi=north_taiwan&at=2026-07-11T02:14:36Z"
```

---

## `GET /api/vessels/count`

Distinct vessels active in the ROI in the last `VESSEL_ACTIVE_MINUTES`.

| Param | Type   | Default            |
|-------|--------|--------------------|
| `roi` | string | `north_taiwan` |

**Response** `{ "count": 42 }`

---

## `GET /api/vessels/{mmsi}/track`

Position history for one vessel, oldest → newest.

| Param   | Type    | Default | Description                                              |
|---------|---------|---------|----------------------------------------------------------|
| `hours` | integer | 12      | Trailing window; clamped to `24 × AIS_RETENTION_DAYS`.   |

**Response** — array of `time`, `lat`, `lon`, `sog`, `cog`, `ship_name`,
`ship_type`, `callsign`, `flag_iso2`, `flag_country`. Empty array if the MMSI
has no rows in the window.

---

## `POST /api/analysis/{roi}` 🔒 **absent in production**

Analyze the newest Sentinel-1 pass over the ROI: fetch pixels (Sentinel Hub
Process API — **spends Processing Units**, ~100 PU per ROI), run YOLOv8 ship
detection, fuse against the AIS buffer, store the results.

**This is not the routine path.** A background scheduler sweeps every region and
analyzes each new usable pass automatically; nothing in the frontend can request
imagery.

**Not registered when `ENV=production`** — the path answers 404 there, with or
without a valid key, and does not appear in the schema. Production exposes no
endpoint that spends Processing Units, because two properties make a network-
reachable spend button unsafe to leave standing:

- it bypasses the scheduler's `PU_MONTHLY_CEILING`, and
- a scene that fails *after* its pixel fetch is retried on every call, each retry
  a fresh spend. The scheduler is protected from that by `scene_has_pu_spend`;
  this path is not.

In production, force a run over a shell instead — `backend/scripts/analyze.py`,
which runs the same pipeline, prints the estimated cost, and warns when the scene
was already paid for or when the run would cross the ceiling:

```bash
railway run python scripts/analyze.py north_taiwan
```

Outside production this endpoint remains available for operator recovery —
backfilling a region, retrying a scene whose fetch already spent PU, or
exercising a newly added region.

**Auth:** header `X-Analysis-Key: <ANALYSIS_API_KEY>`.

**Responses**

| Code | Meaning |
|------|---------|
| 202  | Accepted — `{ "scene_id": "...", "status": "processing" }`; poll `GET /api/scenes`. |
| 200  | Scene already processed — served from DB, 0 PU. |
| 400  | Unknown ROI. |
| 401  | Missing/wrong `X-Analysis-Key`. |
| 404  | `ENV=production` — the route is not registered. |
| 409  | Analysis already running for this ROI, or no eligible scene. Three causes: no pass in the last 3 days; none inside the AIS buffer (let AIS ingest a few hours first); or no pass imaging ≥85% of the ROI's `sar_bbox` — the swath only clipped it, and fetching would return a black chip. The message names which. All checked before any PU is spent. |
| 503  | `ANALYSIS_API_KEY` unset, CDSE credentials unset, or model checkpoint missing (`backend/models/sar_ship.pt`, see `ml/README.md`). All checked **before** any PU is spent. |

```bash
curl -X POST -H "X-Analysis-Key: $ANALYSIS_API_KEY" \
  http://localhost:8000/api/analysis/north_taiwan
```

---

## `GET /api/scenes`

Analyzed (and in-flight) SAR scenes for an ROI, newest first.

| Param   | Type    | Default            |
|---------|---------|--------------------|
| `roi`   | string  | `north_taiwan`      |
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
| `flag_iso2`, `flag_country` | Flag of the matched vessel, from its MMSI's ITU MID; `null` when dark, indeterminate, or matched to a non-ship-station MMSI |

---

## `GET /api/analysis/next-pass`

Free (0 PU) pass timing for an ROI, from the CDSE catalog. Cached 10 minutes.

| Param | Type   | Default            |
|-------|--------|--------------------|
| `roi` | string | `north_taiwan` |

```json
{
  "latest_scene_sensed_at": "2026-07-11T02:14:36Z",
  "next_expected_at": "2026-07-12T02:10:09Z",
  "last_processed_at": null
}
```

`next_expected_at` is the median interval of the last 14 days of passes rolled
forward — `null` when fewer than 3 passes exist (e.g. sparse Black Sea coverage).
It is an estimate from observed cadence, **not an orbit prediction**, and the
analysis follows the pass by however long CDSE takes to publish the GRD product
(hours).

---

## `GET /api/analysis/schedule`

Free (0 PU). Every region's next automatic analysis, the most recently completed
one, and month-to-date spend.

Only the **catalog** facts (`latest_scene_sensed_at`, `next_expected_at`) come
from the scheduler's last sweep, so no catalog call happens per request.
`last_processed_at`, `state`, and `most_recent` are read from the database on
every call — a region that has just finished analyzing must stop reporting
"analyzing"/"never analyzed" immediately, not at its next sweep up to
`SCHEDULER_INTERVAL_SECONDS` later.

```json
{
  "regions": [
    {
      "name": "gulf_of_finland",
      "label": "Gulf of Finland",
      "mode": "fused",
      "latest_scene_sensed_at": "2026-07-26T04:31:02Z",
      "next_expected_at": "2026-07-26T16:28:44Z",
      "last_processed_at": "2026-07-26T07:02:11Z",
      "state": "scheduled"
    }
  ],
  "most_recent": {
    "roi": "gulf_of_finland",
    "label": "Gulf of Finland",
    "mode": "fused",
    "sensed_at": "2026-07-26T04:31:02Z",
    "processed_at": "2026-07-26T07:02:11Z",
    "detection_count": 23,
    "dark_count": 2
  },
  "month_to_date_pu": 1240.5,
  "pu_monthly_ceiling": 24000.0
}
```

`most_recent` is the newest `processed` scene across **all** regions, or `null`
before the first analysis completes. `dark_count` is always 0 for a `survey`
region — those are never fused, so nothing in them can be called dark; clients
should not render a dark figure for them.

`regions` is **empty** until the first sweep completes, and whenever
`SCHEDULER_ENABLED=false` or the scheduler is idle for want of CDSE credentials
or a model checkpoint. Clients should render that as "no schedule yet" rather
than as an error.

| `state` | Meaning |
|---------|---------|
| `scheduled` | Pass still ahead; `next_expected_at` is the estimate. |
| `awaiting_publication` | Expected pass time has gone by. Normal — GRD products publish hours after acquisition. |
| `analyzing` | A fetch/detect/fuse run is in flight for this region now. |
| `unknown` | Fewer than 3 recent passes, so no interval to project. |

`month_to_date_pu` sums the `pu_ledger` table for the current calendar month.
Entries are written immediately *before* each pixel fetch, so a request that
dies mid-flight still counts — the PU is spent either way.

---

## `/api/dev/*` developer tools — **absent in production**

Reset endpoints for local iteration: SAR scenes, AIS data, and the PU ledger.
They replace the old unscoped `DELETE /api/analyses`.

These routes are **not registered** unless `ENV` is `development` or `staging`
*and* `DEVTOOLS_ENABLED=true` *and* `DEVTOOLS_API_KEY` is at least 32
characters. In production they do not exist — every path below answers **404**,
including with a valid key, and none appear in the OpenAPI schema. Setting
`ENV=production` with `DEVTOOLS_ENABLED=true` refuses to boot.

**Auth:** header `X-Devtools-Key: <DEVTOOLS_API_KEY>`. Separate from
`ANALYSIS_API_KEY` on purpose: a key that can read imagery should not also be
able to empty tables. Rejected attempts are logged.

The same operations are available without a key through
`backend/scripts/dev_reset.py`, which talks to the database directly — that is
the intended local path.

> **Resets re-spend PU.** Deleting a scene makes the scheduler see its pass as
> new, so the next sweep re-fetches and re-pays for that imagery. This is
> intended. Set `SCHEDULER_ENABLED=false` first if you don't want the re-fetch.

### `GET /api/dev/pu`

Month-to-date PU against the ceiling, plus a per-ROI breakdown and the most
recent ledger entries.

```json
{
  "month_to_date_pu": 1073.0,
  "pu_monthly_ceiling": 25000.0,
  "pu_monthly_budget": 30000,
  "remaining_under_ceiling": 23927.0,
  "by_roi": [{ "roi": "skagen_kattegat", "pu": 215.0, "entries": 1, "last_spent_at": "…" }],
  "recent": [{ "roi": "north_taiwan", "scene_id": "…", "pu": 185.2, "spent_at": "…" }],
  "all_time_pu": 2066.0,
  "all_time_entries": 28
}
```

### `DELETE /api/dev/pu?scope=month|all&roi=<name>`

Deletes ledger rows. `scope=month` (default) matches exactly the predicate the
ceiling reads, so the number it moves is the number the scheduler checks.
Returns `{ "entries_deleted": n, … }`.

This clears *our* ledger, not Copernicus's meter — the real monthly quota does
not come back.

### `DELETE /api/dev/scenes?roi=<name>` · `?scene_id=<id>` · `?all=true`

Deletes SAR scenes; detections cascade via the `sar_detections` → `sar_scenes`
FK. AIS is untouched. Exactly one selector is required — a bare call is a
**400**, never "delete everything". **409** if an analysis is in flight.

Returns `scenes_deleted`, `rois_affected`, and `projected_pu_respend` — what
the scheduler will spend re-fetching those regions.

### `DELETE /api/dev/ais`

Deletes every row in `ais_positions` **and** `ship_metadata`. Both, because the
vessel endpoints `LEFT JOIN ship_metadata`, so keeping it would surface names
for vessels that no longer exist.

Afterwards fused regions stop analyzing until the AIS buffer refills:
`find_target_scene` refuses a scene with no AIS in the ROI, and fusion needs
`FUSION_MAX_TIME_DELTA_HOURS` of buffer before the acquisition.

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
