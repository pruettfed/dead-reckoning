import { C, MONO, hexA } from "../theme";
import type { Scene } from "../types";

type Props = { scene: Scene | null; accent: string; onClear: () => void };

export default function ViewTag({ scene, accent, onClear }: Props) {
  const color = scene ? accent : C.accent;
  return (
    <div style={{ display: "flex", flex: "none", alignItems: "center", gap: 10, padding: "6px 10px", background: hexA(color, 0.14), border: `1px solid ${hexA(color, 0.55)}`, backdropFilter: "blur(6px)", pointerEvents: "auto" }}>
      <span style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: ".12em", color: C.textHi, whiteSpace: "nowrap" }}>
        {scene ? "SCENE ANALYSIS" : "LIVE AIS"}
      </span>
      {scene && (
        <div onClick={onClear} style={{ cursor: "pointer", fontFamily: MONO, fontSize: 12, color: C.textDim, padding: "0 2px" }}>✕</div>
      )}
    </div>
  );
}
