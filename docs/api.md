# API Reference

Base URL (local): `http://localhost:8000`
Swagger: `http://localhost:8000/docs`
Base URL via Vite proxy: `http://localhost:5173` (proxies `/api` → backend)

All responses are JSON. Timestamps are ISO-8601 UTC.

---

## `GET /api/health`

Liveness check.

**Response**

```json
{ "status": "ok" }
```

---

## `GET /api/rois`

Returns the list of predefined Regions of Interest. The active ROI (set via `ACTIVE_ROI`) is the one currently being ingested; the others are available for future ROI-switching.

**Response** — array of ROI objects

| Field   | Type             | Description                                   |
|---------|------------------|-----------------------------------------------|
| `name`  | string           | Machine key (matches `ACTIVE_ROI` env values) |
| `label` | string           | Human-readable label                          |
| `bbox`  | [min_lon, min_lat, max_lon, max_lat] | Bounding box in WGS-84 degrees |

**Example**

```json
[
  { "name": "south_china_sea",      "label": "South China Sea",       "bbox": [105.0,  0.0, 122.0, 23.0] },
  { "name": "strait_of_hormuz",     "label": "Strait of Hormuz",      "bbox": [ 54.0, 24.0,  58.5, 27.5] },
  { "name": "gulf_of_guinea",       "label": "Gulf of Guinea",        "bbox": [ -5.0, -2.0,   9.0,  7.0] },
  { "name": "eastern_mediterranean","label": "Eastern Mediterranean", "bbox": [ 22.0, 30.0,  36.0, 38.0] }
]
```

---

## `GET /api/vessels`

Latest AIS position per vessel inside the active ROI, within a trailing 6-hour window.

**Query parameters**

| Param | Type            | Default | Description                                                     |
|-------|-----------------|---------|-----------------------------------------------------------------|
| `at`  | ISO-8601 string | now     | Return positions as of this UTC moment. Naive datetimes are treated as UTC. |

**Response** — array of vessel snapshot objects

| Field       | Type    | Description                                             |
|-------------|---------|---------------------------------------------------------|
| `mmsi`      | integer | Maritime Mobile Service Identity (vessel ID)            |
| `time`      | string  | UTC timestamp of the position                           |
| `lat`       | float   | Latitude (WGS-84)                                       |
| `lon`       | float   | Longitude (WGS-84)                                      |
| `sog`       | float   | Speed over ground (knots); `null` if unknown            |
| `cog`       | float   | Course over ground (degrees 0–360); `null` if unknown   |
| `ship_name` | string  | Vessel name from AIS static data; `null` until received |
| `ship_type` | integer | AIS ship type code (see table below); `null` until received |
| `callsign`  | string  | Radio callsign; `null` until received                   |

**Examples**

```bash
# Current snapshot
curl http://localhost:8000/api/vessels

# Snapshot 30 minutes ago
curl "http://localhost:8000/api/vessels?at=2026-05-10T03:00:00Z"
```

```json
[
  {
    "mmsi": 477996333,
    "time": "2026-05-10T03:19:08Z",
    "lat": 22.2906,
    "lon": 114.1685,
    "sog": 7.7,
    "cog": 295.4,
    "ship_name": "SEA SPARKLE",
    "ship_type": 40,
    "callsign": "VRS5586"
  },
  {
    "mmsi": 413465130,
    "time": "2026-05-10T03:18:55Z",
    "lat": 22.3465,
    "lon": 114.1127,
    "sog": 0.0,
    "cog": 74.8,
    "ship_name": null,
    "ship_type": null,
    "callsign": null
  }
]
```

**Notes**
- Each MMSI appears at most once (latest position in the window).
- `ship_name`, `ship_type`, and `callsign` are populated from `ShipStaticData` AIS messages, which vessels broadcast roughly every 6 minutes. Expect `null` for vessels seen only briefly.
- The 6-hour window is a fixed trailing window from `at`; it is not configurable at query time.

