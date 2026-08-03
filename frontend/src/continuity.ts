export const CONTINUITY_BINS = 30;
// AIS_RETENTION_DAYS defaults to 2, so 48 h is the whole queryable history.
export const CONTINUITY_WINDOW_MS = 48 * 3600_000;

export type Continuity = { bins: boolean[]; gapMs: number };

export function continuity(times: string[], nowMs: number): Continuity {
  const bins = new Array<boolean>(CONTINUITY_BINS).fill(false);
  const start = nowMs - CONTINUITY_WINDOW_MS;
  for (const t of times) {
    const i = Math.floor(((new Date(t).getTime() - start) / CONTINUITY_WINDOW_MS) * CONTINUITY_BINS);
    if (i >= 0 && i < CONTINUITY_BINS) bins[i] = true;
  }
  let run = 0;
  let longest = 0;
  for (const on of bins) {
    run = on ? 0 : run + 1;
    if (run > longest) longest = run;
  }
  return { bins, gapMs: (longest / CONTINUITY_BINS) * CONTINUITY_WINDOW_MS };
}

export function formatGap(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours}H${minutes % 60}M` : `${minutes}M`;
}
