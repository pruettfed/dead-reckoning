import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPost } from "../api";
import type { NextPass, Scene } from "../types";

type Props = {
  roi: string;
  scenes: Scene[];
  selectedSceneId: string | null;
  onSelect: (sceneId: string | null) => void;
};

export default function ScenePanel({ roi, scenes, selectedSceneId, onSelect }: Props) {
  const queryClient = useQueryClient();

  const nextPass = useQuery({
    queryKey: ["next-pass", roi],
    queryFn: () => apiGet<NextPass>("/analysis/next-pass", { roi }),
  });

  // Dev-only trigger: analysis spends the owner's PU budget, so the button only
  // exists when VITE_ANALYSIS_API_KEY is set (never set it in a deployed build).
  const analysisKey = import.meta.env.VITE_ANALYSIS_API_KEY as string | undefined;
  const trigger = useMutation({
    mutationFn: () =>
      apiPost<{ scene_id: string; status: string }>(`/analysis/${roi}`, {
        "X-Analysis-Key": analysisKey ?? "",
      }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["scenes", roi] }),
  });

  // Wipes every ROI's scenes + detections (AIS is kept), so refresh everything.
  const reset = useMutation({
    mutationFn: () =>
      apiDelete<{ scenes_deleted: number }>("/analyses", {
        "X-Analysis-Key": analysisKey ?? "",
      }),
    onSettled: () => queryClient.invalidateQueries(),
  });

  return (
    <section>
      <h2>SAR analyses</h2>
      <p className="muted">
        {nextPass.data?.latest_scene_sensed_at && (
          <>
            latest pass: {new Date(nextPass.data.latest_scene_sensed_at).toLocaleString()}
            <br />
          </>
        )}
        {nextPass.data?.next_expected_at && (
          <>
            next expected: {new Date(nextPass.data.next_expected_at).toLocaleString()}
            <br />
          </>
        )}
        {nextPass.data?.last_processed_at
          ? <>last analyzed: {new Date(nextPass.data.last_processed_at).toLocaleString()}</>
          : <>no analysis run yet</>}
      </p>

      {analysisKey && (
        <p>
          <button onClick={() => trigger.mutate()} disabled={trigger.isPending}>
            {trigger.isPending ? "Requesting…" : "Run analysis on latest pass"}
          </button>
          {trigger.isError && <span className="error"> {String(trigger.error.message)}</span>}
          <br />
          <button
            onClick={() => {
              if (
                window.confirm(
                  "Delete ALL SAR scenes and detections across every region? AIS data is kept. This can't be undone.",
                )
              ) {
                reset.mutate();
              }
            }}
            disabled={reset.isPending}
          >
            {reset.isPending ? "Resetting…" : "Reset all analyses"}
          </button>
          {reset.isError && <span className="error"> {String(reset.error.message)}</span>}
        </p>
      )}

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
              </span>
            )}
            {s.status === "failed" && <span className="error">{s.error}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
