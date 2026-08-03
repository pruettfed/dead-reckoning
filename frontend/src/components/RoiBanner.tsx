import { C, MONO, SANS, hexA } from "../theme";
import { Brackets, Tag } from "./ui";
import type { Bbox, Roi } from "../types";

function coords(b: Bbox): string {
  const f = (v: number, axis: "lat" | "lon") =>
    Math.abs(v).toFixed(2) + (axis === "lat" ? (v >= 0 ? "N" : "S") : v >= 0 ? "E" : "W");
  return `${f(b[0], "lon")} ${f(b[1], "lat")} → ${f(b[2], "lon")} ${f(b[3], "lat")}`;
}

type Props = { roi: Roi; accent: string; storyOpen: boolean; onToggleStory: () => void };

export default function RoiBanner({ roi, accent, storyOpen, onToggleStory }: Props) {
  return (
    <div style={{ position: "relative", pointerEvents: "auto", background: C.glass, border: `1px solid ${C.lineStrong}`, padding: "11px 16px 12px 15px", backdropFilter: "blur(6px)", width: 360, maxWidth: "100%" }}>
      <Brackets color={accent} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: ".06em", color: C.textHi, lineHeight: 1 }}>{roi.label}</span>
          <Tag color={accent} background={hexA(accent, 0.14)}>{roi.mode === "survey" ? "SURVEY" : "FUSED"}</Tag>
        </div>
        <div onClick={onToggleStory} style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 5, flex: "none" }}>
          <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".12em", color: "#7c888e", whiteSpace: "nowrap" }}>STORY</span>
          <span style={{ fontFamily: MONO, fontSize: 9, color: "#7c888e", transform: storyOpen ? "rotate(180deg)" : "rotate(0deg)", display: "inline-block" }}>▾</span>
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: "#7c888e", letterSpacing: ".05em", marginTop: 6 }}>{coords(roi.sar_bbox)}</div>
      {storyOpen && (
        <div style={{ fontFamily: SANS, fontSize: 11, lineHeight: 1.5, color: "#9aa8ae", marginTop: 9, paddingTop: 9, borderTop: `1px solid ${C.chromeLine}` }}>
          {roi.blurb}
        </div>
      )}
    </div>
  );
}
