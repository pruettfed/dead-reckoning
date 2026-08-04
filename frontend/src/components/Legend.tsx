import { C, MONO, hexA } from "../theme";

type Entry = { color: string; label: string };

type Props = { survey: boolean; showVessels: boolean; showLandMasked: boolean; compact: boolean };

export default function Legend({ survey, showVessels, showLandMasked, compact }: Props) {
  const entries: Entry[] = survey
    ? [{ color: C.survey, label: "Contact" }]
    : [
        { color: C.dark, label: "Dark" },
        { color: C.unres, label: "Unresolved" },
        { color: C.match, label: "AIS match" },
      ];
  if (showLandMasked) entries.push({ color: C.masked, label: "Land-masked" });
  if (showVessels) entries.push({ color: C.accent, label: compact ? "Live AIS" : "Live AIS / heading" });

  return (
    <div style={{ background: C.glass, border: `1px solid ${C.lineStrong}`, padding: compact ? "7px 11px" : "11px 15px", display: "flex", flexWrap: "wrap", gap: compact ? "6px 11px" : "8px 16px", backdropFilter: "blur(6px)", pointerEvents: "auto" }}>
      {entries.map((l) => (
        <div key={l.label} style={{ display: "flex", alignItems: "center", gap: compact ? 7 : 9 }}>
          <div style={{ width: compact ? 9 : 11, height: compact ? 7 : 9, border: `1.5px solid ${l.color}`, background: hexA(l.color, 0.1) }} />
          <span style={{ fontFamily: MONO, fontSize: compact ? 8.5 : 9.5, letterSpacing: ".08em", color: "#9aa8ae", whiteSpace: "nowrap" }}>{l.label}</span>
        </div>
      ))}
    </div>
  );
}
