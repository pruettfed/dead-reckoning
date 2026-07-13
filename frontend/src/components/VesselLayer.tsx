import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleMarker, Polyline, Popup } from "react-leaflet";

import { apiGet } from "../api";
import type { TrackPoint, Vessel } from "../types";

export default function VesselLayer({ roi, at }: { roi: string; at: string | null }) {
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

  return (
    <>
      {(vessels.data ?? []).map((v) => (
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
            <br />
            {new Date(v.time).toLocaleString()}
            <br />
            <button onClick={() => setTrackMmsi(trackMmsi === v.mmsi ? null : v.mmsi)}>
              {trackMmsi === v.mmsi ? "Hide track" : "Show track"}
            </button>
          </Popup>
        </CircleMarker>
      ))}
      {trackMmsi !== null && (track.data?.length ?? 0) > 1 && (
        <Polyline
          positions={track.data!.map((p) => [p.lat, p.lon])}
          pathOptions={{ color: "#2563eb", weight: 2, dashArray: "4 4" }}
        />
      )}
    </>
  );
}
