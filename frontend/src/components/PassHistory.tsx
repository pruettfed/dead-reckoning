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

export default function PassHistory({ roiLabel, scenes, selectedId, onSelect, expanded, onToggleExpand, accent, survey }: Props) {
  const shown = expanded ? scenes : scenes.slice(0, VISIBLE);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <SectionHeader title="PASS HISTORY" subtitle={roiLabel} count={String(scenes.length)} />
      {scenes.length === 0 && (
        <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint, padding: "4px 3px" }}>no analyzed passes yet</div>
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
            style={{ padding: "9px 12px", flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ fontFamily: MONO, fontSize: 11, color: s.id === selectedId ? C.textHi : C.textMid, letterSpacing: ".03em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {utcStamp(s.sensed_at)}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 8.5, color: failed ? hexA(C.dark, 0.8) : C.label, letterSpacing: ".08em", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis" }}>
                {failed
                  ? s.failure_reason ?? "Analysis error"
                  : processing
                    ? `${s.platform} / IW / VV`
                    : `${s.platform} / IW / VV · ${s.detection_count} contacts`}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "none" }}>
              <Tag
                color={flagColor ? C.bg : "#68757b"}
                background={flagColor ? hexA(flagColor, 0.85) : "rgba(255,255,255,.06)"}
              >
                {flag}
              </Tag>
            </div>
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
