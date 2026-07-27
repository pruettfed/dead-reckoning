import { useQuery } from "@tanstack/react-query";

import { apiGet } from "../api";
import { formatAgo, formatCountdown, useNow } from "../countdown";
import type { Schedule, ScheduleRow, ScheduleState } from "../types";

type Props = {
  roi: string;
  onSelectRoi: (name: string) => void;
};

const STATE_LABEL: Record<ScheduleState, string> = {
  analyzing: "analyzing now",
  awaiting_publication: "awaiting imagery",
  scheduled: "",
  unknown: "no estimate",
};

/** The server's state is up to a poll interval old, and the estimate keeps
 * expiring between polls. Re-derive the elapsed case locally so a row does not
 * sit on a countdown that has already run out. */
function displayState(row: ScheduleRow, now: number): ScheduleState {
  if (
    row.state === "scheduled" &&
    row.next_expected_at &&
    new Date(row.next_expected_at).getTime() <= now
  ) {
    return "awaiting_publication";
  }
  return row.state;
}

function whenColumn(row: ScheduleRow, state: ScheduleState, now: number): string {
  if (state === "analyzing") return "now";
  if (state === "scheduled" && row.next_expected_at) {
    return formatCountdown(row.next_expected_at, now);
  }
  return "—";
}

function lastAnalyzed(row: ScheduleRow, now: number): string {
  if (!row.last_processed_at) return "never analyzed";
  return `analyzed ${formatAgo(row.last_processed_at, now)}`;
}

export default function SchedulePanel({ roi, onSelectRoi }: Props) {
  const now = useNow();

  const schedule = useQuery({
    queryKey: ["schedule"],
    queryFn: () => apiGet<Schedule>("/analysis/schedule"),
    // The sweep behind this runs every 15 minutes; countdowns tick locally.
    refetchInterval: 60_000,
  });

  const regions = schedule.data?.regions ?? [];
  const recent = schedule.data?.most_recent ?? null;

  return (
    <section>
      <h2>Most recent analysis</h2>
      {recent ? (
        <div
          className={`schedule-item recent ${recent.roi === roi ? "selected" : ""}`}
          onClick={() => onSelectRoi(recent.roi)}
        >
          <span className="schedule-region">
            <b>{recent.label}</b>
            {recent.mode === "survey" && <span className="muted"> · survey</span>}
            <br />
            {recent.detection_count} detection
            {recent.detection_count === 1 ? "" : "s"}
            {/* Survey regions are never fused, so a dark count would be a claim
                the data cannot support. */}
            {recent.mode === "fused" && <>, <b>{recent.dark_count} dark</b></>}
            <br />
            <span className="muted">
              pass {new Date(recent.sensed_at).toLocaleString()} · analyzed{" "}
              {formatAgo(recent.processed_at, now)}
            </span>
          </span>
        </div>
      ) : (
        <p className="muted">
          {schedule.isPending ? "loading…" : "nothing analyzed yet"}
        </p>
      )}

      <h2>Upcoming analyses</h2>
      {regions.length === 0 ? (
        <p className="muted">
          {schedule.isPending
            ? "loading…"
            : "no schedule yet — the first sweep is still running"}
        </p>
      ) : (
        <ul className="schedule-list">
          {regions.map((r) => {
            const state = displayState(r, now);
            return (
              <li
                key={r.name}
                className={`schedule-item ${r.name === roi ? "selected" : ""}`}
                onClick={() => onSelectRoi(r.name)}
              >
                <span className="schedule-when">{whenColumn(r, state, now)}</span>
                <span className="schedule-region">
                  {r.label}
                  {r.mode === "survey" && <span className="muted"> · survey</span>}
                  <br />
                  <span className="muted">
                    {lastAnalyzed(r, now)}
                    {STATE_LABEL[state] && <> · {STATE_LABEL[state]}</>}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {schedule.data && (
        <p className="muted">
          {Math.round(schedule.data.month_to_date_pu)} of{" "}
          {Math.round(schedule.data.pu_monthly_ceiling)} processing units used this
          month
        </p>
      )}
    </section>
  );
}
