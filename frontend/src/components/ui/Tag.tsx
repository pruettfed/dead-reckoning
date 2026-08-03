import { ReactNode } from "react";

import { MONO, hexA } from "../../theme";

type Props = { color: string; background?: string; children: ReactNode; size?: "sm" | "md" };

export default function Tag({ color, background, children, size = "sm" }: Props) {
  return (
    <span
      style={{
        fontFamily: MONO,
        fontSize: size === "sm" ? 8.5 : 9.5,
        letterSpacing: ".13em",
        padding: size === "sm" ? "2px 6px" : "3px 7px",
        color,
        background: background ?? hexA(color, 0.14),
        whiteSpace: "nowrap",
        flex: "none",
      }}
    >
      {children}
    </span>
  );
}
