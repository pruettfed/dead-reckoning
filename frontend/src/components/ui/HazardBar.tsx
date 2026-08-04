import { CSSProperties, ReactNode } from "react";

import { C, hexA } from "../../theme";

type Props = { color: string; children?: ReactNode; height?: number; style?: CSSProperties };

// 45° caution stripe — classification banner and contact group dividers.
export default function HazardBar({ color, children, height, style }: Props) {
  return (
    <div
      style={{
        position: "relative",
        height,
        flex: "none",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: `repeating-linear-gradient(45deg, ${hexA(color, 0.35)} 0 6px, ${hexA(C.bg, 0.9)} 6px 12px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
