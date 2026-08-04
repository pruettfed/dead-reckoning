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

/** Time since `pastIso`, coarsening as it recedes. Counterpart to
 * `formatCountdown` for timestamps that have already happened. */
export function formatAgo(pastIso: string, nowMs: number): string {
  const minutes = Math.floor((nowMs - new Date(pastIso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

/** `04 AUG 14:22Z`, optionally with seconds. */
export function utcStamp(at: string | number, withSeconds = false): string {
  const d = new Date(at);
  const p = (n: number) => String(n).padStart(2, "0");
  const secs = withSeconds ? `:${p(d.getUTCSeconds())}` : "";
  return `${p(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}${secs}Z`;
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
