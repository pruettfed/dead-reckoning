import { MONO } from "../../theme";

export type TickerItem = { time: string; text: string; color: string };

export default function Ticker({ items }: { items: TickerItem[] }) {
  const loop = [...items, ...items];
  return (
    <div
      style={{
        flex: 1,
        minWidth: 40,
        overflow: "hidden",
        position: "relative",
        maskImage: "linear-gradient(90deg,transparent,#000 24px,#000 calc(100% - 24px),transparent)",
        WebkitMaskImage: "linear-gradient(90deg,transparent,#000 24px,#000 calc(100% - 24px),transparent)",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          gap: 28,
          whiteSpace: "nowrap",
          animation: `dr-ticker ${items.length * 4.5}s linear infinite`,
        }}
      >
        {loop.map((t, i) => (
          <span key={i} style={{ display: "flex", alignItems: "center", gap: 7, fontFamily: MONO, fontSize: 9.5, letterSpacing: ".04em", whiteSpace: "nowrap" }}>
            <span style={{ color: "#3d474c" }}>{t.time}</span>
            <span style={{ color: t.color }}>{t.text}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
