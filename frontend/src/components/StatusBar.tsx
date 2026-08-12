import { C, MONO, hexA } from "../theme";
import { Ticker } from "./ui";
import type { TickerItem } from "./ui";
import type { StatusMessage } from "../types";

export type Stat = { k: string; v: string; color: string };

type Props = {
  roiLabel: string;
  stats: Stat[];
  ticker: TickerItem[];
  statusMessage?: StatusMessage;
};

const LEVEL_COLOR: Record<StatusMessage["level"], string> = {
  info: C.accent,
  warning: C.amber,
  critical: C.dark,
};

export default function StatusBar({ roiLabel, stats, ticker, statusMessage }: Props) {
  if (statusMessage?.active && statusMessage.message) {
    const color = LEVEL_COLOR[statusMessage.level] ?? C.amber;
    return (
      <div
        style={{
          height: 30,
          flex: "none",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 14px",
          background: hexA(color, 0.14),
          borderTop: `1px solid ${hexA(color, 0.55)}`,
          overflowX: "auto",
          overflowY: "hidden",
        }}
      >
        <span
          style={{
            fontFamily: MONO,
            fontSize: 9.5,
            letterSpacing: ".14em",
            color,
            whiteSpace: "nowrap",
            flex: "none",
          }}
        >
          {statusMessage.level.toUpperCase()}
        </span>
        <span
          style={{
            fontFamily: MONO,
            fontSize: 10.5,
            color: C.textHi,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {statusMessage.message}
        </span>
      </div>
    );
  }

  return (
    <div style={{ height: 30, flex: "none", display: "flex", alignItems: "stretch", background: C.chrome, borderTop: `1px solid ${C.chromeLine}`, overflowX: "auto", overflowY: "hidden" }}>
      <div style={{ display: "flex", flex: "none", alignItems: "center", gap: 8, padding: "0 14px", borderRight: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".1em", color: C.textSubtle, whiteSpace: "nowrap" }}>{roiLabel}</span>
      </div>
      {stats.map((s) => (
        <div key={s.k} style={{ display: "flex", flex: "none", alignItems: "center", gap: 8, padding: "0 15px", borderRight: `1px solid ${C.hairline}` }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".14em", color: C.label, whiteSpace: "nowrap" }}>{s.k}</span>
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: s.color, whiteSpace: "nowrap" }}>{s.v}</span>
        </div>
      ))}
      <Ticker items={ticker} />
    </div>
  );
}
