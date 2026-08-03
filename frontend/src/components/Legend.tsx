import { C, MONO, hexA } from "../theme";

type Entry = { color: string; label: string };

export default function Legend({ survey, showVessels, showLandMasked }: { survey: boolean; showVessels: boolean; showLandMasked: boolean }) {
  const entries: Entry[] = survey
    ? [{ color: C.survey, label: "Contact" }]
    : [
        { color: C.dark, label: "Dark" },
        { color: C.unres, label: "Unresolved" },
        { color: C.match, label: "AIS match" },
      ];
  if (showLandMasked) entries.push({ color: C.masked, label: "Land-masked" });
  if (showVessels) entries.push({ color: C.accent, label: "Live AIS / heading" });

  return (
    <div style={{ background: C.glass, border: `1px solid ${C.lineStrong}`, padding: "11px 15px", display: "flex", flexWrap: "wrap", gap: "8px 16px", backdropFilter: "blur(6px)", pointerEvents: "auto" }}>
      {entries.map((l) => (
        <div key={l.label} style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <div style={{ width: 11, height: 9, border: `1.5px solid ${l.color}`, background: hexA(l.color, 0.1) }} />
          <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".08em", color: "#9aa8ae", whiteSpace: "nowrap" }}>{l.label}</span>
        </div>
      ))}
    </div>
  );
}
