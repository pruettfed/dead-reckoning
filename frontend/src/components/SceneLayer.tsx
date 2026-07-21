import { useQuery } from "@tanstack/react-query";
import { CircleMarker, ImageOverlay, Polygon, Popup } from "react-leaflet";

import { apiGet } from "../api";
import type { Bbox, Detection, Footprint, Roi, Scene } from "../types";

function toLatLng(ring: number[][]): [number, number][] {
  return ring.map(([lon, lat]) => [lat, lon]);
}

function footprintPositions(footprint: Footprint): [number, number][][] {
  if (footprint.type === "Polygon") {
    return (footprint.coordinates as number[][][]).map(toLatLng);
  }
  return (footprint.coordinates as number[][][][]).flatMap((polygon) => polygon.map(toLatLng));
}

function overlayBounds(bbox: Bbox): [[number, number], [number, number]] {
  return [
    [bbox[1], bbox[0]],
    [bbox[3], bbox[2]],
  ];
}

// Survey ROIs have no AIS to correlate against, so their detections are observed
// vessels — amber, never the red that asserts a vessel is running dark.
function markerColor(d: Detection, mode: Roi["mode"]): string {
  if (d.on_land) return "#7c3aed"; // masked: distinct from every vessel state
  if (mode === "survey") return "#f59e0b";
  if (d.is_dark === null) return "#9ca3af"; // unfused
  return d.is_dark ? "#dc2626" : "#16a34a";
}

function markerLabel(d: Detection, mode: Roi["mode"]): string {
  if (d.on_land) return "Land-masked (not a vessel)";
  if (mode === "survey") return "Observed vessel";
  if (d.is_dark === null) return "Unfused detection";
  return d.is_dark ? "DARK VESSEL" : "AIS match";
}

export default function SceneLayer({
  scene,
  mode,
  overlayOpacity,
  showLandMasked,
}: {
  scene: Scene;
  mode: Roi["mode"];
  overlayOpacity: number;
  showLandMasked: boolean;
}) {
  const detections = useQuery({
    queryKey: ["detections", scene.id, scene.status, showLandMasked],
    queryFn: () =>
      apiGet<Detection[]>(
        `/scenes/${scene.id}/detections${showLandMasked ? "?include_land=true" : ""}`,
      ),
    enabled: scene.status === "processed",
  });

  return (
    <>
      {scene.has_overview && scene.imaged_bbox && (
        // tilePane sits below overlayPane, so the radar frame stays under the
        // footprint outline and the detection markers.
        <ImageOverlay
          key={scene.id}
          url={`/api/scenes/${scene.id}/overview.png`}
          bounds={overlayBounds(scene.imaged_bbox)}
          opacity={overlayOpacity}
          pane="tilePane"
        />
      )}
      <Polygon
        positions={footprintPositions(scene.footprint)}
        pathOptions={{ color: "#64748b", weight: 1, fillOpacity: 0.05 }}
      />
      {(detections.data ?? []).map((d) => (
        <CircleMarker
          key={d.id}
          center={[d.lat, d.lon]}
          radius={6}
          pathOptions={{
            color: markerColor(d, mode),
            fillColor: markerColor(d, mode),
            fillOpacity: 0.9,
          }}
        >
          <Popup>
            <b>{markerLabel(d, mode)}</b>
            <br />
            confidence {(d.confidence * 100).toFixed(0)}% ({d.confidence_bucket})
            <br />
            {d.on_land && <em>inside the coastline mask — excluded from counts and fusion</em>}
            {!d.on_land && mode === "survey" && <em>no AIS coverage here — presence only</em>}
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
