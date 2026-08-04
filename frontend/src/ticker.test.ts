import { describe, expect, it } from "vitest";

import { buildTicker } from "./ticker";
import type { Schedule } from "./types";

const schedule = {
  regions: [
    { name: "north_taiwan", label: "North Taiwan", mode: "fused", latest_scene_sensed_at: "2026-08-01T22:14:00Z", next_expected_at: null, last_processed_at: null, state: "analyzing" },
    { name: "kerch_strait", label: "Kerch Strait", mode: "survey", latest_scene_sensed_at: "2026-08-01T03:10:00Z", next_expected_at: null, last_processed_at: null, state: "awaiting_publication" },
    { name: "kharg_island", label: "Kharg Island", mode: "survey", latest_scene_sensed_at: null, next_expected_at: null, last_processed_at: null, state: "scheduled" },
  ],
  most_recent: {
    roi: "north_taiwan", label: "North Taiwan", mode: "fused",
    sensed_at: "2026-08-01T22:14:00Z", processed_at: "2026-08-02T01:22:00Z",
    detection_count: 12, dark_count: 2,
  },
  month_to_date_pu: 900,
  pu_monthly_ceiling: 24000,
} as Schedule;

describe("buildTicker", () => {
  it("leads with the most recent analysis across all regions", () => {
    const items = buildTicker(schedule);
    expect(items[0].text).toBe("North Taiwan analysis complete · 12 contacts · 2 dark");
    expect(items[0].time).toBe("01:22");
  });

  it("reports in-flight and pending regions", () => {
    const items = buildTicker(schedule);
    expect(items.some((i) => i.text === "North Taiwan · analyzing pass")).toBe(true);
    expect(items.some((i) => i.text === "Kerch Strait · awaiting product publication")).toBe(true);
    expect(items.some((i) => i.text === "1/3 regions scheduled for next pass")).toBe(true);
  });

  it("never says dark for a survey region's analysis", () => {
    const survey = { ...schedule, most_recent: { ...schedule.most_recent!, mode: "survey" as const, label: "Kharg Island" } };
    expect(buildTicker(survey)[0].text).toBe("Kharg Island analysis complete · 12 contacts");
  });

  it("returns a standby item before the schedule loads", () => {
    expect(buildTicker(undefined)).toHaveLength(1);
  });
});
