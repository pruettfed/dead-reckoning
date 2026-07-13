# Finish Dead Reckoning: SAR fetch → YOLO detection → AIS fusion → map UI

## Context

The AIS half of the platform is done and hardened (ingestion, retention, health, vessel endpoints). Everything downstream is stubbed: no pixel fetch, no detection model, no fusion, no scene/detection tables, and the frontend is a JSON-dump page (react-leaflet installed but unused). This plan builds the remaining pipeline so a locally-running demo works end-to-end: an admin triggers analysis of the latest Sentinel-1 pass over an ROI → backend fetches calibrated pixels via Sentinel Hub Process API (CDSE) → runs a fine-tuned YOLOv8 checkpoint → fuses against the live AIS buffer with `ST_DWithin` → frontend shows dark/matched detections over live vessels on a leaflet map.

## Decisions locked with user (2026-07-11)

1. **YOLOv8 fine-tune via Colab** — full training pipeline in-repo (`ml/`); user runs it on free Colab GPU and drops `best.pt` into `backend/models/sar_ship.pt`. Inference works the moment the checkpoint lands; clean 503 while absent.
2. **Pixel fetch = Sentinel Hub Process API now, GRD/COG later** — behind the existing `fetch_scene_pixels` signature so a GRD backend can swap in. ~100 PU per analysis ≈ 300/month within the 30k budget. CLAUDE.md's "prefer GRD" constraint gets updated to reflect this.
3. **Analysis trigger is admin-gated, never public** — `POST /api/analysis/{roi}` requires `X-Analysis-Key` header matching `ANALYSIS_API_KEY`. Users only see results + last/next-pass info (next pass estimated from free catalog cadence — no orbit prediction).
4. **Local demo is the finish line** — no Railway/Vercel work. Compose-mirrors-prod still applies.
5. **Frontend stays a skeleton** — functional, zero design polish.

## Fetch design (concrete)

- Process API `https://sh.dataspace.copernicus.eu/api/v1/process`, OAuth2 client-credentials token from `https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token` (module-level cache, refresh 60s early).
- Evalscript: S1 GRD **VV only**, `10*log10(VV)` clipped to `[-25, 0]` → UINT8 PNG (sea dark, ships bright — matches HRSID 8-bit grayscale). Decode with Pillow; **no rasterio/GDAL**.
- 10 m/px, EPSG:4326. ROI ~0.6° ≈ 6050×6670 px > Process API's 2500 px cap → fetch as **3×3 grid of ≤2400 px tiles** on one shared pixel grid, stitch to one numpy array + linear geotransform. ≤4 concurrent requests.
- **Single pass, never mosaic**: `dataFilter.timeRange = [sensed_at − 1 min, sensed_at + 10 min]`, `mosaickingOrder: mostRecent`. `processing: {orthorectify: true, backCoefficient: "SIGMA0_ELLIPSOID"}`.

---

## Section 1 — Schema + config

Files: `backend/app/models.py`, `backend/app/config.py`, `backend/.env.example`, `docker-compose.yml`, `.gitignore`

- `SarSceneRow` (`sar_scenes`): `id: str` PK (CDSE product UUID), `name`, `roi`, `sensed_at DateTime(tz)`, `footprint Geography(POLYGON, 4326, spatial_index=True)`, `platform`, `status` (processing/processed/failed), `processed_at | None`, `error | None`. Index `(roi, sensed_at)`.
- `SarDetection` (`sar_detections`): `id BigInteger` PK, `scene_id` FK → `sar_scenes.id ON DELETE CASCADE` (indexed), `location Geography(POINT, 4326, spatial_index=True)`, `confidence Float`, `confidence_bucket str`, `is_dark bool | None`, `matched_mmsi BigInteger | None`, `match_distance_m Float | None`, `match_time_delta_s Float | None`.
- `config.py` additions (match existing style/aliases): `analysis_api_key: str | None` (ANALYSIS_API_KEY), `model_path: str = "models/sar_ship.pt"` (MODEL_PATH), `fusion_max_distance_m: float = 500`, `fusion_max_time_delta_hours: float = 2`, `detection_conf_threshold: float = 0.25`.
- `.env.example`: mirror all five with comments (contract rule).
- `docker-compose.yml`: backend volume `./backend/models:/app/models` (drop-in checkpoint without rebuild).
- `.gitignore`: add `*.pt`.
- `create_all` only adds missing tables — later column changes need `docker compose down -v` (document in README).

