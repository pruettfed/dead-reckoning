import { describe, expect, it } from "vitest";

import { buildTicker } from "./ticker";
import type { Detection, Scene } from "./types";

const scene = {
  id: "s1", name: "S1A_X", roi: "north_taiwan", sensed_at: "2026-08-01T22:14:00Z",
  platform: "S1A", status: "processed", processed_at: "2026-08-02T01:22:00Z", error: null,
  footprint: { type: "Polygon", coordinates: [] }, imaged_bbox: null, has_overview: true,
  detection_count: 2, dark_count: 1, indeterminate_count: 0, land_count: 0,
  chance_match_rate: 0.04, recall_large_total: 3, recall_large_detected: 3,
} as unknown as Scene;

const dark = {
  id: 5, confidence: 0.9, match_state: "dark", is_dark: true, on_land: false,
  dark_margin_m: 640, matched_mmsi: null, candidate_mmsi: null, ship_name: null,
} as unknown as Detection;

describe("buildTicker", () => {
  it("leads with dark calls and their falsifiable margin", () => {
    const items = buildTicker(scene, [dark], "North Taiwan", "fused");
    expect(items[0].text).toBe("DR-5 flagged dark · Δ 640 m");
    expect(items[0].time).toBe("22:14");
  });

  it("summarises the scene and names the region", () => {
    const items = buildTicker(scene, [dark], "North Taiwan", "fused");
    expect(items.some((i) => i.text === "Scene analysis complete · 2 contacts")).toBe(true);
    expect(items.some((i) => i.text.startsWith("North Taiwan pass ingested"))).toBe(true);
  });

  it("never says dark in a survey region", () => {
    const items = buildTicker(scene, [dark], "Kharg Island", "survey");
    expect(items.every((i) => !i.text.includes("dark"))).toBe(true);
  });

  it("returns a standby item with no scene", () => {
    expect(buildTicker(null, [], "North Taiwan", "fused")).toHaveLength(1);
  });
});
