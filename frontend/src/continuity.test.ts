import { describe, expect, it } from "vitest";

import { CONTINUITY_BINS, CONTINUITY_WINDOW_MS, continuity, formatGap } from "./continuity";

const NOW = Date.UTC(2026, 7, 2, 12, 0, 0);
const BIN_MS = CONTINUITY_WINDOW_MS / CONTINUITY_BINS;
const iso = (msBeforeNow: number) => new Date(NOW - msBeforeNow).toISOString();

describe("continuity", () => {
  it("returns an all-dark strip with no fixes", () => {
    const { bins, gapMs } = continuity([], NOW);
    expect(bins).toHaveLength(CONTINUITY_BINS);
    expect(bins.every((b) => !b)).toBe(true);
    expect(gapMs).toBe(CONTINUITY_WINDOW_MS);
  });

  it("lights every bin when fixes are dense and reports no gap", () => {
    const times = Array.from({ length: CONTINUITY_BINS }, (_, i) => iso(i * BIN_MS + BIN_MS / 2));
    const { bins, gapMs } = continuity(times, NOW);
    expect(bins.every((b) => b)).toBe(true);
    expect(gapMs).toBe(0);
  });

  it("measures the longest run of empty bins, not the total", () => {
    // Fixes in every bin except a 4-bin run and a separate 2-bin run.
    const skip = new Set([5, 6, 7, 8, 20, 21]);
    const times = Array.from({ length: CONTINUITY_BINS }, (_, i) => i)
      .filter((i) => !skip.has(i))
      .map((i) => iso((CONTINUITY_BINS - 1 - i) * BIN_MS + BIN_MS / 2));
    const { gapMs } = continuity(times, NOW);
    expect(gapMs).toBe(4 * BIN_MS);
  });

  it("ignores fixes older than the window", () => {
    const { bins } = continuity([iso(CONTINUITY_WINDOW_MS + 60_000)], NOW);
    expect(bins.every((b) => !b)).toBe(true);
  });
});

describe("formatGap", () => {
  it("renders hours and minutes", () => {
    expect(formatGap(6 * 3600_000 + 12 * 60_000)).toBe("6H12M");
  });

  it("renders minutes alone under an hour", () => {
    expect(formatGap(45 * 60_000)).toBe("45M");
  });
});
