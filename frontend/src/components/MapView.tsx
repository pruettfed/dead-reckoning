import { ReactNode, useEffect } from "react";
import { MapContainer, Polygon, Rectangle, TileLayer, useMap, ZoomControl } from "react-leaflet";
import L from "leaflet";

import { C } from "../theme";
import type { Bbox, Roi } from "../types";

function bounds(b: Bbox): [[number, number], [number, number]] {
  return [[b[1], b[0]], [b[3], b[2]]];
}

function Recenter({ bbox }: { bbox: Bbox | null }) {
  const map = useMap();
  useEffect(() => {
    if (bbox) map.fitBounds(bounds(bbox), { padding: [24, 24] });
  }, [map, bbox]);
  return null;
}

// Everything outside the imaged box is unobserved, so it is dimmed rather than
// drawn as if it were surveyed.
function OutsideDim({ sar }: { sar: Bbox }) {
  const world: [number, number][] = [[-89, -179], [-89, 179], [89, 179], [89, -179]];
  const hole: [number, number][] = [[sar[1], sar[0]], [sar[3], sar[0]], [sar[3], sar[2]], [sar[1], sar[2]]];
  return (
    <Polygon
      positions={[world, hole]}
      pathOptions={{ color: "transparent", fillColor: C.map, fillOpacity: 0.62, interactive: false }}
    />
  );
}

function CornerMarks({ sar, color }: { sar: Bbox; color: string }) {
  const map = useMap();
  useEffect(() => {
    const specs: [number, number, string][] = [
      [sar[3], sar[0], "left:0;border-left:2px solid C;top:0;border-top:2px solid C"],
      [sar[3], sar[2], "right:0;border-right:2px solid C;top:0;border-top:2px solid C"],
      [sar[1], sar[0], "left:0;border-left:2px solid C;bottom:0;border-bottom:2px solid C"],
      [sar[1], sar[2], "right:0;border-right:2px solid C;bottom:0;border-bottom:2px solid C"],
    ];
    const group = L.layerGroup(
      specs.map(([lat, lon, css]) =>
        L.marker([lat, lon], {
          interactive: false,
          icon: L.divIcon({
            className: "",
            iconSize: [28, 28],
            iconAnchor: [14, 14],
            html: `<div style="position:relative;width:28px;height:28px"><div style="position:absolute;width:14px;height:14px;opacity:.85;${css.split("C").join(color)}"></div></div>`,
          }),
        }),
      ),
    ).addTo(map);
    return () => {
      group.remove();
    };
  }, [map, sar, color]);
  return null;
}

export default function MapView({ roi, children }: { roi: Roi | null; children: ReactNode }) {
  const accent = roi?.mode === "survey" ? C.survey : C.accent;
  return (
    <MapContainer center={[25.3, 121.8]} zoom={9} zoomControl={false} minZoom={3} maxZoom={15} style={{ position: "absolute", inset: 0 }}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        subdomains="abcd"
        maxZoom={19}
      />
      <ZoomControl position="bottomright" />
      <Recenter bbox={roi?.ais_bbox ?? null} />
      {roi && (
        <>
          <OutsideDim sar={roi.sar_bbox} />
          <Rectangle
            bounds={bounds(roi.sar_bbox)}
            pathOptions={{ color: accent, weight: 1, opacity: 0.55, dashArray: "3 7", fill: false, interactive: false }}
          />
          <CornerMarks sar={roi.sar_bbox} color={accent} />
        </>
      )}
      {children}
    </MapContainer>
  );
}
