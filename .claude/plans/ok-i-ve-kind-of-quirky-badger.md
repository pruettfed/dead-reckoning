# Pivot: Optical → SAR-only

## Context

The project was built around **Planet PlanetScope optical** imagery as the primary detection
source. Hands-on exploration exposed two fatal problems with optical for dark-vessel work:
clouds block ocean scenes most of the time, and per-scene coverage is partial/inconsistent.
Research confirmed the industry-standard fix: **Sentinel-1 SAR** — free, all-weather, systematic
250 km-swath coverage, with ship hulls appearing as bright returns on dark water. A live CDSE
catalog query verified **~daily** Sentinel-1 coverage over right-sized coastal ROIs (Fujairah
13/15 days, Rotterdam 15/15), via the new S1A/S1C/S1D constellation.

The conceptual reframe that makes this work: dark-vessel detection is **single-snapshot
correlation**, not real-time tracking. Each SAR pass is an independent event — detect every hull
in the scene, compare against AIS interpolated to the acquisition timestamp, flag hulls with no
AIS within ~500 m / a few minutes. This dissolves the "imagery cadence must match AIS" and
"track ships across days" concerns entirely.

This change pivots the codebase to **SAR-only**: rewrite the project doc, replace the ROIs with
water-centered gray-zone hotspots, simplify AIS retention to match the snapshot model, and
scaffold SAR access against CDSE while staying inside a 30,000 Processing-Unit/month budget.

