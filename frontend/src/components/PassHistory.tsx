import { utcStamp } from "../countdown";
import { C, MONO, hexA } from "../theme";
import { Card, SectionHeader, Tag } from "./ui";
import type { Scene } from "../types";

type Props = {
  roiLabel: string;
  scenes: Scene[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  expanded: boolean;
  onToggleExpand: () => void;
  accent: string;
  survey: boolean;
};

const VISIBLE = 5;

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
      <span style={{ fontFamily: MONO, fontSize: 8, letterSpacing: ".12em", color: C.label, whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ fontFamily: MONO, fontSize: 9.5, color, whiteSpace: "nowrap" }}>{value}</span>
    </div>
  );
}

export default function PassHistory({ roiLabel, scenes, selectedId, onSelect, expanded, onToggleExpand, accent, survey }: Props) {
  const shown = expanded ? scenes : scenes.slice(0, VISIBLE);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <SectionHeader title="PASS HISTORY" subtitle={roiLabel} count={String(scenes.length)} />
      {scenes.length === 0 && (
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, padding: "4px 3px" }}>No analyzed passes yet</div>
      )}
      {shown.map((s, i) => {
        const failed = s.status === "failed";
        const processing = s.status === "processing";
        const flag = failed ? "FAILED" : processing ? "PROCESSING" : i === 0 ? "LATEST" : survey ? "SURVEY" : s.dark_count ? `${s.dark_count} DARK` : "CLEAR";
        // Amber sits between the green of a finished latest pass and the red of
        // a failed one — the run is still in flight.
        const flagColor = failed ? C.dark : processing ? C.amber : i === 0 ? C.match : !survey && s.dark_count ? C.dark : null;
        return (
          <Card
            key={s.id}
            accent={accent}
            selected={s.id === selectedId}
            onClick={() => onSelect(s.id)}
            style={{ padding: "9px 12px", gap: 7 }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: MONO, fontSize: 11, color: s.id === selectedId ? C.textHi : C.textMid, letterSpacing: ".03em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {utcStamp(s.sensed_at)}
                  {/* The platform is the one acquisition field that varies pass
                      to pass (mode and polarisation are IW/VV on every scene),
                      but it is too slight to hold a line of its own. */}
                  <span style={{ fontSize: 9, color: C.label, letterSpacing: ".08em" }}> · {s.platform}</span>
                </div>
                {failed && (
                  <div style={{ fontFamily: MONO, fontSize: 8.5, color: hexA(C.dark, 0.8), letterSpacing: ".08em", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis" }}>
                    {s.failure_reason ?? "Analysis error"}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "none" }}>
                <Tag
                  color={flagColor ? C.bg : "#68757b"}
                  background={flagColor ? hexA(flagColor, 0.85) : "rgba(255,255,255,.06)"}
                >
                  {flag}
                </Tag>
              </div>
            </div>
            {/* The scene's own credibility: the noise floor its dark count is
                measured against, and what it found of the hulls AIS says were
                there. Survey scenes never fuse, so both would read "—". */}
            {!failed && !processing && !survey && (
              <div style={{ display: "flex", gap: 14, paddingTop: 6, borderTop: `1px solid ${C.hairline}` }}>
                <Metric
                  label="FALSE MATCH"
                  value={s.chance_match_rate != null ? `${(s.chance_match_rate * 100).toFixed(1)}%` : "—"}
                  color={s.chance_match_rate != null ? C.unres : C.faint}
                />
                <Metric
                  label="RECALL"
                  value={s.recall_large_total ? `${s.recall_large_detected}/${s.recall_large_total}` : "—"}
                  color={s.recall_large_total ? C.match : C.faint}
                />
              </div>
            )}
          </Card>
        );
      })}
      {scenes.length > VISIBLE && (
        <div
          onClick={onToggleExpand}
          style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: "8px 0", cursor: "pointer", background: C.fill, border: `1px solid ${C.line}` }}
        >
          <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: ".1em", lineHeight: 1, color: "#7f8b91" }}>
            {expanded ? "SHOW LESS" : `SHOW ALL ${scenes.length}`}
          </span>
        </div>
      )}
    </div>
  );
}
