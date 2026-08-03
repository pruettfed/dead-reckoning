import { C, MONO } from "../../theme";

type Props = { title: string; subtitle?: string; count?: string; bleed?: number };

export default function SectionHeader({ title, subtitle, count, bleed = 12 }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 6, padding: "0 3px" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6, minWidth: 0 }}>
          <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".18em", color: C.textDim, whiteSpace: "nowrap" }}>
            {title}
          </span>
          {subtitle && (
            <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".03em", color: C.textSubtle, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {subtitle}
            </span>
          )}
        </div>
        {count !== undefined && (
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.countDim, flex: "none" }}>{count}</span>
        )}
      </div>
      <div style={{ height: 1, background: C.chromeLine, margin: `0 -${bleed}px` }} />
    </div>
  );
}
