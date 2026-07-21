import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "./api";
import MapView from "./components/MapView";
import ScenePanel from "./components/ScenePanel";
import SceneLayer from "./components/SceneLayer";
import VesselLayer from "./components/VesselLayer";
import type { Health, Roi, Scene } from "./types";

export default function App() {
  // Default to the ROI with reliable AISStream coverage so the demo lands on live data
  const [roi, setRoi] = useState("singapore_strait");
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [overlayOpacity, setOverlayOpacity] = useState(0.75);
  // Off by default: masked hits are rocks and shore structures. On, they are the
  // only way to see whether LAND_MASK_BUFFER_M has started eating berthed ships.
  const [showLandMasked, setShowLandMasked] = useState(false);

  const rois = useQuery({ queryKey: ["rois"], queryFn: () => apiGet<Roi[]>("/rois") });

  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<Health>("/health"),
    refetchInterval: 30_000,
  });

  const scenes = useQuery({
    queryKey: ["scenes", roi],
    queryFn: () => apiGet<Scene[]>("/scenes", { roi }),
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.status === "processing") ? 5_000 : 30_000,
  });

  const roiObj = rois.data?.find((r) => r.name === roi) ?? null;
  const selectedScene = scenes.data?.find((s) => s.id === selectedSceneId) ?? null;
  // The scene IS the time control: selecting one freezes vessels at its acquisition time.
  const at = selectedScene?.sensed_at ?? null;

  const changeRoi = (name: string) => {
    setRoi(name);
    setSelectedSceneId(null);
  };

  return (
    <div className="app">
      <aside className="panel">
        <h1>Dead Reckoning</h1>
        <p className="muted">dark vessel detection — SAR × AIS</p>

        <label>
          ROI{" "}
          <select value={roi} onChange={(e) => changeRoi(e.target.value)}>
            {(rois.data ?? []).map((r) => (
              <option key={r.name} value={r.name}>
                {r.label}
              </option>
            ))}
          </select>
        </label>

        {roiObj?.mode === "survey" && (
          <p className="survey-note">
            <b>Survey region — no AIS coverage.</b> AISStream has no receivers here,
            so detections are reported as observed vessels. Nothing in this region
            can be called dark.
          </p>
        )}

        <p>
          {at ? (
            <>
              vessels as of {new Date(at).toLocaleString()}{" "}
              <button onClick={() => setSelectedSceneId(null)}>Live</button>
            </>
          ) : (
            <>vessels: live (15 s refresh)</>
          )}
        </p>

        <ScenePanel
          roi={roi}
          scenes={scenes.data ?? []}
          selectedSceneId={selectedSceneId}
          onSelect={setSelectedSceneId}
        />

        {selectedScene?.has_overview && (
          <label className="opacity-control">
            SAR imagery{" "}
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={overlayOpacity}
              onChange={(e) => setOverlayOpacity(Number(e.target.value))}
            />
          </label>
        )}

        {(selectedScene?.land_count ?? 0) > 0 && (
          <label className="land-mask-control">
            <input
              type="checkbox"
              checked={showLandMasked}
              onChange={(e) => setShowLandMasked(e.target.checked)}
            />{" "}
            Show {selectedScene?.land_count} land-masked
          </label>
        )}

        <section>
          <h2>Sources</h2>
          {health.data ? (
            <ul className="source-list">
              {Object.entries(health.data.sources).map(([name, s]) => (
                <li key={name}>
                  {name}: <span className={`status status-${s.state}`}>{s.state}</span>
                  {s.state !== "connected" && s.last_error && (
                    <span className="error"> — {s.last_error}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{health.isError ? "API unreachable" : "checking…"}</p>
          )}
        </section>
      </aside>

      <MapView roi={roiObj}>
        <VesselLayer key={roi} roi={roi} at={at} />
        {selectedScene && roiObj && (
          <SceneLayer
            scene={selectedScene}
            mode={roiObj.mode}
            overlayOpacity={overlayOpacity}
            showLandMasked={showLandMasked}
          />
        )}
      </MapView>
    </div>
  );
}
