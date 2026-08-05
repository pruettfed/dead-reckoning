import { ReactNode, useEffect } from "react";
import { MapContainer, Rectangle, TileLayer, useMap, ZoomControl } from "react-leaflet";
import L from "leaflet";

import "../leafletSmoothWheelZoom";
import { C } from "../theme";
import type { Bbox, Roi } from "../types";

function bounds(b: Bbox): [[number, number], [number, number]] {
  return [[b[1], b[0]], [b[3], b[2]]];
}

// Panning is held to a generous margin around the ROI — wide enough to explore
// the surrounding water, tight enough that the scene never drifts off-screen.
function paddedBounds(b: Bbox): [[number, number], [number, number]] {
  const lonSpan = b[2] - b[0];
  const latSpan = b[3] - b[1];
  return [
    [b[1] - latSpan, b[0] - lonSpan],
    [b[3] + latSpan, b[2] + lonSpan],
  ];
}

// Recentering and the pan limit have to happen in this order, in one effect:
// fitBounds runs through _limitCenter, so recentering while the *previous*
// ROI's maxBounds is still set clamps the new region off-screen.
function FocusRoi({ roi }: { roi: Roi | null }) {
  const map = useMap();
  // Keyed on the box values, not the arrays themselves — /api/rois refetches on
  // window focus and hands back fresh identities, which would re-fit the map
  // and throw away wherever the user had panned to.
  const key = roi ? `${roi.sar_bbox.join()}|${roi.ais_bbox.join()}` : null;
  useEffect(() => {
    map.setMaxBounds(undefined);
    if (!roi) return;
    // Fit the imaged box, not the AIS box: the latter runs much wider up the
    // coast to reach the receivers, so fitting it leaves the scene off-centre.
    map.fitBounds(bounds(roi.sar_bbox), { padding: [24, 24], animate: false });
    map.setMaxBounds(paddedBounds(roi.ais_bbox));
  }, [map, key]);
  return null;
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
    <MapContainer
      center={[25.3, 121.8]}
      zoom={9}
      zoomControl={false}
      minZoom={5}
      maxZoom={20}
      zoomSnap={0}
      zoomDelta={0.5}
      // Native wheel-zoom is discrete (setZoomAround per tick, tiles/overlays
      // snap to the new integer zoom on 'zoomend'). leafletSmoothWheelZoom.ts
      // replaces it with a continuous requestAnimationFrame loop instead.
      scrollWheelZoom={false}
      smoothWheelZoom={true}
      smoothSensitivity={8}
      zoomAnimation={true}
      // Separate from the above, and the real strobe: every tile Leaflet loads
      // is forced to opacity 0 and faded back in over 200 ms, which against this
      // basemap reads as flashing through black.
      fadeAnimation={false}
      maxBoundsViscosity={0.8}
      style={{ position: "absolute", inset: 0 }}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        subdomains="abcd"
        // maxZoom is the map's own ceiling (20); maxNativeZoom is the highest
        // level CARTO actually serves (19) — Leaflet upscales those tiles past
        // it rather than finding no source and going blank.
        maxZoom={20}
        maxNativeZoom={19}
        // Retains tiles just outside the view instead of pruning and refetching
        // them on the next pan. Only helps ground already loaded — a new zoom
        // level's tiles have never been fetched, so this does nothing there.
        keepBuffer={4}
      />
      <ZoomControl position="bottomright" />
      <FocusRoi roi={roi} />
      {roi && (
        <>
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
