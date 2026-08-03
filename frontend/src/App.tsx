import { useEffect, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";

import { apiGet } from "./api";
import LeftRail from "./components/LeftRail";
import NextAcquisition from "./components/NextAcquisition";
import PassHistory from "./components/PassHistory";
import RegionList from "./components/RegionList";
import StatusBar from "./components/StatusBar";
import TopBar from "./components/TopBar";
import { HazardBar } from "./components/ui";
import { useNow } from "./countdown";
import { C, MONO, hexA } from "./theme";
import type { Health, Roi, Schedule, Scene } from "./types";

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
        vesselsAgo={vw < 1360 ? null : "live"}
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
        <div style={{ flex: 1, minWidth: 0, position: "relative", background: C.map }} />
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
