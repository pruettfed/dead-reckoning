import { describe, expect, it } from "vitest";

import { shipTypeLabel } from "./shipType";

describe("shipTypeLabel", () => {
  it("maps the resolvable-hull ranges fusion cares about", () => {
    expect(shipTypeLabel(60)).toBe("Passenger");
    expect(shipTypeLabel(70)).toBe("Cargo");
    expect(shipTypeLabel(89)).toBe("Tanker");
  });

  it("maps the small-craft codes", () => {
    expect(shipTypeLabel(30)).toBe("Fishing");
    expect(shipTypeLabel(37)).toBe("Pleasure craft");
    expect(shipTypeLabel(45)).toBe("High-speed craft");
  });

  it("returns null for absent or unassigned codes", () => {
    expect(shipTypeLabel(null)).toBeNull();
    expect(shipTypeLabel(0)).toBeNull();
    expect(shipTypeLabel(19)).toBeNull();
  });
});
