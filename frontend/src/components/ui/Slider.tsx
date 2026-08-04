import { CSSProperties } from "react";

type Props = { value: number; onChange: (v: number) => void; accent: string; width?: number };

export default function Slider({ value, onChange, accent, width = 100 }: Props) {
  return (
    <input
      type="range"
      min={0}
      max={100}
      step={5}
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="dr-slider"
      style={{
        width,
        background: `linear-gradient(90deg, ${accent} 0%, ${accent} ${value}%, rgba(255,255,255,.15) ${value}%, rgba(255,255,255,.15) 100%)`,
        "--thumb-color": accent,
      } as CSSProperties}
    />
  );
}