**Verify:** compose up → psql `\dt` shows both tables with GIST indexes; existing pytest green.

## Section 2 — SAR pixel fetch

Files: `backend/app/sar.py` (extend; replace `fetch_scene_pixels` stub, remove `detect_vessels` stub — detection moves to `detect.py`), `backend/requirements.txt` (+`numpy`, `pillow`)

- Pure, testable: `plan_fetch_grid(bbox, m_per_px=10.0, max_px=2400) -> list[FetchTile]` (degree→px via cos(mid-lat), exact cover); `build_process_request(scene, bbox, width, height) -> dict`.
- Async: `_get_token(client) -> str` (raises `SarCredentialsMissing` if creds unset); `fetch_scene_pixels(scene, bbox, *, client=None) -> SarChip` where `SarChip(pixels: np.uint8 ndarray, bbox, width, height)`.
- Update module docstring: Process API is the chosen path (~100 PU/analysis); GRD/COG remains a future backend behind the same signature; catalog stays free.

**Verify:** new pytest — grid covers bbox exactly, all tiles ≤2400 px, request body narrows timeRange to the scene, UINT8/VV output. Live fetch deferred to final verification (spends PU once).

## Section 3 — ML training pipeline (`ml/` at repo root)

Files (new): `ml/README.md` (Colab runbook), `ml/prepare_dataset.py`, `ml/train.py`, `ml/eval.py`, `ml/hrsid.yaml`, `backend/requirements-ml.txt`, `backend/Dockerfile` (install ml reqs)

- `prepare_dataset.py`: generic COCO-JSON → YOLO-txt converter (single class `ship`), writes `images/{train,val}` + `labels/{train,val}`. Pure helper `coco_bbox_to_yolo(bbox, img_w, img_h)`. Works for HRSID (primary), and unchanged for **LS-SSDD-v1.0** (Sentinel-1-native, best domain match) or SSDD as fallbacks — note all three in the runbook with download sources + licenses (academic use, cite Wei et al. 2020).
- `train.py`: `YOLO("yolov8n.pt").train(data="hrsid.yaml", imgsz=800, epochs=50, batch=16, device=0)`. `eval.py`: `model.val(...)` → mAP50/mAP50-95.
- `ml/README.md`: copy-paste Colab cells (markdown, not .ipynb): install → upload dataset zip → prepare → train (T4 ~1–2 h) → eval → download `best.pt` → place at `backend/models/sar_ship.pt`. Include a sanity step: run the model on one real fetched chip before trusting it (domain-gap check).
- `requirements-ml.txt`: `--extra-index-url https://download.pytorch.org/whl/cpu`, `torch`, `ultralytics`. Dockerfile installs it after base requirements (layer caching). Image grows ~1.2 GB — acceptable locally; flag for the later deploy pass.

**Verify:** `python ml/prepare_dataset.py --help` runs; `coco_bbox_to_yolo` unit-tested; docker image rebuilds and boots.

## Section 4 — Detection inference (`backend/app/detect.py`, new)

- Pure helpers (no torch import at module top): `iter_tiles(width, height, tile=800, overlap=160)`; `pixel_to_lonlat(col, row, bbox, width, height)` (row 0 = north); `merge_detections(dets, iou_threshold=0.5)` (greedy NMS, pure-python IoU — dedupes tile-overlap hits); `bucket_confidence(conf)` → high ≥0.7 / medium ≥0.4 / low.
- `PixelDetection = (x1, y1, x2, y2, conf)`; `GeoDetection(lon, lat, confidence, bucket)`.
- `Detector` Protocol (`detect_tile(tile) -> list[PixelDetection]`) — injectable; tests use a fake. `YoloDetector` lazy-imports ultralytics; grayscale → 3-channel; applies conf threshold. `load_detector(model_path)` raises `DetectorUnavailable(reason)` if deps/checkpoint missing.
- `run_detection(chip, detector) -> list[GeoDetection]` is **sync**; the pipeline wraps it in `asyncio.to_thread` (torch must not block the event loop).

