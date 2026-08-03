export const C = {
  bg: "#0a0b0c",
  panel: "#0d0f11",
  panelAlt: "#0b0d0f",
  chrome: "#101214",
  map: "#06080a",
  brand: "#d8dee1",

  accent: "#3a8dff",
  amber: "#e8b400",
  dark: "#ff3b30",
  match: "#4fd35e",
  unres: "#ffb020",
  survey: "#5fc8d8",
  masked: "#7c3aed",

  textHi: "#eef4f6",
  text: "#dbe5e9",
  textMid: "#a9b7bd",
  textDim: "#8b979d",
  textSubtle: "#c9d5da",
  faint: "#5a666c",
  label: "#4e5a60",
  countDim: "#525e64",

  line: "rgba(255,255,255,.07)",
  lineStrong: "rgba(255,255,255,.12)",
  chromeLine: "rgba(255,255,255,.08)",
  hairline: "rgba(255,255,255,.05)",
  fill: "rgba(255,255,255,.025)",
  glass: "rgba(12,14,16,.74)",
} as const;

export const MONO = "ui-monospace,'SF Mono',Menlo,monospace";
export const SANS = "ui-sans-serif,system-ui,'Helvetica Neue',sans-serif";

export function hexA(hex: string, a: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

const STATE_COLORS: Record<string, string> = {
  dark: C.dark,
  matched: C.match,
  indeterminate: C.unres,
  contact: C.survey,
  masked: C.masked,
  ais: C.accent,
};

export function stateColor(state: string): string {
  return STATE_COLORS[state] ?? C.accent;
}

// The one surface formula every selectable element in the design shares.
export function selectable(accent: string, on: boolean) {
  return on
    ? { background: hexA(accent, 0.14), borderColor: hexA(accent, 0.55), color: C.textHi }
    : { background: C.fill, borderColor: C.line, color: C.textMid };
}

export function applyTheme(): void {
  const root = document.documentElement.style;
  for (const [k, v] of Object.entries(C)) root.setProperty(`--dr-${k}`, v);
  root.setProperty("--dr-mono", MONO);
  root.setProperty("--dr-sans", SANS);
}
