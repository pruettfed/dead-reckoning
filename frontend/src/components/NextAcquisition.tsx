import { useQuery } from "@tanstack/react-query";

import { apiGet } from "../api";
import { formatCountdown } from "../countdown";
import { C, MONO } from "../theme";
import { Brackets, SectionHeader } from "./ui";
import type { NextPass, SchedulerStatus } from "../types";

type Props = { roi: string; roiLabel: string; accent: string; now: number; scheduler?: SchedulerStatus };

function utcStamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getUTCDate())} ${months[d.getUTCMonth()]} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}Z`;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
      <span style={{ fontFamily: MONO, fontSize: 8, letterSpacing: ".14em", color: C.label, whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.textSubtle, letterSpacing: ".02em", textAlign: "right" }}>{value}</span>
    </div>
  );
}

// A pass estimate is about the satellite, not this deployment — say so when the scheduler isn't running.
function schedulerNote(s: SchedulerStatus | undefined): string | null {
  if (!s) return null;
  if (s.state === "warming_up") return `Holding for AIS coverage: ${s.detail}`;
  if (s.state === "idle") return `Analysis not running: ${s.detail}`;
  if (s.state === "disabled") return "Automatic analysis is switched off";
  return null;
}

export default function NextAcquisition({ roi, roiLabel, accent, now, scheduler }: Props) {
  const q = useQuery({
    queryKey: ["next-pass", roi],
    queryFn: () => apiGet<NextPass>("/analysis/next-pass", { roi }),
    refetchInterval: 60_000,
  });

  const expected = q.data?.next_expected_at ?? null;
  const note = schedulerNote(scheduler);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <SectionHeader title="NEXT ACQUISITION" subtitle={roiLabel} />
      <div style={{ padding: 12, background: "rgba(255,255,255,.045)", border: `1px solid ${C.lineStrong}`, position: "relative", display: "flex", flexDirection: "column", gap: 10 }}>
        <Brackets color={accent} />
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 23, color: accent, letterSpacing: ".02em" }}>
              {expected ? formatCountdown(expected, now) : "—"}
            </span>
            <span style={{ fontFamily: MONO, fontSize: 9, color: C.faint, letterSpacing: ".1em" }}>EST</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: "#6d797f", marginTop: 5, lineHeight: 1.6 }}>
            {expected
              ? `${roiLabel} / ${utcStamp(expected)}`
              : "Too few recent passes over this region to estimate the next one"}
          </div>
        </div>
        {note && (
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.amber, lineHeight: 1.6, letterSpacing: ".02em" }}>
            {note}
          </div>
        )}
        <div style={{ height: 1, background: C.line }} />
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <Row label="LATEST PASS" value={utcStamp(q.data?.latest_scene_sensed_at ?? null)} />
          <Row label="LAST ANALYZED" value={utcStamp(q.data?.last_processed_at ?? null)} />
        </div>
      </div>
    </div>
  );
}
