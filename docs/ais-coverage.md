# AIS coverage: constraints, verified ROIs, and alternatives

AISStream (our AIS source) relays **terrestrial** receivers — hobbyist and port
antennas with ~40–60 nm range. Coverage exists where someone runs a receiver,
which is emphatically *not* where ships go dark: open ocean, contested straits,
and sanctioned coastlines are mostly silent. This document records the live
verification that drove the ROI registry, so future ROI changes start from data
instead of hope.

## Verification method

One WebSocket subscription with all candidate boxes, binning incoming
`PositionReport`s per box for 100 s (run inside the backend container, which has
the API key):

```python
sub = {"APIKey": KEY,
       "BoundingBoxes": [[[min_lat, min_lon], [max_lat, max_lon]], ...],
       "FilterMessageTypes": ["PositionReport"]}
```

A worldwide box (`[[-90,-180],[90,180]]`) streams instantly, so silence in a
candidate box means *no receivers there*, not a broken subscription. Cross-check
visually at https://ships.aihare.in/live (same feed; note its wide-zoom view
renders a cached fleet, so zoom in before trusting it).

## Probe results (2026-07-11/12, 100 s windows, final registry boxes)

| Region | Vessels | Verdict |
|---|---|---|
| Skagen / Kattegat | **75** | ✅ ROI `skagen_kattegat` |
| Bosphorus / Marmara | **45** | ✅ ROI `bosphorus_marmara` |
| Gulf of Finland | **39** | ✅ ROI `gulf_of_finland` |
| Singapore Strait | **29** | ✅ ROI `singapore_strait` (demo default) |
| North Taiwan / ECS | **20** | ✅ ROI `north_taiwan` |
| Malta / Hurd Bank | **3–5** | ✅ ROI `malta_hurds_bank` (thin but alive; strongest dark-STS narrative) |
| Cyprus east (Limassol) | 12 | viable, not selected |
| Rotterdam | dense | viable, weak dark-vessel narrative |
| Fujairah / Gulf of Oman | 0 | ↻ now `fujairah_anchorage` (survey mode) |
| Taiwan Strait mid-channel | 0–1 | ❌ dropped (was `taiwan_strait`) |
| Spratly Islands | 0 | ❌ dropped (open ocean — terrestrial AIS will never cover it) |
| NE Black Sea (Kerch) | 0 | ↻ now `kerch_strait` (survey mode) |
| UAE Gulf side (Dubai) | 0 | ❌ (the aihare map shows cached vessels here; raw feed is silent) |
| Vladivostok / Peter the Great Gulf | 0 | ❌ |
| Laconian Gulf, Chios, Kaohsiung | 0 | ❌ |

**AIS boxes must not be shrunk without re-probing.** A first attempt trimmed each
box toward open water (land clutter is bad for SAR detection) and coverage
collapsed — north_taiwan 24→1, Skagen 82→17, Malta 5→0 — because both the
receivers and the traffic lanes hug the coasts.

This is why an ROI carries **two** boxes (`rois.py`). `ais_bbox` is exactly the
box that probed hot above; it is free, so it stays wide and coastal. `sar_bbox`
is a smaller water-centered subset used for the pixel fetch, detection, and
fusion clip — it is what costs PU, and shrinking it cut the registry from
~51,800 to 23,713 PU/month. The two constraints stopped fighting once they
stopped sharing a field.

## Fused vs survey regions

The 409 guard (below) makes an AIS-silent region unanalyzable, which would rule
out exactly the places vessels go dark. Regions therefore declare a `mode`:

| | `fused` | `survey` |
|---|---|---|
| AIS coverage | verified live | none |
| Fusion | `ST_DWithin` match | skipped |
| `is_dark` | true / false | **NULL** |
| UI | red dark / green matched | amber "observed vessel" + banner |
| Claim | "this vessel is running dark" | "this many vessels were here at 06:12Z" |

Survey regions still subscribe an `ais_bbox`, because AIS costs nothing and a
region should be promoted on evidence rather than assumption. `eopl_tompok_utara`
is the likeliest candidate — its box reaches west toward the Singapore receivers
that feed `singapore_strait`.

A survey region is not a weaker fused region. When ~57% of Strait of Hormuz
transits ran dark through 2026, a per-pass count of what radar actually sees is
the product; there is no AIS baseline to subtract, and pretending otherwise
would be the dishonest version.

