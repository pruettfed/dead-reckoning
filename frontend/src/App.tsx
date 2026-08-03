import { useEffect, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { apiGet } from "./api";
import ContactList from "./components/ContactList";
import DetectionLayer from "./components/DetectionLayer";
import LeftRail from "./components/LeftRail";
import Legend from "./components/Legend";
import MapControls from "./components/MapControls";
import MapView from "./components/MapView";
import NextAcquisition from "./components/NextAcquisition";
import PassHistory from "./components/PassHistory";
import RegionList from "./components/RegionList";
import RightRail from "./components/RightRail";
import RoiBanner from "./components/RoiBanner";
import StatusBar from "./components/StatusBar";
import TopBar from "./components/TopBar";
import TrackLayer from "./components/TrackLayer";
import { HazardBar } from "./components/ui";
import VesselLayer from "./components/VesselLayer";
import ViewTag from "./components/ViewTag";
import Watchlist from "./components/Watchlist";
import { contactMmsi, contactState } from "./contactState";
import { formatAgo, useNow } from "./countdown";
import { C, MONO, hexA, stateColor } from "./theme";
import { useWatchlist } from "./useWatchlist";
import type { Detection, Health, Roi, Schedule, Scene, Vessel } from "./types";

export type Selection = { kind: "det" | "ais"; id: number } | null;

function useViewportWidth(): number {
  const [vw, setVw] = useState(() => window.innerWidth);
  useEffect(() => {
    const on = () => setVw(window.innerWidth);
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return vw;
}

function useClock(): string {
  const [clock, setClock] = useState(() => new Date().toISOString().slice(11, 19) + "Z");
  useEffect(() => {
    const id = setInterval(() => setClock(new Date().toISOString().slice(11, 19) + "Z"), 1000);
    return () => clearInterval(id);
  }, []);
  return clock;
}

export default function App() {
  const [mode, setMode] = useState<"fused" | "survey">("fused");
  const [roi, setRoi] = useState("north_taiwan");
  const [sceneId, setSceneId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [passExpanded, setPassExpanded] = useState(false);
  const [selected, setSelected] = useState<Selection>(null);
  const [sar, setSar] = useState(72);
  const [showVessels, setShowVessels] = useState(true);
  const [hideSmallVessels, setHideSmallVessels] = useState(true);
  const [showLandMasked, setShowLandMasked] = useState(false);
  const [storyOpen, setStoryOpen] = useState(false);
  const [liveVessels, setLiveVessels] = useState<Vessel[]>([]);
  const [vesselsAt, setVesselsAt] = useState<number | null>(null);
  // Getter unused until Task 10 wires the Dossier's DETAIL/HISTORY toggle.
  const [, setTab] = useState<"DETAIL" | "HISTORY">("DETAIL");
  const watchlist = useWatchlist();

  const vw = useViewportWidth();
  const clock = useClock();
  const narrow = vw < 980;

  const rois = useQuery({ queryKey: ["rois"], queryFn: () => apiGet<Roi[]>("/rois") });
  const health = useQuery({ queryKey: ["health"], queryFn: () => apiGet<Health>("/health"), refetchInterval: 30_000 });
  const schedule = useQuery({ queryKey: ["schedule"], queryFn: () => apiGet<Schedule>("/analysis/schedule"), refetchInterval: 60_000 });
  const scenes = useQuery({
    queryKey: ["scenes", roi],
    queryFn: () => apiGet<Scene[]>("/scenes", { roi }),
    refetchInterval: (q) => (q.state.data?.some((s) => s.status === "processing") ? 5_000 : 30_000),
  });

  const now = useNow();
  const fusedRois = (rois.data ?? []).filter((r) => r.mode === "fused");
  const countQueries = useQueries({
    queries: fusedRois.map((r) => ({
      queryKey: ["vessel-count", r.name],
      queryFn: () => apiGet<{ count: number }>("/vessels/count", { roi: r.name }),
      refetchInterval: 30_000,
    })),
  });
  const counts = Object.fromEntries(
    fusedRois.map((r, i) => [r.name, countQueries[i]?.data?.count]),
  );

  const roiObj = rois.data?.find((r) => r.name === roi) ?? null;
  const scene = scenes.data?.find((s) => s.id === sceneId) ?? null;
  const survey = roiObj?.mode === "survey";
  const modeColor = survey ? C.survey : C.accent;

  const detections = useQuery({
    queryKey: ["detections", scene?.id, showLandMasked],
    queryFn: () => apiGet<Detection[]>(`/scenes/${scene!.id}/detections${showLandMasked ? "?include_land=true" : ""}`),
    enabled: scene?.status === "processed",
  });

  const at = scene?.sensed_at ?? null;

  // Once a scene is selected, the AIS layer narrows to vessels that pair with a
  // visible detection, so every marker on screen has a counterpart.
  const matchedMmsis =
    roiObj?.mode === "fused" && scene?.status === "processed"
      ? new Set(
          (detections.data ?? [])
            .map((d) => d.matched_mmsi ?? (d.match_state === "indeterminate" ? d.candidate_mmsi : null))
            .filter((m): m is number => m !== null),
        )
      : null;

  const selectedDet = selected?.kind === "det" ? (detections.data ?? []).find((d) => d.id === selected.id) ?? null : null;

  // Mirrors VesselLayer's own filter so the rail lists the same vessels shown on the map.
  const railVessels = liveVessels.filter((v) =>
    matchedMmsis ? matchedMmsis.has(v.mmsi) : !hideSmallVessels || (v.ship_type !== null && v.ship_type >= 60 && v.ship_type <= 89),
  );

  const selectRoi = (name: string) => {
    setRoi(name);
    setSceneId(null);
  };

  const selectMode = (m: "fused" | "survey") => {
    if (m === mode) return;
    setMode(m);
    const first = (rois.data ?? []).find((r) => r.mode === m);
    if (first) selectRoi(first.name);
  };

  const modeCounts = {
    fused: (rois.data ?? []).filter((r) => r.mode === "fused").length,
    survey: (rois.data ?? []).filter((r) => r.mode === "survey").length,
  };

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: C.bg, color: C.textMid, fontFamily: "var(--dr-sans)", overflow: "hidden" }}>
      <TopBar
        mode={mode}
        onMode={selectMode}
        counts={modeCounts}
        health={health.data}
        vesselsAgo={vw < 1360 || vesselsAt === null ? null : formatAgo(new Date(vesselsAt).toISOString(), now)}
        drawerOpen={drawerOpen}
        onDrawer={() => setDrawerOpen(!drawerOpen)}
        narrow={narrow}
        clock={clock}
      />

      <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
        <LeftRail width={vw < 1180 ? 218 : 270}>
          <RegionList
            rois={rois.data ?? []}
            mode={mode}
            selected={roi}
            onSelect={selectRoi}
            schedule={schedule.data}
            counts={counts}
            now={now}
          />
          <PassHistory
            roiLabel={roiObj?.label ?? ""}
            scenes={scenes.data ?? []}
            selectedId={sceneId}
            onSelect={(id) => setSceneId(id === sceneId ? null : id)}
            expanded={passExpanded}
            onToggleExpand={() => setPassExpanded(!passExpanded)}
            accent={modeColor}
            survey={survey}
          />
          <NextAcquisition roi={roi} roiLabel={roiObj?.label ?? ""} accent={modeColor} now={now} />
        </LeftRail>
        <div style={{ flex: 1, minWidth: 0, position: "relative", background: C.map }}>
          <MapView roi={roiObj}>
            <VesselLayer
              key={roi}
              roi={roi}
              at={at}
              hideSmallVessels={hideSmallVessels}
              matchedMmsis={matchedMmsis}
              show={showVessels}
              selectedMmsi={selected?.kind === "ais" ? selected.id : null}
              onSelect={(mmsi) => setSelected({ kind: "ais", id: mmsi })}
              onData={(v) => { setLiveVessels(v); setVesselsAt(Date.now()); }}
            />
            {scene && roiObj && (
              <DetectionLayer
                scene={scene}
                mode={roiObj.mode}
                detections={detections.data ?? []}
                selectedId={selected?.kind === "det" ? selected.id : null}
                onSelect={(id) => setSelected({ kind: "det", id })}
                opacity={sar / 100}
              />
            )}
            {selected && (() => {
              const trackMmsi = selected.kind === "ais" ? selected.id : (selectedDet ? contactMmsi(selectedDet) : null);
              if (trackMmsi === null) return null;
              return (
                <TrackLayer
                  mmsi={trackMmsi}
                  color={selected.kind === "ais" ? C.accent : stateColor(contactState(selectedDet!, roiObj!.mode))}
                />
              );
            })()}
          </MapView>

          <div style={{ position: "absolute", left: 18, right: 18, top: 18, zIndex: 640, display: "flex", flexWrap: "wrap", alignItems: "flex-start", justifyContent: "space-between", gap: 12, pointerEvents: "none" }}>
            {roiObj && (
              <RoiBanner roi={roiObj} accent={modeColor} storyOpen={storyOpen} onToggleStory={() => setStoryOpen(!storyOpen)} />
            )}
            <ViewTag scene={scene} accent={modeColor} onClear={() => { setSceneId(null); setSelected(null); }} />
          </div>

          <div style={{ position: "absolute", left: 18, right: 58, bottom: 18, zIndex: 600, display: "flex", flexWrap: "wrap", alignItems: "flex-end", justifyContent: "space-between", gap: 12, pointerEvents: "none" }}>
            <MapControls
              sar={sar}
              onSar={setSar}
              showVessels={showVessels}
              onVessels={() => setShowVessels(!showVessels)}
              hideSmall={hideSmallVessels}
              onHideSmall={() => setHideSmallVessels(!hideSmallVessels)}
              landCount={scene?.land_count ?? 0}
              showLandMasked={showLandMasked}
              onLandMasked={() => setShowLandMasked(!showLandMasked)}
              accent={modeColor}
              hasOverlay={!!scene?.has_overview}
            />
            <Legend survey={survey} showVessels={showVessels} showLandMasked={showLandMasked} />
          </div>
        </div>

        <RightRail
          width={narrow ? 264 : vw < 1180 ? 224 : 276}
          narrow={narrow}
          open={!narrow || drawerOpen}
          title={scene ? (survey ? "SURVEY CONTACTS" : "SCENE CONTACTS") : "LIVE AIS TRACKS"}
          count={String(scene ? (detections.data ?? []).length : railVessels.length)}
          footer={<Watchlist entries={watchlist.entries} />}
        >
          <ContactList
            mode={roiObj?.mode ?? "fused"}
            detections={detections.data ?? []}
            vessels={railVessels}
            sceneSelected={!!scene}
            selected={selected}
            onSelect={(s) => { setSelected(s); setTab("DETAIL"); if (narrow) setDrawerOpen(false); }}
          />
        </RightRail>
      </div>

      <HazardBar color={C.match} height={9}>
        <span
          style={{
            background: `linear-gradient(90deg,${hexA(C.bg, 0)} 0%,${hexA(C.bg, 0.97)} 22%,${hexA(C.bg, 0.97)} 78%,${hexA(C.bg, 0)} 100%)`,
            color: hexA(C.match, 0.9),
            fontFamily: MONO,
            fontSize: 7.5,
            letterSpacing: ".22em",
            padding: "0 20px",
            whiteSpace: "nowrap",
          }}
        >
          UNCLASSIFIED
        </span>
      </HazardBar>

      <StatusBar
        roiLabel={roiObj?.label ?? "—"}
        stats={[
          { k: "CONTACTS", v: scene ? String(scene.detection_count) : "—", color: C.text },
          { k: "MASKED", v: scene ? String(scene.land_count) : "—", color: C.textMid },
          { k: "FALSE MATCH", v: scene?.chance_match_rate != null ? `${(scene.chance_match_rate * 100).toFixed(1)}%` : "—", color: scene?.chance_match_rate != null ? C.unres : C.faint },
          { k: "RECALL", v: scene?.recall_large_total ? `${scene.recall_large_detected}/${scene.recall_large_total}` : "—", color: scene?.recall_large_total ? C.match : C.faint },
          { k: "PU", v: schedule.data ? `${Math.round(schedule.data.month_to_date_pu)} / ${Math.round(schedule.data.pu_monthly_ceiling)}` : "—", color: C.amber },
        ]}
        ticker={[{ time: "--:--", text: "awaiting scene analysis", color: C.textMid }]}
      />
    </div>
  );
}
