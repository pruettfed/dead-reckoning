import { C } from "./theme";
import type { Schedule } from "./types";
import type { TickerItem } from "./components/ui";

function hhmm(iso: string | null): string {
  return iso ? new Date(iso).toISOString().slice(11, 16) : "--:--";
}

// Platform-wide activity, deliberately independent of the selected ROI: the
// rest of the status bar already switches with the region.
export function buildTicker(schedule: Schedule | undefined): TickerItem[] {
  if (!schedule) return [{ time: "--:--", text: "connecting to analysis scheduler", color: C.textMid }];

  const items: TickerItem[] = [];
  const recent = schedule.most_recent;
  if (recent) {
    const dark = recent.mode === "fused" ? ` · ${recent.dark_count} dark` : "";
    items.push({
      time: hhmm(recent.processed_at),
      text: `${recent.label} analysis complete · ${recent.detection_count} contacts${dark}`,
      color: recent.mode === "fused" && recent.dark_count > 0 ? C.dark : C.match,
    });
  }
  for (const r of schedule.regions.filter((x) => x.state === "analyzing")) {
    items.push({ time: hhmm(r.latest_scene_sensed_at), text: `${r.label} · analyzing pass`, color: C.amber });
  }
  for (const r of schedule.regions.filter((x) => x.state === "awaiting_publication").slice(0, 3)) {
    items.push({ time: hhmm(r.latest_scene_sensed_at), text: `${r.label} · awaiting product publication`, color: C.textMid });
  }
  const scheduled = schedule.regions.filter((x) => x.state === "scheduled").length;
  items.push({
    time: "--:--",
    text: `${scheduled}/${schedule.regions.length} regions scheduled for next pass`,
    color: C.textMid,
  });
  return items;
}
