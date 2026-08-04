import { C, MONO } from "../theme";
import { Checkbox, Slider } from "./ui";

type Props = {
  sar: number;
  onSar: (v: number) => void;
  showVessels: boolean;
  onVessels: () => void;
  hideSmall: boolean;
  onHideSmall: () => void;
  landCount: number;
  showLandMasked: boolean;
  onLandMasked: () => void;
  accent: string;
  hasOverlay: boolean;
  survey: boolean;
  compact: boolean;
};

const Divider = () => <div style={{ width: 1, alignSelf: "stretch", background: "rgba(255,255,255,.09)" }} />;

export default function MapControls({ sar, onSar, showVessels, onVessels, hideSmall, onHideSmall, landCount, showLandMasked, onLandMasked, accent, hasOverlay, survey, compact }: Props) {
  return (
    <div style={{ display: "flex", background: C.glass, border: `1px solid ${C.lineStrong}`, backdropFilter: "blur(6px)", pointerEvents: "auto" }}>
      {hasOverlay && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: compact ? 8 : 12, padding: compact ? "7px 11px" : "11px 16px" }}>
            <span style={{ fontFamily: MONO, fontSize: compact ? 7.5 : 8.5, letterSpacing: ".16em", color: C.faint }}>
              {compact ? "SAR" : "IMAGERY OVERLAY"}
            </span>
            <Slider value={sar} onChange={onSar} accent={accent} width={compact ? 62 : 100} />
            <span style={{ fontFamily: MONO, fontSize: compact ? 9 : 10, color: accent, minWidth: compact ? 26 : 30 }}>{sar}%</span>
          </div>
          <Divider />
        </>
      )}
      {/* Survey regions have no AIS, so there are no vessels to toggle. */}
      {!survey && (
        <>
          <Checkbox checked={showVessels} onChange={onVessels} label="Vessels" accent={accent} compact={compact} />
          <Divider />
          <Checkbox checked={hideSmall} onChange={onHideSmall} label="Hide small vessels" accent={accent} compact={compact} />
        </>
      )}
      {landCount > 0 && (
        <>
          {!survey && <Divider />}
          <Checkbox checked={showLandMasked} onChange={onLandMasked} label={`Show ${landCount} land-masked`} accent={accent} compact={compact} />
        </>
      )}
    </div>
  );
}
