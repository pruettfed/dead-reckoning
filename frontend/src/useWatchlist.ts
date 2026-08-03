import { useState } from "react";

export type WatchEntry = { mmsi: number; name: string | null };

// Session-scoped today. Swapping this useState for a localStorage-backed one is
// the whole persistence feature.
export function useWatchlist() {
  const [entries, setEntries] = useState<WatchEntry[]>([]);

  return {
    entries,
    has: (mmsi: number) => entries.some((e) => e.mmsi === mmsi),
    toggle: (entry: WatchEntry) =>
      setEntries((prev) =>
        prev.some((e) => e.mmsi === entry.mmsi)
          ? prev.filter((e) => e.mmsi !== entry.mmsi)
          : [...prev, entry],
      ),
  };
}
