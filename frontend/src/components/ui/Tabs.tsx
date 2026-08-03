import { C, MONO, selectable } from "../../theme";

export type TabItem = { key: string; label: string; count?: string };

type Props = {
  items: TabItem[];
  value: string;
  onChange: (key: string) => void;
  accent: string;
  grow?: boolean;
};

export default function Tabs({ items, value, onChange, accent, grow = true }: Props) {
  return (
    <>
      {items.map((t) => {
        const surface = selectable(accent, t.key === value);
        return (
          <div
            key={t.key}
            onClick={() => onChange(t.key)}
            style={{
              flex: grow ? 1 : "none",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
              padding: grow ? "8px 0" : "6px 12px",
              cursor: "pointer",
              background: surface.background,
              border: `1px solid ${surface.borderColor}`,
            }}
          >
            <span style={{ fontFamily: MONO, fontSize: grow ? 9.5 : 10, letterSpacing: ".13em", lineHeight: 1, color: surface.color, whiteSpace: "nowrap" }}>
              {t.label}
            </span>
            {t.count !== undefined && (
              <span style={{ fontFamily: MONO, fontSize: 9, color: C.label }}>{t.count}</span>
            )}
          </div>
        );
      })}
    </>
  );
}
