import type { SourceHealth } from "./types";

export type PillState = "ok" | "warn" | "bad";

function findSource(sources: Record<string, SourceHealth>, needle: string): SourceHealth | undefined {
  return Object.entries(sources).find(([n]) => n.includes(needle))?.[1];
}

export function aisPill(sources: Record<string, SourceHealth>): {
  state: PillState;
  label: string;
  lastMessageAt: string | null;
} {
  const ais = findSource(sources, "ais");
  const lastMessageAt = ais?.last_message_at ?? null;
  if (ais?.state === "stale") return { state: "warn", label: "AIS STALE", lastMessageAt };
  if (ais?.state === "connected") return { state: "ok", label: "AIS LIVE", lastMessageAt };
  return { state: "bad", label: "AIS DOWN", lastMessageAt };
}

export function sarPill(sources: Record<string, SourceHealth>, healthLoaded: boolean): PillState {
  if (!healthLoaded) return "bad";
  const sar = findSource(sources, "sar");
  if (sar?.state === "error") return "bad";
  if (sar?.state === "degraded") return "warn";
  return "ok";
}
