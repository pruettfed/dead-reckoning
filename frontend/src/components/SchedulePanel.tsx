import { useQuery } from "@tanstack/react-query";

import { apiGet } from "../api";
import { formatCountdown, useNow } from "../countdown";
import type { Schedule, ScheduleRow } from "../types";

type Props = {
  roi: string;
  onSelectRoi: (name: string) => void;
};

const STATE_LABEL: Record<ScheduleRow["state"], string> = {
  analyzing: "analyzing now",
  awaiting_publication: "awaiting imagery",
  scheduled: "",
  unknown: "no estimate",
};

function whenColumn(row: ScheduleRow, now: number): string {
  if (row.state === "analyzing") return "now";
  if (row.state === "scheduled" && row.next_expected_at) {
    return formatCountdown(row.next_expected_at, now);
  }
  return "—";
}

function lastAnalyzed(row: ScheduleRow, now: number): string {
  if (!row.last_processed_at) return "never analyzed";
  const days = Math.floor((now - new Date(row.last_processed_at).getTime()) / 86_400_000);
  if (days < 1) return "analyzed today";
  return `analyzed ${days}d ago`;
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

  return (
    <section>
      <h2>Upcoming analyses</h2>
      {regions.length === 0 ? (
        <p className="muted">
          {schedule.isPending
            ? "loading…"
            : "no schedule yet — the first sweep is still running"}
        </p>
      ) : (
        <ul className="schedule-list">
          {regions.map((r) => (
            <li
              key={r.name}
              className={`schedule-item ${r.name === roi ? "selected" : ""}`}
              onClick={() => onSelectRoi(r.name)}
            >
              <span className="schedule-when">{whenColumn(r, now)}</span>
              <span className="schedule-region">
                {r.label}
                {r.mode === "survey" && <span className="muted"> · survey</span>}
                <br />
                <span className="muted">
                  {lastAnalyzed(r, now)}
                  {STATE_LABEL[r.state] && <> · {STATE_LABEL[r.state]}</>}
                </span>
              </span>
            </li>
          ))}
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
