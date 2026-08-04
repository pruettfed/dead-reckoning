import { C, MONO, hexA } from "../../theme";

type Props = { checked: boolean; onChange: () => void; label: string; accent: string; compact?: boolean };

export default function Checkbox({ checked, onChange, label, accent, compact = false }: Props) {
  return (
    <div onClick={onChange} style={{ display: "flex", alignItems: "center", gap: compact ? 7 : 9, padding: compact ? "7px 11px" : "11px 16px", cursor: "pointer" }}>
      <div
        style={{
          width: compact ? 9 : 11,
          height: compact ? 9 : 11,
          border: `1px solid ${checked ? hexA(accent, 0.85) : "rgba(255,255,255,.2)"}`,
          background: checked ? accent : "transparent",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <span style={{ fontSize: compact ? 7 : 8, lineHeight: 1, color: C.bg, fontWeight: 700, opacity: checked ? 1 : 0 }}>✓</span>
      </div>
      <span style={{ fontFamily: MONO, fontSize: compact ? 8.5 : 9.5, letterSpacing: ".1em", color: checked ? C.text : "#5f6c72", whiteSpace: "nowrap" }}>
        {label}
      </span>
    </div>
  );
}
