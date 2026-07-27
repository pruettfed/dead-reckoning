import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleMarker, Polyline, Popup } from "react-leaflet";

import { apiGet } from "../api";
import { navStatusLabel } from "../navStatus";
import { formatAge } from "../vesselAge";
import type { TrackPoint, Vessel } from "../types";

// Same resolvable-hull range as LARGE_VESSEL_TYPE_MIN/MAX in
// backend/app/fusion.py: passenger (60-69), cargo (70-79), tanker (80-89) —
// the hulls 10 m/px resolves. Unknown ship_type is treated as small.
const LARGE_VESSEL_TYPE_MIN = 60;
const LARGE_VESSEL_TYPE_MAX = 89;

function isLargeVessel(v: Vessel): boolean {
  return (
    v.ship_type !== null &&
    v.ship_type >= LARGE_VESSEL_TYPE_MIN &&
    v.ship_type <= LARGE_VESSEL_TYPE_MAX
  );
}

export default function VesselLayer({
  roi,
  at,
  hideSmallVessels,
  matchedMmsis,
}: {
  roi: string;
  at: string | null;
  hideSmallVessels: boolean;
  // Non-null once a scene is selected: takes over from hideSmallVessels so
  // every visible AIS marker pairs with a visible SAR detection.
  matchedMmsis: Set<number> | null;
}) {
  const [trackMmsi, setTrackMmsi] = useState<number | null>(null);

  const vessels = useQuery({
    queryKey: ["vessels", roi, at],
    queryFn: () => apiGet<Vessel[]>("/vessels", at ? { roi, at } : { roi }),
    refetchInterval: at ? false : 15_000,
  });

  const track = useQuery({
    queryKey: ["track", trackMmsi],
    queryFn: () => apiGet<TrackPoint[]>(`/vessels/${trackMmsi}/track`),
    enabled: trackMmsi !== null,
  });

  const visible = (vessels.data ?? []).filter((v) =>
    matchedMmsis ? matchedMmsis.has(v.mmsi) : !hideSmallVessels || isLargeVessel(v),
  );

  return (
    <>
      {visible.map((v) => {
        const status = navStatusLabel(v.nav_status);
        return (
          <CircleMarker
            key={v.mmsi}
            center={[v.lat, v.lon]}
            radius={5}
            pathOptions={{ color: "#2563eb", fillOpacity: 0.7 }}
          >
            <Popup>
              <b>{v.ship_name ?? "unknown vessel"}</b> · MMSI {v.mmsi}
              <br />
              SOG {v.sog ?? "–"} kn · COG {v.cog ?? "–"}°
              {status && (
                <>
                  <br />
                  {status}
                </>
              )}
              <br />
              {new Date(v.time).toLocaleString()}
              <br />
              {formatAge(v.time, at ? new Date(at).getTime() : Date.now(), at ? "scene" : "live")}
              <br />
              <button onClick={() => setTrackMmsi(trackMmsi === v.mmsi ? null : v.mmsi)}>
                {trackMmsi === v.mmsi ? "Hide track" : "Show track"}
              </button>
            </Popup>
          </CircleMarker>
        );
      })}
      {trackMmsi !== null && (track.data?.length ?? 0) > 1 && (
        <Polyline
          positions={track.data!.map((p) => [p.lat, p.lon])}
          pathOptions={{ color: "#2563eb", weight: 2, dashArray: "4 4" }}
        />
      )}
    </>
  );
}
