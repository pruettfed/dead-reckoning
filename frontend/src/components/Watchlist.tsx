import { C, MONO } from "../theme";
import { SectionHeader } from "./ui";
import type { WatchEntry } from "../useWatchlist";

export default function Watchlist({ entries }: { entries: WatchEntry[] }) {
  return (
    <div style={{ flex: "none", borderTop: `1px solid ${C.line}`, background: C.panelAlt, padding: "12px 12px 14px" }}>
      <div style={{ marginBottom: 8 }}>
        <SectionHeader title="WATCHLIST" count={String(entries.length)} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {entries.length === 0 && (
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".06em", color: C.label, padding: "4px 3px" }}>
            No vessels watched
          </div>
        )}
        {entries.map((w) => (
          <div key={w.mmsi} style={{ padding: "9px 12px", background: C.fill, border: `1px solid ${C.line}`, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.textSubtle, letterSpacing: ".04em" }}>{w.mmsi}</div>
              <div style={{ fontFamily: MONO, fontSize: 8.5, color: C.label, letterSpacing: ".06em", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {w.name ?? "unknown vessel"}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
