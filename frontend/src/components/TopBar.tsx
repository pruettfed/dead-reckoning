import logo from "../assets/logo.svg";
import { formatAgo, utcStamp } from "../countdown";
import { C, MONO, hexA } from "../theme";
import { Tabs } from "./ui";

// Identity and navigation only. Source health, data sources and the version
// live in the status bar, which is the one place that answers "what is the
// system doing" — the same bar the outage banner already appears in.
type Props = {
  mode: "fused" | "survey";
  onMode: (m: "fused" | "survey") => void;
  counts: { fused: number; survey: number };
  drawerOpen: boolean;
  onDrawer: () => void;
  narrow: boolean;
  clock: string;
  sceneAt: string | null;
  accent: string;
  now: number;
};

export default function TopBar({ mode, onMode, counts, drawerOpen, onDrawer, narrow, clock, sceneAt, accent, now }: Props) {
  const ago = sceneAt ? formatAgo(sceneAt, now) : "";

  return (
    <div style={{ height: 40, flex: "none", display: "flex", alignItems: "stretch", background: C.chrome, borderBottom: `1px solid ${C.chromeLine}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 16px" }}>
        <img src={logo} width={26} height={26} alt="" style={{ flex: "none" }} />
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
