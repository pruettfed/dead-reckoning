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
};

const Divider = () => <div style={{ width: 1, alignSelf: "stretch", background: "rgba(255,255,255,.09)" }} />;

export default function MapControls({ sar, onSar, showVessels, onVessels, hideSmall, onHideSmall, landCount, showLandMasked, onLandMasked, accent, hasOverlay }: Props) {
  return (
    <div style={{ display: "flex", background: C.glass, border: `1px solid ${C.lineStrong}`, backdropFilter: "blur(6px)", pointerEvents: "auto" }}>
      {hasOverlay && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px" }}>
            <span style={{ fontFamily: MONO, fontSize: 8.5, letterSpacing: ".16em", color: C.faint }}>IMAGERY OVERLAY</span>
            <Slider value={sar} onChange={onSar} accent={accent} />
            <span style={{ fontFamily: MONO, fontSize: 10, color: accent, minWidth: 30 }}>{sar}%</span>
          </div>
          <Divider />
        </>
      )}
      <Checkbox checked={showVessels} onChange={onVessels} label="Vessels" accent={accent} />
      <Divider />
      <Checkbox checked={hideSmall} onChange={onHideSmall} label="Hide small vessels" accent={accent} />
      {landCount > 0 && (
        <>
          <Divider />
          <Checkbox checked={showLandMasked} onChange={onLandMasked} label={`Show ${landCount} land-masked`} accent={accent} />
        </>
      )}
    </div>
  );
}
