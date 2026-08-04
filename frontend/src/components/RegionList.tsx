import { C, MONO, SANS } from "../theme";
import { Card, Field, SectionHeader } from "./ui";
import { formatAgo, formatCountdown } from "../countdown";
import type { Roi, Schedule } from "../types";

type Props = {
  rois: Roi[];
  mode: "fused" | "survey";
  selected: string;
  onSelect: (name: string) => void;
  schedule: Schedule | undefined;
  counts: Record<string, number | undefined>;
  now: number;
};

const BLURB = {
  fused: "SAR detections are matched against AIS. Unmatched hulls outside the false-match floor are called dark.",
  survey: "No AIS coverage. Contacts are counted and measured only — no vessel can be called dark here.",
};

export default function RegionList({ rois, mode, selected, onSelect, schedule, counts, now }: Props) {
  const shown = rois.filter((r) => r.mode === mode);
  const accent = mode === "survey" ? C.survey : C.accent;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <SectionHeader title={mode === "survey" ? "SURVEY REGIONS" : "FUSED REGIONS"} count={String(shown.length)} />
      <div style={{ fontFamily: SANS, fontSize: 10.5, lineHeight: 1.5, color: "#68757b", padding: "0 3px 3px" }}>
        {BLURB[mode]}
      </div>
      {shown.map((r) => {
        const row = schedule?.regions.find((x) => x.name === r.name);
        const on = r.name === selected;
        const analyzing = row?.state === "analyzing";
        const due = row?.next_expected_at && new Date(row.next_expected_at).getTime() > now;
        const stat = analyzing ? "Analyzing" : due ? formatCountdown(row!.next_expected_at!, now) : "Due";
        return (
          <Card key={r.name} accent={accent} selected={on} onClick={() => onSelect(r.name)}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontSize: 12.5, fontWeight: 500, letterSpacing: ".02em", color: on ? C.textHi : C.textMid, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {r.label}
              </span>
              <span style={{ flex: "none", fontFamily: MONO, fontSize: 10, color: analyzing ? C.amber : "#68757b" }}>{stat}</span>
            </div>
            <div style={{ display: "flex", gap: 14 }}>
              <Field label="PASSES" value={`${r.passes_per_month} / mo`} />
              {r.mode === "fused" ? (
                <Field label="AIS" value={counts[r.name] === undefined ? "…" : String(counts[r.name])} />
              ) : (
                <Field label="LAST" value={row?.last_processed_at ? formatAgo(row.last_processed_at, now) : "never"} />
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
