import { useQuery } from "@tanstack/react-query";

import { apiGet } from "../api";
import { formatCountdown, useNow } from "../countdown";
import type { NextPass, Scene } from "../types";

type Props = {
  roi: string;
  scenes: Scene[];
  selectedSceneId: string | null;
  onSelect: (sceneId: string | null) => void;
};

export default function ScenePanel({ roi, scenes, selectedSceneId, onSelect }: Props) {
  const now = useNow();

  const nextPass = useQuery({
    queryKey: ["next-pass", roi],
    queryFn: () => apiGet<NextPass>("/analysis/next-pass", { roi }),
    // The backend caches this for 10 minutes; the countdown ticks locally.
    refetchInterval: 60_000,
  });

  const expected = nextPass.data?.next_expected_at ?? null;

  return (
    <section>
      <h2>SAR analyses</h2>
      <p className="countdown">
        {expected ? (
          <>
            next pass in <b>{formatCountdown(expected, now)}</b>
            <br />
            <span className="muted">
              {new Date(expected).toLocaleString()} — estimated from recent pass
              intervals. Analysis runs automatically once the imagery publishes,
              a few hours later.
            </span>
          </>
        ) : (
          <span className="muted">
            not enough recent passes over this region to estimate the next one
          </span>
        )}
      </p>
      <p className="muted">
        {nextPass.data?.latest_scene_sensed_at && (
          <>
            latest pass: {new Date(nextPass.data.latest_scene_sensed_at).toLocaleString()}
            <br />
          </>
        )}
        {nextPass.data?.last_processed_at
          ? <>last analyzed: {new Date(nextPass.data.last_processed_at).toLocaleString()}</>
          : <>no analysis run yet</>}
      </p>

      {scenes.length === 0 && <p className="muted">no analyzed scenes for this ROI</p>}
      <ul className="scene-list">
        {scenes.map((s) => (
          <li
            key={s.id}
            className={`scene-item ${s.id === selectedSceneId ? "selected" : ""}`}
            onClick={() => onSelect(s.id === selectedSceneId ? null : s.id)}
          >
            <b>{new Date(s.sensed_at).toLocaleString()}</b> · {s.platform} ·{" "}
            <span className={`status status-${s.status}`}>{s.status}</span>
            <br />
            {s.status === "processed" && (
              <span>
                {s.detection_count} detections, <b>{s.dark_count} dark</b>
                {s.indeterminate_count > 0 && <>, {s.indeterminate_count} unresolved</>}
                {/* A dark count without its noise floor is not a result. */}
                {s.chance_match_rate !== null && (
                  <>
                    <br />
                    <span className="muted">
                      false-match rate {(s.chance_match_rate * 100).toFixed(1)}%
                      {s.recall_large_total !== null && s.recall_large_total > 0 && (
                        <>
                          {" "}· large-vessel recall {s.recall_large_detected}/
                          {s.recall_large_total}
                        </>
                      )}
                    </span>
                  </>
                )}
              </span>
            )}
            {s.status === "failed" && <span className="error">{s.error}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