## AIS coverage must be spatially uniform, not just present (found 2026-07-26)

The verification method above only ever checked *aggregate* vessel count in
a candidate box — "does AIS exist somewhere in here" — which is not the same
question as "is a `fused` verdict honest everywhere in this box." A live
review of `bosphorus_marmara` found detections reading "dark" almost
entirely on the east side of the box. Binning AIS positions into 5 buckets
along each axis showed why: longitude density collapsed from 195 → 271 →
47 → 3 vessels moving west→east across the box, while latitude bins stayed
smooth (66/117/140/144/127) — a real Istanbul-receiver range cliff, not
noise. The `sar_bbox` extended to 29.45°E, well past the ~28.78°E cliff, so
any detection in the eastern two-thirds of the box was **structurally
guaranteed** to read dark regardless of whether it actually carried AIS —
there was no receiver there to catch it either way.

The `MAX_CHANCE_MATCH_RATE` gate (`docs/fusion-rework.md`) does not catch
this failure mode: it measures the odds of a *coincidental* match on empty
water, which goes *down* as AIS density drops, so a receiver dead zone
sails straight through it.

**Fix:** `bosphorus_marmara`'s `sar_bbox` was shrunk to
`(28.45, 40.72, 28.85, 41.00)` — inside the real coverage boundary. Still
100% SAR coverage / 15 usable passes/mo, and cheaper as a side effect
(930 → ~400 PU/mo) since the imaged area shrank.

**New required check** for any `fused` candidate box, alongside the
aggregate-count probe above — bin AIS positions along both axes and look
for a bucket that collapses to near-zero while its neighbors don't:

```sql
with pts as (
  select width_bucket(ST_X(location::geometry), min_lon, max_lon, 5) as xb,
         width_bucket(ST_Y(location::geometry), min_lat, max_lat, 5) as yb,
         mmsi
  from ais_positions
  where location && ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
)
select xb, count(distinct mmsi) from pts group by xb order by xb;  -- then yb
```

All 5 other fused regions, and every enlarged 2026-07-26 candidate box, were
checked with this method — gentle gradients only, no comparable cliff.

## SAR coverage constrains regions too (measured 2026-07-21)

AIS is not the only thing that can be absent, and the SAR failure mode is
quieter: the catalog happily reports a pass whose swath merely clips the corner
of the box. Fetching it costs full PU and returns an all-black chip with zero
detections. The first live Hormuz analysis did exactly this — **0.1% of the
`sar_bbox` covered, 2 KB of black PNG, 49 PU spent**.

So "a pass exists" is not "the box is imaged". Two numbers per region:

- **passes** — acquisitions whose swath touches `sar_bbox` at all.
- **usable passes** — those whose mosaicked footprint covers ≥ 85% of it.

Only usable passes are budgeted for, and `find_target_scene` refuses the rest
before spending. Roughly half of all passes fail the check even for well-placed
boxes (Hormuz 10/20, Singapore 6/9).

Coverage is the union of slices in the Process API's `[-1 min, +10 min]`
mosaicking window, not the anchor slice alone — `PROCESS_WINDOW_BACK`/`FWD` in
`sar.py` are shared with `pipeline.footprint_coverage` so the two cannot drift.

**Placement beats size.** Moving a box inside a real swath track is worth far
more than shrinking it:

| Region | Before | After | PU/pass |
|---|---|---|---|
| `north_taiwan` | 3/11 usable, 67% median | 11/11, 100% | 179 → 65 |
| `fujairah_anchorage` | 8/20, 32% | 14/20, 100% | 51 → 18 |
| `somali_coast` | 4/13, 26% | 9/13, 93% | 103 → 37 |
| `gulf_of_finland` | 14/41, 55% | 19/33, 85% | 222 → 107 |

**Some regions cannot be fixed.** Sentinel-1 runs IW over land and coastal water
only:

- A Somali Basin box (89×89 km, open ocean) returned **0 passes in 30 days**.
- `gulf_of_aden_irtc` covered a **median 3%** with 3/11 usable, and no resize
  helped — the IRTC corridor is open water. Dropped 2026-07-21. The nearest
  workable placement was the Berbera coast, which is a different subject.

