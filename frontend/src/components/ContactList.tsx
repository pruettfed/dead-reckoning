import { contactId, contactState, STATE_LABEL } from "../contactState";
import { flagEmoji } from "../flag";
import { C, MONO, hexA, stateColor } from "../theme";
import { Card, Field, HazardBar, Tag } from "./ui";
import type { ContactState } from "../contactState";
import type { Detection, Roi, Vessel } from "../types";

type Props = {
  mode: Roi["mode"];
  detections: Detection[];
  vessels: Vessel[];
  sceneSelected: boolean;
  selected: { kind: "det" | "ais"; id: number } | null;
  onSelect: (sel: { kind: "det" | "ais"; id: number }) => void;
};

const ORDER: ContactState[] = ["dark", "indeterminate", "matched", "contact", "masked"];

function detFields(d: Detection, state: ContactState) {
  if (state === "matched") {
    return [
      { label: "MMSI", value: String(d.matched_mmsi) },
      { label: "FLAG", value: d.flag_iso2 ? `${flagEmoji(d.flag_iso2) ?? ""} ${d.flag_iso2}` : "—" },
    ];
  }
  if (state === "dark") {
    return [
      { label: "MARGIN", value: d.dark_margin_m != null ? `${d.dark_margin_m.toFixed(0)} m` : "—" },
      { label: "CONF", value: d.confidence_bucket },
    ];
  }
  if (state === "indeterminate") {
    return [
      { label: "CANDIDATE", value: d.candidate_mmsi != null ? String(d.candidate_mmsi) : "—" },
      { label: "Δ DR", value: d.match_distance_m != null ? `${d.match_distance_m.toFixed(0)} m` : "—" },
    ];
  }
  return [
    { label: "CONF", value: d.confidence_bucket },
    { label: "POSITION", value: `${d.lat.toFixed(3)}, ${d.lon.toFixed(3)}` },
  ];
}

export default function ContactList({ mode, detections, vessels, sceneSelected, selected, onSelect }: Props) {
  if (!sceneSelected) {
    return (
      <>
        {vessels.map((v) => (
          <Card
            key={v.mmsi}
            accent={C.accent}
            selected={selected?.kind === "ais" && selected.id === v.mmsi}
            onClick={() => onSelect({ kind: "ais", id: v.mmsi })}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.textHi, letterSpacing: ".04em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {v.ship_name ?? v.mmsi}
              </span>
              <Tag color={C.accent} background={hexA(C.accent, 0.12)} size="md">
                {v.sog != null ? `${v.sog.toFixed(1)} kn` : "—"}
              </Tag>
            </div>
            <div style={{ display: "flex", gap: 14 }}>
              <Field label="MMSI" value={String(v.mmsi)} />
              <Field label="FLAG" value={v.flag_iso2 ? `${flagEmoji(v.flag_iso2) ?? ""} ${v.flag_iso2}` : "—"} />
            </div>
          </Card>
        ))}
      </>
    );
  }

  const grouped = ORDER.map((state) => ({
    state,
    items: detections.filter((d) => contactState(d, mode) === state),
  })).filter((g) => g.items.length > 0);

  return (
    <>
      {grouped.map((g) => {
        const color = stateColor(g.state);
        return (
          <div key={g.state} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <HazardBar color={color}>
              <div style={{ display: "flex", alignItems: "center", gap: 7, whiteSpace: "nowrap", background: `linear-gradient(90deg,${hexA(C.panel, 0)} 0%,${hexA(C.panel, 0.97)} 22%,${hexA(C.panel, 0.97)} 78%,${hexA(C.panel, 0)} 100%)`, padding: "1.5px 20px" }}>
                <div style={{ width: 8, height: 6, flex: "none", background: color }} />
                <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".15em", color }}>{STATE_LABEL[g.state]}</span>
                <span style={{ fontFamily: MONO, fontSize: 9, color: C.label }}>{g.items.length}</span>
              </div>
            </HazardBar>
            {g.items.map((d) => (
              <Card
                key={d.id}
                accent={color}
                selected={selected?.kind === "det" && selected.id === d.id}
                onClick={() => onSelect({ kind: "det", id: d.id })}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.textHi, letterSpacing: ".04em" }}>{contactId(d, mode)}</span>
                  <Tag color={color} background={hexA(color, 0.12)} size="md">{(d.confidence * 100).toFixed(0)}%</Tag>
                </div>
                <div style={{ display: "flex", gap: 14 }}>
                  {detFields(d, g.state).map((f) => (
                    <Field key={f.label} label={f.label} value={f.value} />
                  ))}
                </div>
              </Card>
            ))}
          </div>
        );
      })}
    </>
  );
}
