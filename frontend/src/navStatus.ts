// ITU-R M.1371 navigational status (AIS message types 1/2/3 field). Codes
// 9-13 are reserved/special-craft, 14 is AIS-SART/MOB/EPIRB, 15 is the
// transponder's own "not available" — none of those are a meaningful status
// to show a user, so they fall through to null same as an absent value.
const NAV_STATUS_LABELS: Record<number, string> = {
  0: "under way using engine",
  1: "at anchor",
  2: "not under command",
  3: "restricted manoeuvrability",
  4: "constrained by draught",
  5: "moored",
  6: "aground",
  7: "engaged in fishing",
  8: "under way sailing",
};

export function navStatusLabel(code: number | null): string | null {
  if (code === null) return null;
  return NAV_STATUS_LABELS[code] ?? null;
}
