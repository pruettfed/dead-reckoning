import { useQuery } from "@tanstack/react-query";
import { CircleMarker, Polygon, Popup } from "react-leaflet";

import { apiGet } from "../api";
import type { Detection, Footprint, Scene } from "../types";

function toLatLng(ring: number[][]): [number, number][] {
  return ring.map(([lon, lat]) => [lat, lon]);
}

function footprintPositions(footprint: Footprint): [number, number][][] {
  if (footprint.type === "Polygon") {
    return (footprint.coordinates as number[][][]).map(toLatLng);
  }
  return (footprint.coordinates as number[][][][]).flatMap((polygon) => polygon.map(toLatLng));
}

function markerColor(d: Detection): string {
  if (d.is_dark === null) return "#9ca3af"; // unfused
  return d.is_dark ? "#dc2626" : "#16a34a";
}

export default function SceneLayer({ scene }: { scene: Scene }) {
  const detections = useQuery({
    queryKey: ["detections", scene.id, scene.status],
    queryFn: () => apiGet<Detection[]>(`/scenes/${scene.id}/detections`),
    enabled: scene.status === "processed",
  });

  return (
    <>
      <Polygon
        positions={footprintPositions(scene.footprint)}
        pathOptions={{ color: "#64748b", weight: 1, fillOpacity: 0.05 }}
      />
      {(detections.data ?? []).map((d) => (
        <CircleMarker
          key={d.id}
          center={[d.lat, d.lon]}
          radius={6}
          pathOptions={{ color: markerColor(d), fillColor: markerColor(d), fillOpacity: 0.9 }}
        >
          <Popup>
            <b>{d.is_dark === null ? "Unfused detection" : d.is_dark ? "DARK VESSEL" : "AIS match"}</b>
            <br />
            confidence {(d.confidence * 100).toFixed(0)}% ({d.confidence_bucket})
            <br />
            {d.matched_mmsi !== null && (
              <>
                matched: {d.ship_name ?? "unknown"} (MMSI {d.matched_mmsi})
                <br />
                {d.match_distance_m?.toFixed(0)} m away
                {d.match_time_delta_s !== null && <>, {(d.match_time_delta_s / 60).toFixed(0)} min offset</>}
              </>
            )}
          </Popup>
        </CircleMarker>
      ))}
    </>
  );
}
