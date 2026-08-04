import { contactId, contactMmsi, contactState, STATE_LABEL } from "../contactState";
import { C, MONO, hexA, stateColor } from "../theme";
import DossierDetail from "./DossierDetail";
import DossierHistory from "./DossierHistory";
import SarChip from "./SarChip";
import { Tabs, Tag } from "./ui";
import type { Detection, Roi, Scene, Sighting, Vessel } from "../types";

type Props = {
  detection: Detection | null;
  vessel: Vessel | null;
  mode: Roi["mode"];
  scene: Scene | null;
  tab: "DETAIL" | "HISTORY";
  onTab: (t: "DETAIL" | "HISTORY") => void;
  onClose: () => void;
  onSelectSighting: (s: Sighting) => void;
  watched: boolean;
  onToggleWatch: () => void;
  top: number;
  side: "left" | "right";
};

export default function Dossier(p: Props) {
  const { detection, vessel, mode, scene, tab, onTab, onClose } = p;
  if (!detection && !vessel) return null;

  const state = vessel ? "ais" : contactState(detection!, mode);
  const color = stateColor(state);
  const id = vessel ? String(vessel.mmsi) : contactId(detection!, mode);
  const mmsi = vessel ? vessel.mmsi : contactMmsi(detection!);
  // Nothing in a survey region carries an MMSI, so there is no history to trace.
  const history = mode !== "survey";
  const view = history ? tab : "DETAIL";

  return (
    <div
      style={{
        position: "absolute",
        [p.side]: 18,
        top: p.top,
        zIndex: 700,
        width: 300,
        maxWidth: "calc(100% - 36px)",
        maxHeight: `calc(100% - ${p.top + 18}px)`,
        display: "flex",
        flexDirection: "column",
        background: "rgba(12,14,16,.88)",
        border: "1px solid rgba(255,255,255,.15)",
        backdropFilter: "blur(10px)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 12px", flex: "none", borderBottom: "1px solid rgba(255,255,255,.09)" }}>
        <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color, letterSpacing: ".05em" }}>{id}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Tag color={color} background={hexA(color, 0.13)}>{STATE_LABEL[state]}</Tag>
          <div onClick={onClose} style={{ cursor: "pointer", fontFamily: MONO, fontSize: 12, color: "#6d797f", padding: "0 2px" }}>✕</div>
        </div>
      </div>

      {history && (
        <div style={{ display: "flex", flex: "none", gap: 7, padding: "9px 12px", borderBottom: "1px solid rgba(255,255,255,.09)" }}>
          <Tabs
            items={[{ key: "DETAIL", label: "DETAIL" }, { key: "HISTORY", label: "HISTORY" }]}
            value={tab}
            onChange={(k) => onTab(k as "DETAIL" | "HISTORY")}
            accent={color}
          />
        </div>
      )}

      {detection && scene && view === "DETAIL" && (
        <SarChip
          scene={scene}
          lat={detection.lat}
          lon={detection.lon}
          color={color}
          label={`${STATE_LABEL[state]} ${(detection.confidence * 100).toFixed(0)}%`}
        />
      )}

      <div style={{ padding: "13px 14px 14px", overflowY: "auto", minHeight: 0 }}>
        {view === "DETAIL" ? (
          <DossierDetail detection={detection} vessel={vessel} mode={mode} scene={scene} color={color} />
        ) : (
          <DossierHistory mmsi={mmsi} onSelectSighting={p.onSelectSighting} />
        )}
        <button
          onClick={p.onToggleWatch}
          disabled={mmsi === null}
          style={{
            width: "100%",
            marginTop: 9,
            background: p.watched ? hexA(C.accent, 0.16) : "rgba(255,255,255,.04)",
            border: `1px solid ${p.watched ? hexA(C.accent, 0.55) : C.lineStrong}`,
            color: mmsi === null ? C.label : p.watched ? C.accent : "#b6c3c9",
            fontFamily: MONO,
            fontSize: 10,
            letterSpacing: ".14em",
            padding: "10px 0",
            cursor: mmsi === null ? "not-allowed" : "pointer",
          }}
        >
          {mmsi === null ? "NO TRANSPONDER IDENTITY" : p.watched ? "✓ ON WATCHLIST" : "+ ADD TO WATCHLIST"}
        </button>
      </div>
    </div>
  );
}
