import { CSSProperties, ReactNode } from "react";

import { selectable } from "../../theme";

type Props = {
  accent: string;
  selected?: boolean;
  onClick?: () => void;
  children: ReactNode;
  style?: CSSProperties;
};

export default function Card({ accent, selected = false, onClick, children, style }: Props) {
  const surface = selectable(accent, selected);
  return (
    <div
      onClick={onClick}
      style={{
        padding: "10px 12px",
        cursor: onClick ? "pointer" : "default",
        background: surface.background,
        border: `1px solid ${surface.borderColor}`,
        display: "flex",
        flexDirection: "column",
        gap: 7,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
