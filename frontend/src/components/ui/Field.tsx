import { ReactNode } from "react";

import { C, MONO } from "../../theme";

type Props = { label: string; value: ReactNode; color?: string };

export default function Field({ label, value, color = C.textMid }: Props) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontFamily: MONO, fontSize: 8, letterSpacing: ".14em", color: C.label }}>{label}</div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color, marginTop: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {value}
      </div>
    </div>
  );
}