**Verify:** pytest — tiling coverage/clamping edges, corner+center pixel→lonlat, seam dedupe via fake detector emitting the same box in two overlapping tiles, bucket boundaries.

## Section 5 — Fusion + pipeline + endpoints

Files: `backend/app/fusion.py`, `backend/app/pipeline.py` (new), `backend/app/main.py`

- `fusion.py`:
  - `coverage_ok(sensed_at, min_ais_time, window_hours) -> bool` — pure guard. **Refuse to analyze (409) any scene whose `sensed_at − 2 h` predates `min(ais_positions.time)` — checked before any PU is spent** (2-day retention would otherwise mark everything falsely dark).
  - Insert detections; then footprint clip: `DELETE FROM sar_detections d USING sar_scenes s WHERE d.scene_id = :sid AND NOT ST_Covers(s.footprint, d.location)` (chip bbox = ROI, so ROI∩footprint clip is complete).
  - One readable `FUSE_QUERY`: CTE with `LEFT JOIN LATERAL` — nearest AIS per detection via `ST_DWithin(geography, :max_dist_m)` + `p.time BETWEEN sensed_at ± make_interval(hours => :h)` + `ORDER BY d.location <-> p.location LIMIT 1`; then `UPDATE sar_detections SET matched_mmsi, match_distance_m, match_time_delta_s, is_dark = (mmsi IS NULL)`.
  - `async fuse_scene(session, scene_id, sensed_at) -> dict` (counts).
- `pipeline.py`:
  - Pure: `pick_scene(scenes, processed_ids, min_ais_time, window_hours) -> SarScene | None`; `estimate_next_pass(sensed_times, now) -> datetime | None` (median consecutive interval over recent catalog scenes, ≥3 required, rolled past now).
  - `async analyze_roi(roi, *, fetcher=fetch_scene_pixels, detector_factory=load_detector)`: search last 3 days (0 PU) → guard/pick → upsert scene `processing` → fetch (mark `sources` `"sar_sentinel1"` connected/message/error — the health source tests already expect) → `to_thread(run_detection)` → insert/clip/fuse → `processed`; exceptions → `failed` + error. Idempotent: already-`processed` scene short-circuits (0 PU).
  - Module `_in_flight: dict[str, asyncio.Task]` (one analysis per ROI); 10-min TTL cache for next-pass catalog queries.
- `main.py`:
  - Pure `check_admin_key(provided, configured)` — 503 if unconfigured, 401 on mismatch (`secrets.compare_digest`); wired as a Header dependency.
  - `POST /api/analysis/{roi}` (admin) — pre-check detector availability + CDSE creds (503 **before** PU spend), 409 if in-flight/no eligible scene, else spawn task → `202 {scene_id, status}`.
  - `GET /api/scenes?roi=&limit=10` — scenes + `ST_AsGeoJSON(footprint)` + dark/total counts (one grouped query).
  - `GET /api/scenes/{scene_id}/detections` — joined to `ship_metadata` for matched names.
  - `GET /api/analysis/next-pass?roi=` — `{latest_scene_sensed_at, last_processed_at, next_expected_at}` (None-safe).
  - Lifespan: `sources.mark_disconnected("sar_sentinel1")` at startup so health lists it.

**Verify:** pytest — `coverage_ok`, `pick_scene`, `estimate_next_pass`, `check_admin_key`. SQL/network untested per repo convention (pure-function suite only).

## Section 6 — Frontend skeleton

Files: `frontend/src/api.ts`, `App.tsx`, `main.tsx`, `index.css`, new `src/types.ts`, `src/components/{MapView,VesselLayer,SceneLayer,ScenePanel}.tsx`, `frontend/.env.example`

