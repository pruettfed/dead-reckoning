import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "./api";
import StatusBar from "./components/StatusBar";
import TopBar from "./components/TopBar";
import { HazardBar } from "./components/ui";
import { C, MONO } from "./theme";
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

  const roiObj = rois.data?.find((r) => r.name === roi) ?? null;
  const scene = scenes.data?.find((s) => s.id === sceneId) ?? null;

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

  const counts = {
    fused: (rois.data ?? []).filter((r) => r.mode === "fused").length,
    survey: (rois.data ?? []).filter((r) => r.mode === "survey").length,
  };

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column", background: C.bg, color: C.textMid, fontFamily: "var(--dr-sans)", overflow: "hidden" }}>
      <TopBar
        mode={mode}
        onMode={selectMode}
        counts={counts}
        health={health.data}
        vesselsAgo={vw < 1360 ? null : "live"}
        drawerOpen={drawerOpen}
        onDrawer={() => setDrawerOpen(!drawerOpen)}
        narrow={narrow}
        clock={clock}
      />

      <div style={{ flex: 1, display: "flex", minHeight: 0, position: "relative" }}>
        <div style={{ flex: 1, minWidth: 0, position: "relative", background: C.map }} />
      </div>

      <HazardBar color={C.match} height={9}>
        <span
          style={{
            background: "linear-gradient(90deg,rgba(10,11,12,0) 0%,rgba(10,11,12,.97) 22%,rgba(10,11,12,.97) 78%,rgba(10,11,12,0) 100%)",
            color: "rgba(60,200,120,.9)",
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
