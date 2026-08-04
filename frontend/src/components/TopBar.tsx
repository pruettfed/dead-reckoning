import { formatAgo, utcStamp } from "../countdown";
import { C, MONO, hexA } from "../theme";
import { Tabs } from "./ui";
import type { Health } from "../types";

type Props = {
  mode: "fused" | "survey";
  onMode: (m: "fused" | "survey") => void;
  counts: { fused: number; survey: number };
  health: Health | undefined;
  vesselsAgo: string | null;
  drawerOpen: boolean;
  onDrawer: () => void;
  narrow: boolean;
  clock: string;
  sceneAt: string | null;
  accent: string;
  now: number;
};

function Pill({ label, value, ok }: { label: string; value?: string; ok: boolean }) {
  const color = ok ? C.match : C.unres;
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 9px", background: hexA(color, 0.12), whiteSpace: "nowrap" }}>
      <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".1em", color }}>{label}</span>
      {value && <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".06em", color }}>{value}</span>}
    </div>
  );
}

export default function TopBar({ mode, onMode, counts, health, vesselsAgo, drawerOpen, onDrawer, narrow, clock, sceneAt, accent, now }: Props) {
  const sources = health?.sources ?? {};
  const aisOk = Object.entries(sources).some(([n, s]) => n.includes("ais") && s.state === "connected");
  const sarOk = health !== undefined && !Object.entries(sources).some(([n, s]) => n.includes("sar") && s.state === "error");
  const ago = sceneAt ? formatAgo(sceneAt, now) : "";

  return (
    <div style={{ height: 40, flex: "none", display: "flex", alignItems: "stretch", background: C.chrome, borderBottom: `1px solid ${C.chromeLine}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 16px" }}>
        <svg width="15" height="15" viewBox="0 0 16 16" style={{ flex: "none" }}>
          <circle cx="8" cy="8" r="6.5" fill="none" stroke={C.brand} strokeWidth="1.4" />
          <line x1="8" y1="0.5" x2="8" y2="3" stroke={C.brand} strokeWidth="1.4" />
          <line x1="8" y1="13" x2="8" y2="15.5" stroke={C.brand} strokeWidth="1.4" />
          <line x1="0.5" y1="8" x2="3" y2="8" stroke={C.brand} strokeWidth="1.4" />
          <line x1="13" y1="8" x2="15.5" y2="8" stroke={C.brand} strokeWidth="1.4" />
          <circle cx="8" cy="8" r="1.4" fill={C.brand} />
        </svg>
        <div style={{ fontWeight: 600, fontSize: 13.5, letterSpacing: ".16em", color: C.textHi, whiteSpace: "nowrap" }}>
          DEAD RECKONING
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "0 14px", borderLeft: `1px solid ${C.line}` }}>
        <Tabs
          grow={false}
          value={mode}
          onChange={(k) => onMode(k as "fused" | "survey")}
          accent={mode === "survey" ? C.survey : C.accent}
          items={[
            { key: "fused", label: "Fused ROIs", count: String(counts.fused) },
            { key: "survey", label: "Survey ROIs", count: String(counts.survey) },
          ]}
        />
      </div>

      <div style={{ flex: 1 }} />

      {narrow && (
        <div
          onClick={onDrawer}
          style={{
            display: "flex",
            alignItems: "center",
            padding: "0 14px",
            cursor: "pointer",
            background: drawerOpen ? C.chromeLine : "transparent",
            borderLeft: `1px solid ${C.line}`,
          }}
        >
          <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".12em", color: drawerOpen ? C.textHi : C.textDim, whiteSpace: "nowrap" }}>
            {drawerOpen ? "✕ Contacts" : "☰ Contacts"}
          </span>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 16px", borderLeft: `1px solid ${C.line}` }}>
        <Pill label="Imagery link" ok={sarOk} />
        {vesselsAgo && <Pill label="AIS Live" value={vesselsAgo} ok={aisOk} />}
      </div>

      {sceneAt && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 14px", borderLeft: `1px solid ${C.line}`, background: hexA(accent, 0.08) }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".14em", color: C.label, whiteSpace: "nowrap" }}>SCENE</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: accent, letterSpacing: ".04em", whiteSpace: "nowrap" }}>{utcStamp(sceneAt)}</span>
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, whiteSpace: "nowrap" }}>{ago === "just now" ? "now" : `−${ago.replace(" ago", "")}`}</span>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", padding: "0 16px", borderLeft: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.textHi, letterSpacing: ".04em", whiteSpace: "nowrap" }}>{clock}</span>
      </div>
    </div>
  );
}
