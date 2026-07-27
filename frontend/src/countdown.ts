import { useEffect, useState } from "react";

/** Time until `targetIso`, coarsening as it recedes: seconds only matter when
 * the pass is imminent, and region intervals run to six days. */
export function formatCountdown(targetIso: string, nowMs: number): string {
  const seconds = Math.floor((new Date(targetIso).getTime() - nowMs) / 1000);
  if (seconds <= 0) return "any time now";

  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m ${seconds % 60}s`;
}

// Clock, re-rendering on an interval.
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
