import { CircleMarker, ImageOverlay, Polygon, Popup } from "react-leaflet";

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
// `indeterminate` shares that amber: neither matched nor ruled out.
function markerColor(d: Detection, mode: Roi["mode"]): string {
  if (d.on_land) return "#7c3aed"; // masked: distinct from every vessel state
  if (mode === "survey") return "#f59e0b";
  if (d.match_state === "indeterminate") return "#f59e0b";
  if (d.is_dark === null) return "#9ca3af"; // unfused
  return d.is_dark ? "#dc2626" : "#16a34a";
}

function markerLabel(d: Detection, mode: Roi["mode"]): string {
  if (d.on_land) return "Land-masked (not a vessel)";
  if (mode === "survey") return "Observed vessel";
  if (d.match_state === "indeterminate") return "Unresolved vessel";
  if (d.is_dark === null) return "Unfused detection";
  return d.is_dark ? "DARK VESSEL" : "AIS match";
}

export default function SceneLayer({
  scene,
  mode,
  overlayOpacity,
  detections,
}: {
  scene: Scene;
  mode: Roi["mode"];
  overlayOpacity: number;
  detections: Detection[];
}) {
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
      {detections.map((d) => (
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
                {d.match_distance_m?.toFixed(0)} m from its dead-reckoned position
                {d.match_time_delta_s !== null && (
                  <>, from a fix {Math.abs(d.match_time_delta_s / 60).toFixed(1)} min away</>
                )}
              </>
            )}
            {/* The margin is what makes a dark call falsifiable. */}
            {d.is_dark === true && d.dark_margin_m !== null && (
              <>
                {d.dark_margin_m.toFixed(0)} m clear of every AIS vessel&rsquo;s
                dead-reckoned uncertainty
              </>
            )}
            {d.match_state === "indeterminate" && (
              <em>
                cannot be matched or ruled out — inside an AIS vessel&rsquo;s
                dead-reckoning uncertainty, or this scene failed its chance-match check
              </em>
            )}
          </Popup>
        </CircleMarker>
      ))}
    </>
  );
}
