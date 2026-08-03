import { useEffect, useRef } from "react";

import { MONO } from "../theme";
import type { Scene } from "../types";

type Props = { scene: Scene; lat: number; lon: number; color: string; label: string };

const CROP_FRACTION = 0.09;

export default function SarChip({ scene, lat, lon, color, label }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const cv = ref.current;
    const bbox = scene.imaged_bbox;
    if (!cv || !bbox || !scene.has_overview) return;
    const img = new Image();
    img.src = `/api/scenes/${scene.id}/overview.png`;
    img.onload = () => {
      const w = cv.clientWidth || 300;
      const h = cv.clientHeight || 168;
      cv.width = w * 2;
      cv.height = h * 2;
      const g = cv.getContext("2d");
      if (!g) return;
      g.setTransform(2, 0, 0, 2, 0, 0);
      const cx = ((lon - bbox[0]) / (bbox[2] - bbox[0])) * img.width;
      const cy = ((bbox[3] - lat) / (bbox[3] - bbox[1])) * img.height;
      const sw = img.width * CROP_FRACTION;
      const sh = sw * (h / w);
      g.imageSmoothingEnabled = false;
      g.fillStyle = "#05070a";
      g.fillRect(0, 0, w, h);
      g.drawImage(img, cx - sw / 2, cy - sh / 2, sw, sh, 0, 0, w, h);
      const bw = 34;
      const bh = 22;
      const bx = w / 2 - bw / 2;
      const by = h / 2 - bh / 2;
      g.strokeStyle = color;
      g.lineWidth = 1.5;
      g.strokeRect(bx, by, bw, bh);
      g.strokeStyle = color;
      g.globalAlpha = 0.5;
      g.lineWidth = 1;
      g.beginPath();
      g.moveTo(w / 2, 0);
      g.lineTo(w / 2, by);
      g.moveTo(w / 2, by + bh);
      g.lineTo(w / 2, h);
      g.stroke();
      g.globalAlpha = 1;
      g.fillStyle = color;
      g.font = `500 9px ${MONO}`;
      g.fillText(label, bx, by - 6);
    };
  }, [scene, lat, lon, color, label]);

  if (!scene.has_overview || !scene.imaged_bbox) return null;

  return (
    <div style={{ position: "relative", height: 168, flex: "none", background: "#05070a", borderBottom: "1px solid rgba(255,255,255,.09)" }}>
      <canvas ref={ref} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block" }} />
      <div style={{ position: "absolute", left: 10, top: 9, fontFamily: MONO, fontSize: 8.5, letterSpacing: ".12em", color: "rgba(230,240,245,.75)" }}>
        VV / SENTINEL-1 IW
      </div>
      <div style={{ position: "absolute", left: 10, bottom: 9, fontFamily: MONO, fontSize: 9, color: "rgba(230,240,245,.8)", letterSpacing: ".05em" }}>
        {lat.toFixed(4)}, {lon.toFixed(4)}
      </div>
    </div>
  );
}