Intended outcome: an honest, credible, demo-worthy maritime-OSINT pipeline whose only changed
assumption (vs. what's already built and hardened) is the optical→SAR sensor swap. The AIS
ingestion, PostGIS buffer, and `ST_DWithin` fusion stub all survive untouched in principle.

## Decisions locked with user

- **AIS:** keep the hardened always-on AISStream WebSocket; just shorten retention (7d → 2d).
  True pass-windowed capture is deferred to the fusion phase as a per-event snapshot table.
- **SAR access:** scaffold only — add CDSE credentials + a free **catalog search** helper now;
  leave the pixel-fetch method (GRD download / COG `/vsicurl` vs. Sentinel Hub Process API) as a
  documented stub to pick when detection is actually built. Catalog search costs 0 PU.
- **ROIs:** Fujairah/Hormuz, Taiwan Strait, Spratly/S. China Sea, NE Black Sea (Kerch).

---

## 1. Rewrite `CLAUDE.md` (SAR-only)

Describe the project cleanly as it stands now — **no pivot/history narrative**, just present
Sentinel-1 SAR as the detection source. Remove all Planet/optical-primary framing:

- **Intro/Repo state:** describe the platform as SAR-based; drop "Planet Labs PlanetScope API
  access acquired" and any optical-primary language. State the current phase plainly.
- **Architecture stage 2 (detection — UNCHANGED in concept, still ML object detection):** the SAR
  scene is still **run through a detection model** to produce vessel centroids + confidence. What
  changes: input is **Sentinel-1 IW `GRDH` VV+VH via CDSE** (not optical), and the model is
  **YOLOv8 fine-tuned on a SAR ship dataset (HRSID / SSDD)** instead of the optical HRSC2016 (an
  optical-trained model can't read radar). Note: **only assert dark/not-dark inside the actual
  image footprint** (ships outside it are *unobserved*, not "not dark") — this is a fusion rule,
  detection still runs normally on whatever pixels exist.
- **Architecture stage 3 (fusion):** add two rules — (a) clip the AIS comparison to
  `ROI ∩ image-footprint` at the single acquisition timestamp; (b) **never mosaic passes for the
  correlation** (different times = vessels moved); mosaic only as a visual backdrop.
- **External APIs:** replace the Planet section with **Sentinel-1 via CDSE** as primary; demote
  SAR from "future/optional." Note the **30,000 PU/month** budget, that **catalog search is free**,
  and that the **Copernicus Browser visual exploration consumes PU** (do discovery via the catalog
  API, not the Browser).
- **Constraints:** replace "Optical source via env / PLANET_API_KEY" with CDSE
  (`CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET`); add **credit-budget** constraint; add **ROIs must be
  water-centered and ≤ ~250 km** (one swath) so a single pass covers them whole.
- **Phase context / Layout:** update the detection-training reference to the SAR ship dataset
  (HRSID/SSDD, not HRSC2016) and rename `optical.py` → `sar.py` in the tree.

## 2. Redefine ROIs — `backend/app/rois.py`

Replace the 4 oversized boxes with 4 water-centered, ≤~90 km hotspots. Keep the existing `ROI`
dataclass and `get_roi()` helper unchanged. New bboxes `(min_lon, min_lat, max_lon, max_lat)`:

- `strait_of_hormuz` — "Fujairah Anchorage (Gulf of Oman)" — `(56.50, 25.00, 57.10, 25.60)` —
  Iran STS / sanctions tankers. **Hero ROI.**
- `taiwan_strait` — "Taiwan Strait" — `(119.00, 23.70, 119.80, 24.50)` — mid-channel gray-zone.
- `spratly_islands` — "Spratly Islands (S. China Sea)" — `(114.80, 9.60, 115.60, 10.40)` —
  maritime-militia vessels going dark (open water; tiny reefs only).
- `black_sea` — "NE Black Sea (Kerch approaches)" — `(36.50, 44.20, 37.30, 44.90)` —
  Russian shadow-fleet sanctions evasion (offshore Novorossiysk/Kerch, open water).

## 3. AIS efficiency — continuous + short retention

- `backend/app/config.py`: change `ais_retention_days` default `7 → 2`.
- `backend/.env.example`: update `AIS_RETENTION_DAYS=2` with a comment explaining the snapshot
  model (2 days comfortably brackets ~daily passes + SAR latency + UI tracks).
- No change to `ingest.py` ingestion logic — it already reads `ais_retention_days` from settings;
  `run_retention()` hourly prune stays. The always-on WebSocket and durability hardening are kept.
- `backend/app/main.py`: update the default `roi=` query param in `vessel_count` and `list_vessels`
  from `"south_china_sea"` → `"strait_of_hormuz"` (the old key no longer exists). `vessel_track`'s
  `max_hours = 24 * ais_retention_days` auto-adjusts to 48 h — no code change.
- **Deferred (documented, not built):** the durable per-event AIS snapshot table that captures the
  matched AIS at each processed SAR pass — belongs to the fusion phase.

## 4. SAR scaffolding + credit safety

- Rename `backend/app/optical.py` → `backend/app/sar.py` (`git mv`). Rewrite the docstring/stub for
  **Sentinel-1 IW GRDH VV+VH via CDSE**. Include a **free catalog-search** helper signature
  (CDSE OData / STAC by ROI bbox + date range — the same query proven in this session) and a clear
  **stub marker** for the pixel-fetch step (GRD download / COG `/vsicurl` vs. Process API — to be
  chosen at build). Document the credit rule inline: catalog = free; pixel fetch = the only
  PU-spending step; prefer GRD/COG over Process API; never drive discovery through the Browser.
- `backend/app/config.py`: **remove** `planet_api_key`; **add** `cdse_client_id` /
  `cdse_client_secret` (`str | None = None`, aliases `CDSE_CLIENT_ID` / `CDSE_CLIENT_SECRET`).
- `backend/.env.example`: replace the `PLANET_API_KEY` block with `CDSE_CLIENT_ID=` /
  `CDSE_CLIENT_SECRET=` and a one-line note on the 30k PU/month budget.
- `backend/app/fusion.py`: extend the one-line docstring to name the footprint-clipping +
  no-mosaic-for-correlation rules (keeps the stub honest for the next phase).

## 5. Update the test suite — `backend/tests/`

- Update `test_rois.py` (and any other test) that asserts the old ROI keys/labels
  (`south_china_sea`, `gulf_of_guinea`, `eastern_mediterranean`) to the 4 new keys, and update any
  bbox assertions.
- Adjust any test referencing `optical.py` / Planet to the new `sar.py` module.
- Suite must pass (`.venv/bin/pytest`) before the work is considered done.

## 6. Cleanup / references to update

- Grep the repo for the old ROI keys and any `planet`/`optical` references; update
  `frontend/src/App.tsx` / `api.ts` if they hardcode a `roi=` value.
- `requirements.txt` / `requirements-ml.txt`: no change in this pass (pixel-fetch deps deferred with
  the scaffold-only decision).

## Critical files

- `CLAUDE.md` — full SAR-only rewrite
- `backend/app/rois.py` — 4 new water-centered ROIs (reuse `ROI` / `get_roi`)
- `backend/app/config.py` — drop `planet_api_key`; add CDSE creds; `ais_retention_days` 7→2
- `backend/.env.example` — mirror config changes
- `backend/app/sar.py` (renamed from `optical.py`) — Sentinel-1 stub + free catalog-search helper
- `backend/app/main.py` — default `roi=` → `strait_of_hormuz`
- `backend/app/fusion.py` — docstring: footprint-clip + no-mosaic rules
- `backend/tests/`, `frontend/src/App.tsx` — stale ROI-key / source references

## Verification

1. **Tests green:** `cd backend && .venv/bin/pytest` — including the updated ROI-key assertions.
2. **Backend boots:** `docker compose up --build` → `GET /api/health` returns `200` with the `ais`
   source; `GET /api/rois` lists exactly the 4 new ROIs with correct bboxes.
3. **Endpoints use new defaults:** `GET /api/vessels` and `/api/vessels/count` (no `roi`) resolve to
   `strait_of_hormuz` without 400s; passing each new ROI name works; an old key returns 400.
4. **AIS still flows:** with `AISSTREAM_API_KEY` set, confirm rows accrue
   (`docker compose exec db psql -U dvd -d dvd -c "select count(*) from ais_positions;"`) and that
   retention prunes at the 2-day boundary (log line "pruned N rows older than 2 days").
5. **Catalog search works for 0 PU:** call the new `sar.py` catalog helper against the Fujairah
   bbox for the last 14 days; confirm it returns `IW_GRDH_1SDV` products and that the CDSE
   dashboard PU counter does **not** move.
