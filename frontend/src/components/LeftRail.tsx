import { ReactNode } from "react";

import { C } from "../theme";

export default function LeftRail({ width, children }: { width: number; children: ReactNode }) {
  return (
    <div
      style={{
        width,
        flex: "none",
        display: "flex",
        flexDirection: "column",
        background: C.panel,
        borderRight: `1px solid ${C.line}`,
        overflowY: "auto",
        padding: "14px 12px 16px",
        gap: 20,
      }}
    >
      {children}
    </div>
  );
}
