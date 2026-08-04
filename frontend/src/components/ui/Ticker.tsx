import { C, MONO } from "../../theme";

export type TickerItem = { time: string; text: string; color: string };

const GAP = 28;

// One cycle rendered twice at width:max-content, so translateX(-50%) lands
// exactly on the seam and the feed never snaps. Trailing margins (not `gap`)
// keep the two halves identical in width.
function Cycle({ items }: { items: TickerItem[] }) {
  return (
    <div style={{ display: "flex", alignItems: "center", width: "max-content" }}>
      {items.map((t, i) => (
        <span key={i} style={{ display: "flex", alignItems: "center", gap: 7, marginRight: GAP, fontFamily: MONO, fontSize: 9.5, letterSpacing: ".04em", whiteSpace: "nowrap" }}>
          <span style={{ color: "#3d474c" }}>{t.time}</span>
          <span style={{ color: t.color }}>{t.text}</span>
        </span>
      ))}
      <span style={{ marginRight: GAP, fontFamily: MONO, fontSize: 8.5, letterSpacing: ".28em", color: C.label, whiteSpace: "nowrap" }}>
        ◆ END OF FEED ◆
      </span>
    </div>
  );
}

export default function Ticker({ items }: { items: TickerItem[] }) {
  if (items.length === 0) return <div style={{ flex: 1, minWidth: 40 }} />;
  // Repeat short feeds so one cycle is wider than the bar and no blank gap
  // ever scrolls through.
  const reps = Math.max(1, Math.ceil(6 / items.length));
  const cycle = Array.from({ length: reps }, () => items).flat();

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
          left: 0,
          top: 0,
          bottom: 0,
          width: "max-content",
          display: "flex",
          alignItems: "center",
          whiteSpace: "nowrap",
          animation: `dr-ticker ${(cycle.length + 1) * 5}s linear infinite`,
        }}
      >
        <Cycle items={cycle} />
        <Cycle items={cycle} />
      </div>
    </div>
  );
}
