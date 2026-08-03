import { contactId, contactState } from "./contactState";
import { C } from "./theme";
import type { Detection, Roi, Scene } from "./types";
import type { TickerItem } from "./components/ui";

function hhmm(iso: string): string {
  return new Date(iso).toISOString().slice(11, 16);
}

export function buildTicker(
  scene: Scene | null,
  detections: Detection[],
  roiLabel: string,
  mode: Roi["mode"],
): TickerItem[] {
  if (!scene) return [{ time: "--:--", text: "awaiting scene analysis", color: C.textMid }];

  const t = hhmm(scene.sensed_at);
  const items: TickerItem[] = [];

  if (mode === "survey") {
    for (const d of detections.slice(0, 5)) {
      items.push({
        time: t,
        text: `${contactId(d, mode)} new contact · ${(d.confidence * 100).toFixed(0)}% conf`,
        color: C.survey,
      });
    }
  } else {
    for (const d of detections.filter((x) => contactState(x, mode) === "dark").slice(0, 4)) {
      items.push({
        time: t,
        text: `${contactId(d, mode)} flagged dark · Δ ${d.dark_margin_m?.toFixed(0) ?? "—"} m`,
        color: C.dark,
      });
    }
  }

  items.push({
    time: scene.processed_at ? hhmm(scene.processed_at) : t,
    text: `Scene analysis complete · ${scene.detection_count} contacts`,
    color: C.match,
  });
  items.push({ time: t, text: `${roiLabel} pass ingested · ${scene.platform} / IW / VV`, color: C.textMid });
  return items;
}
