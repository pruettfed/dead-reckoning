import { CSSProperties, ReactNode } from "react";

import { C, MONO } from "../theme";

type Props = { width: number; narrow: boolean; open: boolean; title: string; count: string; children: ReactNode; footer: ReactNode };

export default function RightRail({ width, narrow, open, title, count, children, footer }: Props) {
  if (!open) return null;
  const overlay: CSSProperties = narrow
    ? { position: "absolute", inset: "0 0 0 auto", zIndex: 1200, boxShadow: "-18px 0 30px rgba(0,0,0,.5)" }
    : { position: "relative" };
  return (
    <div style={{ width, flex: "none", display: "flex", flexDirection: "column", background: C.panel, borderLeft: `1px solid ${C.line}`, minHeight: 0, ...overlay }}>
      <div style={{ flex: "none", padding: "14px 14px 12px", borderBottom: `1px solid ${C.line}`, display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".18em", color: C.textDim }}>{title}</span>
        <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.countDim }}>{count}</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "12px 12px 16px", display: "flex", flexDirection: "column", gap: 16 }}>
        {children}
      </div>
      {footer}
    </div>
  );
}
