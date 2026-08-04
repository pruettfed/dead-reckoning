import { describe, expect, it } from "vitest";

import { STATE_LABEL, contactId, contactMmsi, contactState } from "./contactState";
import type { Detection } from "./types";

function det(over: Partial<Detection> = {}): Detection {
  return {
    id: 42, lat: 25.1, lon: 121.8, confidence: 0.9, confidence_bucket: "high",
    match_state: "matched", is_dark: false, on_land: false, matched_mmsi: 416001,
    match_distance_m: 120, match_time_delta_s: -90, candidate_mmsi: null,
    dark_margin_m: null, ship_name: "X", candidate_name: null, ship_type: 70, callsign: null,
    flag_iso2: "TW", flag_country: "Taiwan", ...over,
  };
}

describe("contactState", () => {
  it("passes through the fused match states", () => {
    expect(contactState(det({ match_state: "matched" }), "fused")).toBe("matched");
    expect(contactState(det({ match_state: "dark" }), "fused")).toBe("dark");
    expect(contactState(det({ match_state: "indeterminate" }), "fused")).toBe("indeterminate");
  });

  it("calls survey detections contacts, never dark", () => {
    expect(contactState(det({ match_state: null }), "survey")).toBe("contact");
    expect(contactState(det({ match_state: "dark" }), "survey")).toBe("contact");
  });

  it("treats an unfused detection in a fused roi as a contact", () => {
    expect(contactState(det({ match_state: null }), "fused")).toBe("contact");
  });

  it("lets the land mask win over every other state", () => {
    expect(contactState(det({ on_land: true, match_state: "dark" }), "fused")).toBe("masked");
    expect(contactState(det({ on_land: true }), "survey")).toBe("masked");
  });
});

describe("STATE_LABEL", () => {
  it("never labels anything in a survey region dark", () => {
    expect(STATE_LABEL.contact).toBe("CONTACT");
    expect(STATE_LABEL.dark).toBe("DARK");
    expect(STATE_LABEL.matched).toBe("AIS MATCH");
    expect(STATE_LABEL.indeterminate).toBe("UNRESOLVED");
  });
});

describe("contactId", () => {
  it("prefixes by roi mode", () => {
    expect(contactId(det({ id: 7 }), "fused")).toBe("DR-7");
    expect(contactId(det({ id: 7 }), "survey")).toBe("SC-7");
  });
});

describe("contactMmsi", () => {
  it("prefers the assigned match, then the candidate", () => {
    expect(contactMmsi(det({ matched_mmsi: 1, candidate_mmsi: 2 }))).toBe(1);
    expect(contactMmsi(det({ matched_mmsi: null, candidate_mmsi: 2 }))).toBe(2);
  });

  it("is null for a dark contact, which has no transponder identity", () => {
    expect(contactMmsi(det({ matched_mmsi: null, candidate_mmsi: null }))).toBeNull();
  });
});
