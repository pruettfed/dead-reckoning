# Vessel Display Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "hide small vessels" toggle to the live AIS layer, and narrow the AIS layer to only vessels tied to a SAR detection whenever a scene is selected, so matched pairs read 1-to-1 and dark vessels stand out as a visible absence.

**Architecture:** One backend addition (a `candidate_mmsi` column so indeterminate detections expose which AIS vessel they're ambiguous against, not just a margin) threaded through `fusion.py` and the detections endpoint. Three frontend changes: lift the detections query out of `SceneLayer` into `App.tsx` so both it and `VesselLayer` can see it, add a size-filter checkbox, and filter `VesselLayer` to the matched/candidate set whenever a scene is selected.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / asyncpg (backend); Vite / React 18 / TS / react-leaflet / TanStack Query (frontend).

## Global Constraints

- No Alembic in this repo — `create_all` won't ALTER existing tables. The new `candidate_mmsi` column requires `docker compose down -v` before the next `up` to actually appear.
- Backend tests are a pure-function suite only (no DB/network/torch) — `cd backend && .venv/bin/pytest`. Baseline before this work: **2 pre-existing failures** in `tests/test_detect.py::TestBucketConfidence::test_boundaries` (`0.699-medium` and `0.399-low`), unrelated to this change. Success criterion for backend tasks is "no new failures beyond those 2," not "all green."
- Frontend has no test framework configured (`frontend/package.json` has no test script). Verification is `cd frontend && pnpm exec tsc -b --noEmit` for type-checking plus a manual check in the browser (`pnpm dev`, proxies `/api` → :8000).
- Never reimplement distance/matching logic in Python — `classify()` in `backend/app/fusion.py` is unchanged by this plan; only its inputs/outputs get one more field threaded alongside them.
- Match `dark` detections get no candidate exposed — a "nearest vessel" on a detection we're calling genuinely dark would look like an implied pairing that isn't real. Only `indeterminate` detections carry `candidate_mmsi`.

---

### Task 1: Backend — `candidate_mmsi` column threaded through fusion and the API

**Files:**
- Modify: `backend/app/models.py:127` (`SarDetection`, add column after `matched_mmsi`)
- Modify: `backend/app/fusion.py:174-187` (`DARK_MARGINS`), `backend/app/fusion.py:243-255` (`APPLY_MATCH`), `backend/app/fusion.py:257-264` (`RESET_MATCH`), `backend/app/fusion.py:408-434` (`fuse_scene` loop)
- Modify: `backend/app/main.py:304-318` (`DETECTIONS_QUERY`)

**Interfaces:**
- Consumes: nothing new — `dr` CTE (`backend/app/fusion.py:131-153`) already exposes `dr.mmsi`.
- Produces: `sar_detections.candidate_mmsi` (nullable bigint), non-null only when `match_state == "indeterminate"`. Surfaced in `GET /api/scenes/{id}/detections` responses as `candidate_mmsi` (int or null) — this is what Task 4's frontend work consumes.

- [ ] **Step 1: Add the column to the SQLAlchemy model**

In `backend/app/models.py`, in `class SarDetection`, immediately after the existing `matched_mmsi` line:

```python
    matched_mmsi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Nearest AIS vessel by dead-reckoned position when a detection is
    # indeterminate (neither confidently matched nor ruled dark). NULL for
    # matched (which already has matched_mmsi) and dark (a candidate there
    # would imply a pairing that isn't real).
    candidate_mmsi: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
```

- [ ] **Step 2: Expose the nearest vessel's mmsi from `DARK_MARGINS`**

In `backend/app/fusion.py`, replace the `DARK_MARGINS` query:

```python
DARK_MARGINS = text(
    f"""
    WITH {DR_CTE}
    SELECT d.id AS det_id, x.margin_m
    FROM sar_detections d
    LEFT JOIN LATERAL (
        SELECT ST_Distance(dr.loc, d.location) - dr.envelope_m AS margin_m
        FROM dr
        ORDER BY ST_Distance(dr.loc, d.location) - dr.envelope_m
        LIMIT 1
    ) x ON true
    WHERE d.scene_id = :scene_id AND NOT d.on_land
    """
)
```

with:

```python
DARK_MARGINS = text(
    f"""
    WITH {DR_CTE}
    SELECT d.id AS det_id, x.margin_m, x.mmsi AS candidate_mmsi
    FROM sar_detections d
    LEFT JOIN LATERAL (
        SELECT ST_Distance(dr.loc, d.location) - dr.envelope_m AS margin_m, dr.mmsi
        FROM dr
        ORDER BY ST_Distance(dr.loc, d.location) - dr.envelope_m
        LIMIT 1
    ) x ON true
    WHERE d.scene_id = :scene_id AND NOT d.on_land
    """
)
```

- [ ] **Step 3: Store it on `APPLY_MATCH` and clear it on `RESET_MATCH`**

Replace `APPLY_MATCH`:

```python
APPLY_MATCH = text(
    """
    UPDATE sar_detections SET
        match_state = CAST(:state AS text),
        is_dark = CASE CAST(:state AS text)
                      WHEN 'dark' THEN true WHEN 'matched' THEN false ELSE NULL END,
        matched_mmsi = :mmsi,
        match_distance_m = :distance_m,
        match_time_delta_s = :time_delta_s,
        dark_margin_m = :margin_m,
        candidate_mmsi = :candidate_mmsi
    WHERE id = :det_id
    """
)
```

Replace `RESET_MATCH`:

```python
RESET_MATCH = text(
    """
    UPDATE sar_detections
    SET match_state = NULL, is_dark = NULL, matched_mmsi = NULL,
        match_distance_m = NULL, match_time_delta_s = NULL, dark_margin_m = NULL,
        candidate_mmsi = NULL
    WHERE scene_id = :scene_id
    """
)
```

- [ ] **Step 4: Thread the candidate through `fuse_scene`'s loop**

In `backend/app/fusion.py`, in `fuse_scene`, replace:

```python
    margins = {
        r["det_id"]: r["margin_m"]
        for r in (await session.execute(DARK_MARGINS, dr_params)).mappings()
    }

    chance = await measure_chance_match(
        session, scene_id, sensed_at, settings, detection_count=len(margins)
    )
    # Unmeasurable is not good: with no probes, dark calls are withheld.
    discriminating = chance is not None and chance <= settings.max_chance_match_rate

    for det_id, margin_m in margins.items():
        state = classify(det_id, assigned, margin_m, discriminating)
        match = assigned.get(det_id)
        await session.execute(
            APPLY_MATCH,
            {
                "det_id": det_id,
                "state": state,
                "mmsi": match.mmsi if match else None,
                "distance_m": match.distance_m if match else None,
                "time_delta_s": match.time_delta_s if match else None,
                "margin_m": margin_m,
            },
        )
```

with:

```python
    margins = {
        r["det_id"]: (r["margin_m"], r["candidate_mmsi"])
        for r in (await session.execute(DARK_MARGINS, dr_params)).mappings()
    }

    chance = await measure_chance_match(
        session, scene_id, sensed_at, settings, detection_count=len(margins)
    )
    # Unmeasurable is not good: with no probes, dark calls are withheld.
    discriminating = chance is not None and chance <= settings.max_chance_match_rate

    for det_id, (margin_m, nearest_mmsi) in margins.items():
        state = classify(det_id, assigned, margin_m, discriminating)
        match = assigned.get(det_id)
        await session.execute(
            APPLY_MATCH,
            {
                "det_id": det_id,
                "state": state,
                "mmsi": match.mmsi if match else None,
                "distance_m": match.distance_m if match else None,
                "time_delta_s": match.time_delta_s if match else None,
                "margin_m": margin_m,
                "candidate_mmsi": nearest_mmsi if state == "indeterminate" else None,
            },
        )
```

- [ ] **Step 5: Select the new column in the detections endpoint**

In `backend/app/main.py`, in `DETECTIONS_QUERY`, change:

```python
    SELECT d.id,
           ST_Y(d.location::geometry) AS lat,
           ST_X(d.location::geometry) AS lon,
           d.confidence, d.confidence_bucket, d.is_dark, d.match_state, d.on_land,
           d.matched_mmsi, d.match_distance_m, d.match_time_delta_s, d.dark_margin_m,
           m.ship_name, m.ship_type, m.callsign
```

to:

```python
    SELECT d.id,
           ST_Y(d.location::geometry) AS lat,
           ST_X(d.location::geometry) AS lon,
           d.confidence, d.confidence_bucket, d.is_dark, d.match_state, d.on_land,
           d.matched_mmsi, d.match_distance_m, d.match_time_delta_s, d.dark_margin_m,
           d.candidate_mmsi,
           m.ship_name, m.ship_type, m.callsign
```

(The endpoint returns raw `list[dict]` from the row mapping — no response schema to update elsewhere.)

- [ ] **Step 6: Run the backend test suite**

Run: `cd backend && .venv/bin/pytest -q`
Expected: `200 passed, 2 failed` — the same two pre-existing `test_detect.py` boundary failures noted in Global Constraints, and nothing new. `test_fusion.py` in particular should be unaffected since `classify()`'s signature and behavior are untouched.

- [ ] **Step 7: Manually verify the column exists (best effort)**

This step needs a running DB and will pick up the new column via `create_all` only on a fresh volume:

```bash
docker compose down -v
docker compose up --build
docker compose exec db psql -U dvd -d dvd -c "\d sar_detections"
```

Expected: `candidate_mmsi` listed as a `bigint` column. If a live SAR analysis pass is runnable in this environment (checkpoint + CDSE creds + `ANALYSIS_API_KEY` present), triggering one on a fused ROI and re-running the `\d`/`SELECT` afterward is a stronger check, but is not required to complete this task — the column and SQL wiring are the deliverable, not a live pipeline run.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models.py backend/app/fusion.py backend/app/main.py
git commit -m "Add candidate_mmsi so indeterminate detections expose their nearest AIS vessel"
```

---

### Task 2: Frontend — lift the detections query into `App.tsx` (pure refactor, no behavior change)

**Files:**
- Modify: `frontend/src/types.ts:76-96` (`Detection` type)
- Modify: `frontend/src/App.tsx` (add the lifted query, pass `detections` prop)
- Modify: `frontend/src/components/SceneLayer.tsx` (take `detections` as a prop instead of querying)

**Interfaces:**
- Consumes: `Detection` type from `frontend/src/types.ts`; `Scene` type (unchanged).
- Produces: `SceneLayer` component signature becomes `{ scene: Scene; mode: Roi["mode"]; overlayOpacity: number; detections: Detection[] }` — Task 4 does not touch this signature further. App.tsx's `detections` query (`queryKey: ["detections", selectedScene?.id, selectedScene?.status, showLandMasked]`) is what Task 4 reads to build `matchedMmsis`.

- [ ] **Step 1: Add `candidate_mmsi` to the `Detection` type**

In `frontend/src/types.ts`, in `export type Detection`, add after `match_time_delta_s`:

```typescript
  match_time_delta_s: number | null; // signed age of the AIS fix used
  // Nearest AIS vessel by dead-reckoned position when the detection is
  // indeterminate (neither confidently matched nor ruled dark); null
  // otherwise.
  candidate_mmsi: number | null;
```

- [ ] **Step 2: Move the detections query into `App.tsx`**

In `frontend/src/App.tsx`, change the import line:

```typescript
import type { Health, Roi, Scene } from "./types";
```

to:

```typescript
import type { Detection, Health, Roi, Scene } from "./types";
```

Then, immediately after the existing `const at = selectedScene?.sensed_at ?? null;` line, add:

```typescript
  const detections = useQuery({
    queryKey: ["detections", selectedScene?.id, selectedScene?.status, showLandMasked],
    queryFn: () =>
      apiGet<Detection[]>(
        `/scenes/${selectedScene!.id}/detections${showLandMasked ? "?include_land=true" : ""}`,
      ),
    enabled: selectedScene?.status === "processed",
  });
```

- [ ] **Step 3: Pass `detections` down to `SceneLayer` instead of `showLandMasked`**

In `frontend/src/App.tsx`, change:

```tsx
        {selectedScene && roiObj && (
          <SceneLayer
            scene={selectedScene}
            mode={roiObj.mode}
            overlayOpacity={overlayOpacity}
            showLandMasked={showLandMasked}
          />
        )}
```

to:

```tsx
        {selectedScene && roiObj && (
          <SceneLayer
            scene={selectedScene}
            mode={roiObj.mode}
            overlayOpacity={overlayOpacity}
            detections={detections.data ?? []}
          />
        )}
```

- [ ] **Step 4: Update `SceneLayer` to take `detections` as a prop**

In `frontend/src/components/SceneLayer.tsx`, remove the `useQuery` import and the internal query. Change:

```typescript
import { useQuery } from "@tanstack/react-query";
import { CircleMarker, ImageOverlay, Polygon, Popup } from "react-leaflet";

import { apiGet } from "../api";
import type { Bbox, Detection, Footprint, Roi, Scene } from "../types";
```

to:

```typescript
import { CircleMarker, ImageOverlay, Polygon, Popup } from "react-leaflet";

import type { Bbox, Detection, Footprint, Roi, Scene } from "../types";
```

Change the component signature and body from:

```tsx
export default function SceneLayer({
  scene,
  mode,
  overlayOpacity,
  showLandMasked,
}: {
  scene: Scene;
  mode: Roi["mode"];
  overlayOpacity: number;
  showLandMasked: boolean;
}) {
  const detections = useQuery({
    queryKey: ["detections", scene.id, scene.status, showLandMasked],
    queryFn: () =>
      apiGet<Detection[]>(
        `/scenes/${scene.id}/detections${showLandMasked ? "?include_land=true" : ""}`,
      ),
    enabled: scene.status === "processed",
  });

  return (
```

to:

```tsx
export default function SceneLayer({
  scene,
  mode,
  overlayOpacity,
  detections,
}: {
  scene: Scene;
  mode: Roi["mode"];
  overlayOpacity: number;
  detections: Detection[];
}) {
  return (
```

And change the marker-mapping line from `{(detections.data ?? []).map((d) => (` to `{detections.map((d) => (`.

- [ ] **Step 5: Type-check**

Run: `cd frontend && pnpm exec tsc -b --noEmit`
Expected: no errors. (If `pnpm install` hasn't been run in this checkout, run it first.)

- [ ] **Step 6: Manual verification — no visual regression**

Run `cd frontend && pnpm dev` (proxies `/api` to a running backend on :8000), open the app, select a ROI with existing scenes (e.g. `singapore_strait`), select a processed scene. Confirm detection markers still render with the same colors/popups as before this change — this step is a pure refactor, so nothing should look different yet.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/App.tsx frontend/src/components/SceneLayer.tsx
git commit -m "Lift detections query into App.tsx so VesselLayer can share it"
```

---

### Task 3: Frontend — "hide small vessels" size filter

**Files:**
- Modify: `frontend/src/App.tsx` (add `hideSmallVessels` state + checkbox, pass prop)
- Modify: `frontend/src/components/VesselLayer.tsx` (add filter)

**Interfaces:**
- Consumes: `Vessel.ship_type` (`frontend/src/types.ts`, unchanged).
- Produces: `VesselLayer` gains a `hideSmallVessels: boolean` prop. Task 4 adds the sibling `matchedMmsis` prop to this same signature — that task's diff assumes this task landed first.

- [ ] **Step 1: Add the state and checkbox in `App.tsx`**

In `frontend/src/App.tsx`, immediately after the existing `showLandMasked` state declaration:

```typescript
  // Off by default: masked hits are rocks and shore structures. On, they are the
  // only way to see whether LAND_MASK_BUFFER_M has started eating berthed ships.
  const [showLandMasked, setShowLandMasked] = useState(false);
  // On by default: small hulls (fishing, etc.) are below what 10 m/px SAR
  // resolves, so showing them next to detections reads as missed detections
  // rather than a sensor limit. Only governs the live/no-scene view — once a
  // scene is selected, VesselLayer narrows to matched vessels instead (Task 4).
  const [hideSmallVessels, setHideSmallVessels] = useState(true);
```

Add the checkbox in the JSX, immediately before the `<ScenePanel` element:

```tsx
        <label className="small-vessel-control">
          <input
            type="checkbox"
            checked={hideSmallVessels}
            onChange={(e) => setHideSmallVessels(e.target.checked)}
          />{" "}
          Hide small vessels (fishing, etc.)
        </label>

        <ScenePanel
```

- [ ] **Step 2: Pass the prop to `VesselLayer`**

Change:

```tsx
        <VesselLayer key={roi} roi={roi} at={at} />
```

to:

```tsx
        <VesselLayer key={roi} roi={roi} at={at} hideSmallVessels={hideSmallVessels} />
```

- [ ] **Step 3: Filter in `VesselLayer`**

In `frontend/src/components/VesselLayer.tsx`, add after the imports:

```typescript
// Same resolvable-hull range as LARGE_VESSEL_TYPE_MIN/MAX in
// backend/app/fusion.py: passenger (60-69), cargo (70-79), tanker (80-89) —
// the hulls 10 m/px resolves. Unknown ship_type is treated as small.
const LARGE_VESSEL_TYPE_MIN = 60;
const LARGE_VESSEL_TYPE_MAX = 89;

function isLargeVessel(v: Vessel): boolean {
  return (
    v.ship_type !== null &&
    v.ship_type >= LARGE_VESSEL_TYPE_MIN &&
    v.ship_type <= LARGE_VESSEL_TYPE_MAX
  );
}
```

Change the component signature from:

```tsx
export default function VesselLayer({ roi, at }: { roi: string; at: string | null }) {
```

to:

```tsx
export default function VesselLayer({
  roi,
  at,
  hideSmallVessels,
}: {
  roi: string;
  at: string | null;
  hideSmallVessels: boolean;
}) {
```

Immediately before the `return (` statement, add:

```typescript
  const visible = (vessels.data ?? []).filter((v) => !hideSmallVessels || isLargeVessel(v));
```

Then change `{(vessels.data ?? []).map((v) => (` to `{visible.map((v) => (`.

- [ ] **Step 4: Type-check**

Run: `cd frontend && pnpm exec tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 5: Manual verification**

Run `cd frontend && pnpm dev`, open the app on a live ROI (no scene selected — e.g. `singapore_strait`). Confirm:
- The "Hide small vessels" checkbox is checked by default and small/fishing vessels are not shown.
- Unchecking it brings back all AIS vessels including unknown/small `ship_type`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/VesselLayer.tsx
git commit -m "Add toggle to hide small AIS vessels below SAR's resolvable-hull range"
```

---

### Task 4: Frontend — narrow AIS to matched/candidate vessels during scene view

**Files:**
- Modify: `frontend/src/App.tsx` (compute `matchedMmsis`, pass to `VesselLayer`)
- Modify: `frontend/src/components/VesselLayer.tsx` (accept `matchedMmsis`, combine with size filter)

**Interfaces:**
- Consumes: `detections` query from Task 2 (`detections.data: Detection[] | undefined`), `Detection.matched_mmsi` / `Detection.match_state` / `Detection.candidate_mmsi` from Task 1/2.
- Produces: `VesselLayer`'s final signature: `{ roi: string; at: string | null; hideSmallVessels: boolean; matchedMmsis: Set<number> | null }`.

- [ ] **Step 1: Compute `matchedMmsis` in `App.tsx`**

Immediately after the `detections` query added in Task 2, add:

```typescript
  // null = live view (no scene selected): VesselLayer falls back to the size
  // filter. Non-null = only these vessels pair with a detection in the
  // selected scene, so every visible AIS dot has a visible paired detection.
  const matchedMmsis = selectedScene
    ? new Set(
        (detections.data ?? [])
          .map((d) =>
            d.matched_mmsi ?? (d.match_state === "indeterminate" ? d.candidate_mmsi : null),
          )
          .filter((mmsi): mmsi is number => mmsi !== null),
      )
    : null;
```

- [ ] **Step 2: Pass it to `VesselLayer`**

Change:

```tsx
        <VesselLayer key={roi} roi={roi} at={at} hideSmallVessels={hideSmallVessels} />
```

to:

```tsx
        <VesselLayer
          key={roi}
          roi={roi}
          at={at}
          hideSmallVessels={hideSmallVessels}
          matchedMmsis={matchedMmsis}
        />
```

- [ ] **Step 3: Combine the filters in `VesselLayer`**

In `frontend/src/components/VesselLayer.tsx`, change the component signature from:

```tsx
export default function VesselLayer({
  roi,
  at,
  hideSmallVessels,
}: {
  roi: string;
  at: string | null;
  hideSmallVessels: boolean;
}) {
```

to:

```tsx
export default function VesselLayer({
  roi,
  at,
  hideSmallVessels,
  matchedMmsis,
}: {
  roi: string;
  at: string | null;
  hideSmallVessels: boolean;
  // Non-null once a scene is selected: takes over from hideSmallVessels so
  // every visible AIS marker pairs with a visible SAR detection.
  matchedMmsis: Set<number> | null;
}) {
```

Change the `visible` filter from:

```typescript
  const visible = (vessels.data ?? []).filter((v) => !hideSmallVessels || isLargeVessel(v));
```

to:

```typescript
  const visible = (vessels.data ?? []).filter((v) =>
    matchedMmsis ? matchedMmsis.has(v.mmsi) : !hideSmallVessels || isLargeVessel(v),
  );
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && pnpm exec tsc -b --noEmit`
Expected: no errors.

- [ ] **Step 5: Manual verification**

Run `cd frontend && pnpm dev`, open the app on `singapore_strait`, select a processed scene with at least one matched detection. Confirm:
- Only AIS vessels paired to a matched (or, if present, indeterminate-candidate) detection are shown — no unrelated AIS markers scattered across the ROI.
- Every green "AIS match" detection marker has a nearby blue AIS marker; dark (red) detections have no AIS marker anywhere near them.
- Toggling "Hide small vessels" while the scene is selected has no visible effect (filters don't stack, per design).
- Deselecting the scene (clicking "Live") brings back the full live AIS view, governed again by the size toggle.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/VesselLayer.tsx
git commit -m "Narrow AIS layer to matched/candidate vessels when a scene is selected"
```
