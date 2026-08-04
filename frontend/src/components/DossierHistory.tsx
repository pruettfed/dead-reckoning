import { useQuery } from "@tanstack/react-query";

import { apiGet } from "../api";
import { CONTINUITY_BINS, CONTINUITY_WINDOW_H, CONTINUITY_WINDOW_MS, continuity, formatGap } from "../continuity";
import { utcStamp } from "../countdown";
import { C, MONO, hexA, stateColor } from "../theme";
import { Tag } from "./ui";
import type { Sighting, TrackPoint } from "../types";

const GAP_THRESHOLD_MS = 2 * CONTINUITY_WINDOW_MS / CONTINUITY_BINS;

type Props = { mmsi: number | null; onSelectSighting: (s: Sighting) => void };

export default function DossierHistory({ mmsi, onSelectSighting }: Props) {
  const track = useQuery({
    queryKey: ["track", mmsi, CONTINUITY_WINDOW_H],
    queryFn: () => apiGet<TrackPoint[]>(`/vessels/${mmsi}/track`, { hours: String(CONTINUITY_WINDOW_H) }),
    enabled: mmsi !== null,
  });

  const sightings = useQuery({
    queryKey: ["sightings", mmsi],
    queryFn: () => apiGet<Sighting[]>(`/vessels/${mmsi}/sightings`),
    enabled: mmsi !== null,
  });

  if (mmsi === null) {
    return (
      <div style={{ fontFamily: MONO, fontSize: 10, lineHeight: 1.6, color: C.faint, padding: "6px 2px" }}>
        No transponder identity — nothing to trace.
      </div>
    );
  }

  const { bins, gapMs } = continuity((track.data ?? []).map((p) => p.time), Date.now());
  const gapped = gapMs >= GAP_THRESHOLD_MS;

  return (
    <div>
      <div style={{ padding: "10px 12px", background: "rgba(255,255,255,.035)", border: `1px solid ${C.chromeLine}` }}>
        <div style={{ fontFamily: MONO, fontSize: 8, letterSpacing: ".14em", color: C.label }}>AIS CONTINUITY / {CONTINUITY_WINDOW_H}H</div>
        <div style={{ display: "flex", gap: 1.5, height: 14, marginTop: 7 }}>
          {bins.map((on, i) => (
            <div key={i} style={{ flex: 1, background: on ? "rgba(150,175,190,.5)" : hexA(C.dark, 0.32) }} />
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO, fontSize: 8, color: C.label, marginTop: 5 }}>
          <span>-{CONTINUITY_WINDOW_H}H</span>
          <span style={{ color: gapped ? C.dark : C.label }}>{gapped ? `GAP ${formatGap(gapMs)}` : "CONTINUOUS"}</span>
          <span>NOW</span>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 8, lineHeight: 1.5, color: C.label, marginTop: 6 }}>
          Gaps include time spent outside the subscribed area.
        </div>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".15em", color: C.textDim, margin: "15px 0 8px" }}>
        PRIOR SIGHTINGS
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {(sightings.data ?? []).length === 0 && (
          <div style={{ fontFamily: MONO, fontSize: 9, color: C.label }}>
            {sightings.isPending ? "loading…" : "no prior SAR sightings of this MMSI"}
          </div>
        )}
        {(sightings.data ?? []).map((s) => {
          const state = s.matched ? "matched" : s.match_state ?? "contact";
          const color = stateColor(state);
          return (
            <div
              key={s.detection_id}
              onClick={() => onSelectSighting(s)}
              style={{ padding: "8px 11px", background: C.fill, border: `1px solid ${C.line}`, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, cursor: "pointer" }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.textSubtle }}>{utcStamp(s.sensed_at)}</div>
                <div style={{ fontFamily: MONO, fontSize: 8.5, color: C.label, letterSpacing: ".06em", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {s.label}
                </div>
              </div>
              <Tag color={C.bg} background={hexA(color, 0.8)}>{s.matched ? "MATCH" : "CANDIDATE"}</Tag>
            </div>
          );
        })}
      </div>
    </div>
  );
}