---

## `GET /api/vessels/{mmsi}/track`

Full position history for a single vessel, ordered oldest → newest. Suitable for rendering as a polyline on a map or as a timeline.

**Path parameter**

| Param  | Type    | Description                |
|--------|---------|----------------------------|
| `mmsi` | integer | Vessel MMSI to look up     |

**Query parameters**

| Param   | Type    | Default | Description                                                         |
|---------|---------|---------|---------------------------------------------------------------------|
| `hours` | integer | 72      | How many trailing hours of history to return. Clamped to `24 × AIS_RETENTION_DAYS` (default 168h / 7 days). |

**Response** — array of position objects, ascending by time

| Field       | Type    | Description                                             |
|-------------|---------|---------------------------------------------------------|
| `time`      | string  | UTC timestamp                                           |
| `lat`       | float   | Latitude                                                |
| `lon`       | float   | Longitude                                               |
| `sog`       | float   | Speed over ground (knots); `null` if unknown            |
| `cog`       | float   | Course over ground (degrees); `null` if unknown         |
| `ship_name` | string  | Vessel name (same value on every row, `null` if not yet received) |
| `ship_type` | integer | AIS ship type code; `null` if not yet received          |
| `callsign`  | string  | Radio callsign; `null` if not yet received              |

**Example**

```bash
# Last 24 hours of track for MMSI 477996333
curl "http://localhost:8000/api/vessels/477996333/track?hours=24"
```

```json
[
  { "time": "2026-05-10T03:17:55Z", "lat": 22.2906, "lon": 114.1685, "sog": 10.1, "cog": 75.4, "ship_name": "SEA SPARKLE", "ship_type": 40, "callsign": "VRS5586" },
  { "time": "2026-05-10T03:18:36Z", "lat": 22.2998, "lon": 114.1669, "sog": 1.5,  "cog": 80.2, "ship_name": "SEA SPARKLE", "ship_type": 40, "callsign": "VRS5586" },
  { "time": "2026-05-10T03:21:03Z", "lat": 22.2998, "lon": 114.1669, "sog": 0.0,  "cog": 65.5, "ship_name": "SEA SPARKLE", "ship_type": 40, "callsign": "VRS5586" }
]
```

**Notes**
- Returns an empty array if the MMSI has no data in the requested window.
- `ship_name`, `ship_type`, and `callsign` are the same on every row (joined from `ship_metadata`).

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

---

## Switching the active Region of Interest

The active ROI controls which bounding box the AISStream WebSocket subscribes to. It is set at startup via the `ACTIVE_ROI` environment variable.

**Available values**

| `ACTIVE_ROI` value      | Region                 | Bbox (lon_min, lat_min, lon_max, lat_max) |
|-------------------------|------------------------|-------------------------------------------|
| `south_china_sea`       | South China Sea        | 105, 0, 122, 23                           |
| `strait_of_hormuz`      | Strait of Hormuz       | 54, 24, 58.5, 27.5                        |
| `gulf_of_guinea`        | Gulf of Guinea         | -5, -2, 9, 7                              |
| `eastern_mediterranean` | Eastern Mediterranean  | 22, 30, 36, 38                            |

**How to switch**

1. Edit `backend/.env`:
   ```
   ACTIVE_ROI=strait_of_hormuz
   ```
2. Restart the backend:
   ```bash
   docker compose restart backend
   ```
   The new subscription takes effect on the next WebSocket connection (within a few seconds of restart).

**Limitation:** live ROI switching (without a restart) is not yet implemented. The AISStream protocol supports it on the same connection, but the frontend ROI selector that would drive it hasn't been built yet. This is planned for the Weeks 7–9 UI phase.

**Data after switching:** only positions received after the restart appear in the new ROI. Historical data from the previous ROI is retained in `ais_positions` (subject to the 7-day retention window) and is still queryable by MMSI via `/api/vessels/{mmsi}/track`.
