import { useEffect, useRef, useState } from "react";

import { formatAgo } from "../countdown";
import { aisPill, sarPill } from "../sourceHealth";
import { C, MONO, hexA } from "../theme";
import { Ticker } from "./ui";
import type { TickerItem } from "./ui";
import type { Health, StatusMessage } from "../types";

type Props = {
  ticker: TickerItem[];
  statusMessage?: StatusMessage;
  health: Health | undefined;
  now: number;
};

const LEVEL_COLOR: Record<StatusMessage["level"], string> = {
  info: C.accent,
  warning: C.amber,
  critical: C.dark,
};

const STATE_COLOR = { ok: C.match, warn: C.unres, bad: C.dark } as const;

const IMAGERY_TEXT = { ok: "OK", warn: "DEGRADED", bad: "DOWN" } as const;

const AIS_TEXT = { ok: "LIVE", warn: "STALE", bad: "DOWN" } as const;

function Cell({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: "flex", flex: "none", alignItems: "center", gap: 8, padding: "0 15px", borderLeft: `1px solid ${C.hairline}` }}>
      <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".14em", color: C.label, whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 10.5, color, whiteSpace: "nowrap" }}>{value}</span>
    </div>
  );
}

type SourceLink = { label: string; text: string; href: string };

const SOURCES: SourceLink[] = [
  { label: "AIS", text: "Live vessel positions via AISStream", href: "https://aisstream.io" },
  { label: "Satellite Imagery", text: "Contains modified Copernicus Sentinel-1 data", href: "https://dataspace.copernicus.eu" },
  {
    label: "Detection model",
    text: "Trained on SARFish + xView3-SAR",
    href: "https://github.com/pruettfed/dead-reckoning#acknowledgements",
  },
];

function SourcesInfo() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} style={{ position: "relative", display: "flex", flex: "none", alignItems: "stretch", borderLeft: `1px solid ${C.hairline}` }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Data sources"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          padding: "0 15px",
          border: "none",
          background: open ? hexA(C.textDim, 0.12) : "transparent",
          color: C.textDim,
          fontFamily: MONO,
          fontSize: 8.5,
          letterSpacing: ".14em",
          whiteSpace: "nowrap",
          cursor: "pointer",
        }}
      >
        ⓘ SOURCES
      </button>
      {open && (
        <div
          style={{
            position: "fixed",
            bottom: 38,
            right: 12,
            zIndex: 1300,
            width: 280,
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
            background: C.panel,
            border: `1px solid ${C.lineStrong}`,
            boxShadow: "0 -8px 24px rgba(0,0,0,.5)",
          }}
        >
          <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".14em", color: C.label }}>DATA SOURCES</span>
          {SOURCES.map((s) => (
            <a
              key={s.label}
              href={s.href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: "flex", flexDirection: "column", gap: 2, textDecoration: "none" }}
            >
              <span style={{ fontFamily: MONO, fontSize: 10, letterSpacing: ".06em", color: C.textHi }}>{s.label} ↗</span>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.textDim, lineHeight: 1.4 }}>{s.text}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

export default function StatusBar({ ticker, statusMessage, health, now }: Props) {
  const showMessage = statusMessage?.active && statusMessage.message;
  const messageColor = showMessage ? LEVEL_COLOR[statusMessage.level] ?? C.amber : undefined;
  const sources = health?.sources ?? {};
  const ais = aisPill(sources);
  const sar = sarPill(sources, health !== undefined);

  return (
    <div style={{ height: 30, flex: "none", display: "flex", alignItems: "stretch", background: C.chrome, borderTop: `1px solid ${C.chromeLine}`, overflowX: "auto", overflowY: "hidden" }}>
      {showMessage ? (
        <div
          style={{
            display: "flex",
            flex: 1,
            minWidth: 40,
            alignItems: "center",
            gap: 8,
            padding: "0 15px",
            border: `1px solid ${hexA(messageColor!, 0.55)}`,
            borderLeft: "none",
            background: hexA(messageColor!, 0.14),
            overflow: "hidden",
          }}
        >
          <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".14em", color: messageColor, whiteSpace: "nowrap", flex: "none" }}>
            {(statusMessage!.title ?? statusMessage!.level).toUpperCase()}
          </span>
          <span
            style={{
              fontFamily: MONO,
              fontSize: 10.5,
              fontWeight: 600,
              color: C.textHi,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {statusMessage!.message}
          </span>
        </div>
      ) : (
        <Ticker items={ticker} />
      )}
      <Cell label="IMAGERY" value={IMAGERY_TEXT[sar]} color={STATE_COLOR[sar]} />
      <Cell
        label="AIS"
        value={[AIS_TEXT[ais.state], ais.lastMessageAt && formatAgo(ais.lastMessageAt, now)].filter(Boolean).join(" ")}
        color={STATE_COLOR[ais.state]}
      />
      <SourcesInfo />
      <Cell label="VERSION" value={health?.version ?? "—"} color={C.textMid} />
    </div>
  );
}
