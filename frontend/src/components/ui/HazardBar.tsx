import { ReactNode } from "react";

import { C, hexA } from "../../theme";

type Props = { color: string; children?: ReactNode; height?: number };

// 45° caution stripe — classification banner and contact group dividers.
export default function HazardBar({ color, children, height }: Props) {
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
      }}
    >
      {children}
    </div>
  );
}
