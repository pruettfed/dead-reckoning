import type { Detection, Roi } from "./types";

export type ContactState =
  | "dark"
  | "matched"
  | "indeterminate"
  | "contact"
  | "masked"
  | "ais";

export const STATE_LABEL: Record<ContactState, string> = {
  dark: "DARK",
  matched: "AIS MATCH",
  indeterminate: "UNRESOLVED",
  contact: "CONTACT",
  masked: "MASKED",
  ais: "AIS",
};

// Survey regions have no AIS to correlate against, so nothing in one is ever
// dark. Land-masked hits are not vessels at all and outrank every other state.
export function contactState(d: Detection, mode: Roi["mode"]): ContactState {
  if (d.on_land) return "masked";
  if (mode === "survey" || d.match_state === null) return "contact";
  return d.match_state;
}

export function contactId(d: Detection, mode: Roi["mode"]): string {
  return `${mode === "survey" ? "SC" : "DR"}-${d.id}`;
}

export function contactMmsi(d: Detection): number | null {
  return d.matched_mmsi ?? d.candidate_mmsi;
}