- `api.ts`: `apiGet<T>(path, params?)` via URLSearchParams + `apiPost<T>(path, headers?)`.
- `App.tsx`: state `{roi, selectedSceneId, at}`; left panel (ROI `<select>` from `/api/rois`, ScenePanel, health line) + full-height MapView. **Selecting a scene sets vessel layer `?at=sensed_at`; "Live" button clears it** (the scene IS the time control; no free scrubber).
- `VesselLayer`: CircleMarkers, `refetchInterval: at ? false : 15000`; click → fetch track → Polyline. Popup: name/mmsi/sog/time.
- `SceneLayer`: footprint Polygon from GeoJSON; detection markers **red = dark, green = matched, gray = unfused**; popup: confidence + bucket + matched mmsi/name/distance.
- `ScenePanel`: scene list with status badges; next-pass line; refetch 30 s while any scene `processing`; "Run analysis" button rendered **only if `import.meta.env.VITE_ANALYSIS_API_KEY` is set** (dev-only — document in `frontend/.env.example` that this must never be set in a deployed build; it bakes into the bundle).
- Map recenters on ROI change (`useMap()` helper). Leaflet CSS import in `main.tsx`; flex layout only in `index.css`.

**Verify:** `pnpm dev` against compose — map renders, live vessels for strait_of_hormuz, ROI switch recenters, scene select freezes vessel time.

## Section 7 — Docs + cleanup

Files: `CLAUDE.md`, `README.md`, `docs/api.md`

- `CLAUDE.md`: repo-state update; **rewrite PU constraint** (Process API chosen, ~100 PU/analysis, tiled ≤2400 px, single-pass timeRange; GRD/COG a future backend); layout tree (+`ml/`, `detect.py`, `pipeline.py`); commands (+`requirements-ml` install, MODEL_PATH, ml runbook pointer); phase context.
- `README.md`: fix stale `docs/scaffold-smoke-test.md` link; analysis feature + checkpoint drop-in setup step; `down -v` note for schema changes.
- `docs/api.md`: full rewrite — current ROI keys (drop `south_china_sea`/`gulf_of_guinea`/`eastern_mediterranean` and the dead `ACTIVE_ROI` mechanism), all new endpoints with examples + auth errors.
- Flag only (don't delete): `data/rois/*.geojson` uses 3 old ROI names — stale local artifacts (gitignored), user's call.

**Verify:** every var in `config.py` appears in `.env.example`; no stale ROI keys grep-able in docs.

---

## Final end-to-end verification (ordered)

1. `cd backend && .venv/bin/pytest` — all green (existing + new suites).
2. `docker compose up --build` — new tables exist; `/api/health` lists `ais` + `sar_sentinel1`.
3. No creds/model: `POST /api/analysis/strait_of_hormuz` without header → 401; with key, no model → 503 (0 PU). `GET /api/analysis/next-pass?roi=strait_of_hormuz` returns estimate (free).
4. **[USER: CDSE creds + Colab checkpoint required]** With `CDSE_CLIENT_ID/SECRET` in `backend/.env` and `backend/models/sar_ship.pt` present, AIS ingesting ≥2 h: trigger analysis → poll `/api/scenes` until `processed` (~30–90 s); check CDSE dashboard billed ~100 PU once.
5. `/api/scenes/{id}/detections` — coordinates inside ROI, mix of dark/matched.
6. Frontend: footprint + red/green markers over live AIS; scene click sets vessel time; dev trigger button only with `VITE_ANALYSIS_API_KEY`.
7. Re-POST same ROI → instant 202 with same scene id (idempotent, 0 PU).

## Blocked-on-user inputs (marked in code/docs where needed)

- `AISSTREAM_API_KEY` (already in use), `CDSE_CLIENT_ID`/`CDSE_CLIENT_SECRET` (register at dataspace.copernicus.eu), `ANALYSIS_API_KEY` (any secret string you choose), and the Colab training run producing `backend/models/sar_ship.pt`.

## Risks

- **Domain gap (highest)**: HRSID is 0.5–3 m imagery; our chips are 10 m/px Sentinel-1. LS-SSDD-v1.0 (Sentinel-1 IW GRD native) is the domain-matched alternative — same converter, documented in runbook. Sanity-check the checkpoint on one real chip before trusting results.
- **HRSID hosting** (Google Drive links rot) — fallbacks documented.
- **Black Sea ROI** Sentinel-1 coverage is sparse — estimator returns null gracefully; demo leads with Hormuz.
- **Process API PU multipliers** (ortho ×2 etc.) — estimate verified once against the dashboard in step 4.
- `--reload` kills in-flight analysis on code edit — dev annoyance, documented.
