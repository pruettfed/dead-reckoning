// Formats how stale an AIS position is, relative to either wall-clock now
// (live view) or a selected SAR scene's acquisition time (time-travel view).
// The query window is ± around the reference time, so scene mode can be
// either direction; live mode is always "ago" since a position can't be
// newer than the current moment.

function formatDuration(ms: number): string {
  const totalMinutes = Math.floor(ms / 60_000);
  if (totalMinutes < 1) return "under a minute";
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours === 0 ? `${minutes} min` : `${hours}h ${minutes}m`;
}

export function formatAge(
  vesselTimeIso: string,
  referenceMs: number,
  mode: "live" | "scene",
): string {
  const diffMs = referenceMs - new Date(vesselTimeIso).getTime();
  if (mode === "live") {
    return `reported ${formatDuration(Math.abs(diffMs))} ago`;
  }
  return diffMs >= 0
    ? `${formatDuration(diffMs)} before this pass`
    : `${formatDuration(Math.abs(diffMs))} after this pass`;
}
