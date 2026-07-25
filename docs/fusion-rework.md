# Fusion rework — what was wrong, what was fixed, what still isn't

Audit of the `north_taiwan` scene of 2026-07-24 21:51Z (8 detections). All numbers
below are measured on that scene. Re-measure anything here with
`scripts/refuse.py` — it re-runs fusion over stored detections at **0 PU**.

## Fixed

**1. The matcher could not discriminate.** 1,578 probes on *empty water* in the
same traffic as the real detections, pushed through the old 500 m / ±2 h gate:
**51.6% came back "matched"**. Observed match rate was 6/8 = 75%, so the output
was statistically indistinguishable from noise (binomial p ≈ 0.17). In a traffic
lane every point has *some* vessel passing within 500 m within ±2 h — the gate
tested "is this on a shipping lane", not "is this that ship".
→ AIS is now dead-reckoned to the acquisition instant and matched against a
physical budget. Same probe now scores **1.9–3.0%**.

**2. Matches were misattributed.** Two of six matches were the *wrong vessel* —
the 65-min and 49-min offsets were stale pings from other ships. MMSI 416042000
was assigned to two detections 4.2 km apart; there was no one-to-one constraint.
→ `assign_one_to_one` (greedy, nearest pair first, mutually exclusive). Det 115
now correctly resolves to *ASIA CEMENT NO.9*, not 416042000.

**3. The match radius was smaller than AIS staleness.** Measured cadence: median
168 s between fixes (p90 536 s, max 5,038 s) = **864 m of travel at 10 kn**,
against a 500 m radius. Over half of lawfully broadcasting vessels could not
match. Not tunable — widening the radius worsens (1).
→ `ST_Project` along `sog`/`cog` to the acquisition instant. Match distances
collapsed from a median 230 m to **92 m** (392→4 m, 99→19 m, 306→78 m).

**4. Dark/matched was a binary that hid its own uncertainty.** Added a third
state and a physical uncertainty budget per candidate:

    gate     = MATCH_RADIUS_M + SAR_AZIMUTH_SHIFT_S x speed
    envelope = gate + DR_COURSE_ERR_FRAC x speed x fix age

`SAR_AZIMUTH_SHIFT_S` (90 s ≈ slant range / platform velocity for S1 IW) is
physics, not slack: a moving target images displaced along-azimuth by up to
~450 m at 10 kn. **This was silently manufacturing dark vessels.** Detections
inside an envelope are now `indeterminate`, not `dark`; dark calls carry
`dark_margin_m` (metres clear of every vessel's envelope). The two darks here
score 942 m and 2,488 m — falsifiable claims, where before they were coin flips.

**5. Nothing was measured.** Every fused scene now stores `chance_match_rate`
(its own false-match rate on empty water) and `recall_large_*`. Above
`MAX_CHANCE_MATCH_RATE` (10%) **all dark calls are withheld as indeterminate** —
a number nobody can distinguish from noise is not published. Recall is scored
only against AIS ship types 60–89 (cargo/tanker/passenger); at 10 m/px a fishing
boat is under the sensor, so counting it would measure Sentinel-1, not the model.

**Result on the audited scene:** 6 matched, 2 dark, 0 indeterminate,
false-match rate 1.9%, large-vessel recall 2/2.

## Verified

- 18 new unit tests on assignment + classification; **200 pass** overall.
- Publish gate exercised end-to-end (`MAX_CHANCE_MATCH_RATE=0.005` → 2 dark
  become 2 indeterminate, dark count drops to 0).
- Schema migration is **additive and idempotent** (`fusion.apply_schema`, same
  pattern as `landmask`). Deliberately *not* `down -v`: that would discard the
  scene and overview you already paid PU for. Verified 2 scenes / 39 detections /
  2 overviews survived.
- UI checked in the browser, not assumed: dark popups render the margin, match
  popups render "N m from its dead-reckoned position, from a fix N min away",
  scene list renders the false-match rate.
- Survey ROIs untouched — `musandam_stage` still 31 detections, all state NULL.

## Outstanding — read this before shipping

1. **Detector precision is still completely unmeasured, and it is now the
   single largest risk.** `chance_match_rate` validates the *matcher*, not the
   *detector*. A YOLO false positive on open-water clutter will be labelled DARK
   with a large, confident margin — the new machinery makes such an error look
   *more* credible, not less. Both current darks are real, but I confirmed that
   by cropping the SAR chips by hand; no code checks it. **Hand-label 2–3 scenes
   and record precision before anyone acts on a dark call.**
2. **n = 1 scene, 8 detections, recall denominator of 2.** Every figure here is
   a sample of one. Nothing about reliability is established until ~5–10 scenes
   across several ROIs have run.
3. **Azimuth displacement is modelled as an isotropic disc.** The real effect is
   directional; without the look vector the full term is carried in every
   direction. Conservative (it can only suppress dark calls, never invent them)
   but it inflates the gate to ~660 m at 10 kn, which is most of the residual
   2–3% false-match rate. Using the S1 heading/look geometry would tighten it.
4. **Vessels with no `ship_metadata` are excluded from recall**, not assumed
   small. Real recall is likely worse than 2/2 suggests.
5. **Greedy assignment is not globally optimal.** Fine at tens of detections;
   revisit if a scene ever produces hundreds.
6. **MMSI is not identity.** AIS is trivially spoofed. A vessel broadcasting a
   false position that happens to land near a detection still matches, and this
   pipeline cannot tell. "Matched" means "consistent with a broadcast", nothing more.
7. **The land mask has never actually fired in production** (0 detections on land
   across both scenes). Untested path.
8. **AIS ingest health is not gated per scene** — only buffer depth is checked.
   Ingest was continuous here (778 min, no gaps), but a dropout during a future
   acquisition would read as dark vessels.
9. **Pre-existing, not mine:** `tests/test_detect.py::TestBucketConfidence` has 2
   failures on clean `HEAD` — `CONF_HIGH`/`CONF_MEDIUM` were retuned to 0.6/0.25
   without updating the test. Decide which is canonical.
