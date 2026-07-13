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
| Fujairah / Gulf of Oman | 0 | ❌ dropped (was `strait_of_hormuz`) |
| Taiwan Strait mid-channel | 0–1 | ❌ dropped (was `taiwan_strait`) |
| Spratly Islands | 0 | ❌ dropped (open ocean — terrestrial AIS will never cover it) |
| NE Black Sea (Kerch) | 0 | ❌ dropped |
| UAE Gulf side (Dubai) | 0 | ❌ (the aihare map shows cached vessels here; raw feed is silent) |
| Vladivostok / Peter the Great Gulf | 0 | ❌ |
| Laconian Gulf, Chios, Kaohsiung | 0 | ❌ |

**Boxes must not be shrunk without re-probing.** A first attempt trimmed each
box toward open water (land clutter is bad for SAR detection) and coverage
collapsed — north_taiwan 24→1, Skagen 82→17, Malta 5→0 — because both the
receivers and the traffic lanes hug the coasts. The registry keeps the exact
boxes that probed hot and accepts coastal land in the SAR chips as the cost.

## Why each selected ROI is a dark-vessel story

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
- **Singapore Strait** — densest coverage; the always-works demo region and STS
  hub in its own right.

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