Price any candidate with `backend/scripts/probe_regions.py` (free, 0 PU) before
adding it. It reuses the pipeline's own coverage query, flags `passes_per_month`
values that have drifted, and warns on regions with too few usable passes.

## Why each selected ROI is a dark-vessel story

The canonical, per-region version of this narrative now lives in the `blurb`
field on each `ROI` in `backend/app/rois.py` (exposed via `GET /api/rois`) —
covers all 14 regions, fused and survey. Summary:

- **Gulf of Finland** — every Russian shadow-fleet tanker loading at
  Primorsk/Ust-Luga transits this corridor; documented AIS manipulation.
- **Skagen / Kattegat** — the mandatory Baltic exit chokepoint for that same
  fleet; Danish authorities actively monitor it.
- **Bosphorus / Marmara** — the transit queue for Russian oil and grain leaving
  the Black Sea; the reachable proxy for Kerch (which itself has no coverage).
- **Malta / Hurd Bank** — a documented hotspot for dark ship-to-ship transfers
  of sanctioned crude in the central Mediterranean.
- **North Taiwan / ECS** — gray-zone activity north of Taiwan, including the
  subsea-cable interference incidents off Keelung; AIS spoofing documented.
- **Singapore Strait** — densest coverage; the always-works demo region. Its
  story is weaker-in-kind than the Gulf regions — not a conflict zone, but a
  documented IMB piracy/armed-robbery hotspot and a waypoint for sanctioned
  Iran/Venezuela crude STS transfers nearby. Kept as the demo region with
  that framing stated honestly rather than oversold.
- **Strait of Hormuz / Gulf of Oman** (`hormuz_strait` + `fujairah_anchorage`
  + `musandam_stage` + `kharg_island`) — presented as one story across four
  boxes rather than merged into one, because a literal merge was tested and
  rejected (median SAR coverage collapses to 36–58% across the combined
  extent — the sub-areas sit on different swath geometry). `hormuz_strait`
  is the transit chokepoint itself, `fujairah_anchorage` is where tankers
  wait or transfer cargo before/after transiting, `kharg_island` is the
  upstream export terminal, and `musandam_stage` is the queuing area on the
  peninsula that pinches the strait.

## Alternative AIS sources considered

| Source | Cost | Coverage | Fit |
|---|---|---|---|
| **AISStream** (current) | free | terrestrial, patchy | Fine once ROIs are chosen around coverage — that's what this doc enforces. |
| **Global Fishing Watch API** | free (research, registration) | global AIS (satellite+terrestrial), ~72 h delay | Best free complement: publishes **AIS gap events** ("likely disabling") and its own **Sentinel-1 SAR detections** — ideal for validating our fusion output. Delay rules it out for the live layer. |
| Satellite AIS (Spire, Kpler/exactEarth, Unseenlabs) | $$$ commercial | global, near-real-time | The real fix for gray zones (would make Hormuz/Spratlys viable). Not portfolio-budget. |
| MarineTraffic / similar APIs | $$ credits | global (mixed sat/terrestrial) | Paid; same class as above at lower fidelity. |
| AISHub | free *if you operate a receiver* | terrestrial exchange | No receiver, no data. |
| Danish Maritime Authority (`aisdk` dumps) | free | Denmark, historical CSV | Retrospective Skagen studies; not live. |
| Norwegian Coastal Administration | free, open | Norwegian waters, live | Region-locked; no dark-vessel narrative fit. |

## How analysis stays honest under these constraints

1. **ROIs are chosen where narrative ∩ coverage ≠ ∅** (this doc).
2. **Per-ROI coverage guard** — `POST /api/analysis/{roi}` refuses (409) when
   the ROI's AIS buffer is empty or doesn't bracket the scene's ±2 h window;
   otherwise every detection would be falsely dark. Checked before any PU spend.
3. **Timing** — a scene is only analyzable while the AIS buffer brackets it
   (retention window), and within the 3-day catalog search window. Practically:
   run analysis within ~2 days of a pass, with ingestion running beforehand.
4. **Future enrichment (not built)** — cross-validate flagged dark vessels
   against Global Fishing Watch gap events / SAR detections; a satellite-AIS
   backend behind the same ingest interface would unlock true gray-zone ROIs.
