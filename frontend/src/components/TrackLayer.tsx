import { useQuery } from "@tanstack/react-query";
import { CircleMarker, Polyline } from "react-leaflet";

import { apiGet } from "../api";
import type { TrackPoint } from "../types";

export default function TrackLayer({ mmsi, color }: { mmsi: number; color: string }) {
  const track = useQuery({
    queryKey: ["track", mmsi],
    queryFn: () => apiGet<TrackPoint[]>(`/vessels/${mmsi}/track`, { hours: "48" }),
  });

  const pts = track.data ?? [];
  if (pts.length < 2) return null;

  return (
    <>
      <Polyline
        positions={pts.map((p) => [p.lat, p.lon])}
        pathOptions={{ color, weight: 1.2, opacity: 0.75, dashArray: "5 5", interactive: false }}
      />
      {pts.slice(0, -1).map((p, i) => (
        <CircleMarker
          key={p.time}
          center={[p.lat, p.lon]}
          radius={3}
          pathOptions={{ stroke: false, fillColor: color, fillOpacity: 0.25 + (i / pts.length) * 0.5, interactive: false }}
        />
      ))}
    </>
  );
}
