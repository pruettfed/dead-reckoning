import { describe, expect, it } from "vitest";

import { C, hexA, selectable, stateColor } from "./theme";

describe("hexA", () => {
  it("converts a hex to rgba at the given alpha", () => {
    expect(hexA("#ff3b30", 0.5)).toBe("rgba(255,59,48,0.5)");
  });

  it("handles pure black and white", () => {
    expect(hexA("#000000", 1)).toBe("rgba(0,0,0,1)");
    expect(hexA("#ffffff", 0)).toBe("rgba(255,255,255,0)");
  });
});

describe("stateColor", () => {
  it("maps every contact state to its palette entry", () => {
    expect(stateColor("dark")).toBe(C.dark);
    expect(stateColor("matched")).toBe(C.match);
    expect(stateColor("indeterminate")).toBe(C.unres);
    expect(stateColor("contact")).toBe(C.survey);
    expect(stateColor("masked")).toBe(C.masked);
    expect(stateColor("ais")).toBe(C.accent);
  });
});

describe("selectable", () => {
  it("tints background and border with the accent when on", () => {
    const on = selectable(C.dark, true);
    expect(on.background).toBe(hexA(C.dark, 0.14));
    expect(on.borderColor).toBe(hexA(C.dark, 0.55));
    expect(on.color).toBe(C.textHi);
  });

  it("falls back to neutral chrome when off", () => {
    const off = selectable(C.dark, false);
    expect(off.background).toBe(C.fill);
    expect(off.borderColor).toBe(C.line);
    expect(off.color).toBe(C.textMid);
  });
});
