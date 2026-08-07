import { useEffect, useRef, useState } from "react";

import { formatAgo, utcStamp } from "../countdown";
import { aisPill, sarPill } from "../sourceHealth";
import { C, MONO, hexA } from "../theme";
import { Tabs } from "./ui";
import type { Health } from "../types";

type Props = {
  mode: "fused" | "survey";
  onMode: (m: "fused" | "survey") => void;
  counts: { fused: number; survey: number };
  health: Health | undefined;
  vesselsAgo: string | null;
  drawerOpen: boolean;
  onDrawer: () => void;
  narrow: boolean;
  clock: string;
  sceneAt: string | null;
  accent: string;
  now: number;
};

const PILL_COLOR = { ok: C.match, warn: C.unres, bad: C.dark } as const;

function Pill({ label, value, state }: { label: string; value?: string | null; state: "ok" | "warn" | "bad" }) {
  const color = PILL_COLOR[state];
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 9px", background: hexA(color, 0.12), whiteSpace: "nowrap" }}>
      <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".1em", color }}>{label}</span>
      {value && <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".06em", color }}>{value}</span>}
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
    <div ref={ref} style={{ position: "relative", display: "flex", alignItems: "center" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Data sources"
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "5px 9px",
          border: "none",
          background: hexA(C.textDim, open ? 0.22 : 0.12),
          color: C.textDim,
          fontFamily: MONO,
          fontSize: 9.5,
          letterSpacing: ".1em",
          whiteSpace: "nowrap",
          cursor: "pointer",
        }}
      >
        ⓘ Data sources
      </button>
      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            zIndex: 1300,
            width: 280,
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
            background: C.panel,
            border: `1px solid ${C.lineStrong}`,
            boxShadow: "0 8px 24px rgba(0,0,0,.5)",
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

export default function TopBar({ mode, onMode, counts, health, vesselsAgo, drawerOpen, onDrawer, narrow, clock, sceneAt, accent, now }: Props) {
  const sources = health?.sources ?? {};
  const ais = aisPill(sources);
  const sar = sarPill(sources, health !== undefined);
  const ago = sceneAt ? formatAgo(sceneAt, now) : "";

  return (
    <div style={{ height: 40, flex: "none", display: "flex", alignItems: "stretch", background: C.chrome, borderBottom: `1px solid ${C.chromeLine}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 16px" }}>
        <svg width="15" height="15" viewBox="0 0 16 16" style={{ flex: "none" }}>
          <circle cx="8" cy="8" r="6.5" fill="none" stroke={C.brand} strokeWidth="1.4" />
          <line x1="8" y1="0.5" x2="8" y2="3" stroke={C.brand} strokeWidth="1.4" />
          <line x1="8" y1="13" x2="8" y2="15.5" stroke={C.brand} strokeWidth="1.4" />
          <line x1="0.5" y1="8" x2="3" y2="8" stroke={C.brand} strokeWidth="1.4" />
          <line x1="13" y1="8" x2="15.5" y2="8" stroke={C.brand} strokeWidth="1.4" />
          <circle cx="8" cy="8" r="1.4" fill={C.brand} />
        </svg>
        <div style={{ fontWeight: 600, fontSize: 13.5, letterSpacing: ".16em", color: C.textHi, whiteSpace: "nowrap" }}>
          DEAD RECKONING
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "0 14px", borderLeft: `1px solid ${C.line}` }}>
        <Tabs
          grow={false}
          value={mode}
          onChange={(k) => onMode(k as "fused" | "survey")}
          accent={mode === "survey" ? C.survey : C.accent}
          items={[
            { key: "fused", label: "Fused ROIs", count: String(counts.fused) },
            { key: "survey", label: "Survey ROIs", count: String(counts.survey) },
          ]}
        />
      </div>

      <div style={{ flex: 1 }} />

      {narrow && (
        <div
          onClick={onDrawer}
          style={{
            display: "flex",
            alignItems: "center",
            padding: "0 14px",
            cursor: "pointer",
            background: drawerOpen ? C.chromeLine : "transparent",
            borderLeft: `1px solid ${C.line}`,
          }}
        >
          <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".12em", color: drawerOpen ? C.textHi : C.textDim, whiteSpace: "nowrap" }}>
            {drawerOpen ? "✕ Contacts" : "☰ Contacts"}
          </span>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 16px", borderLeft: `1px solid ${C.line}` }}>
        <Pill label="Imagery link" state={sar} />
        {vesselsAgo && (
          <Pill label={ais.label} value={ais.lastMessageAt ? formatAgo(ais.lastMessageAt, now) : undefined} state={ais.state} />
        )}
        <SourcesInfo />
      </div>

      {sceneAt && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 14px", borderLeft: `1px solid ${C.line}`, background: hexA(accent, 0.08) }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".14em", color: C.label, whiteSpace: "nowrap" }}>SCENE</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: accent, letterSpacing: ".04em", whiteSpace: "nowrap" }}>{utcStamp(sceneAt)}</span>
          <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, whiteSpace: "nowrap" }}>{ago === "just now" ? "now" : `−${ago.replace(" ago", "")}`}</span>
        </div>
      )}

      <div style={{ display: "flex", alignItems: "center", padding: "0 16px", borderLeft: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: MONO, fontSize: 12.5, color: C.textHi, letterSpacing: ".04em", whiteSpace: "nowrap" }}>{clock}</span>
      </div>
    </div>
  );
}
