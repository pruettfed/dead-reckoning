import { ReactNode, useEffect } from "react";
import { MapContainer, Rectangle, TileLayer, Tooltip, useMap } from "react-leaflet";

import type { Bbox, Roi } from "../types";

function toBounds(bbox: Bbox): [[number, number], [number, number]] {
  return [
    [bbox[1], bbox[0]],
    [bbox[3], bbox[2]],
  ];
}

function Recenter({ bbox }: { bbox: Bbox | null }) {
  const map = useMap();
  useEffect(() => {
    if (bbox) {
      map.fitBounds(toBounds(bbox));
    }
  }, [map, bbox]);
  return null;
}

/** The two boxes are deliberately different shapes — see rois.py. */
function RoiBoxes({ roi }: { roi: Roi }) {
  return (
    <>
      <Rectangle
        bounds={toBounds(roi.ais_bbox)}
        pathOptions={{ color: "#2563eb", weight: 1, dashArray: "6 4", fill: false }}
      >
        <Tooltip sticky>
          AIS subscription area
          {roi.mode === "survey" && " — no receiver coverage here"}
        </Tooltip>
      </Rectangle>
      <Rectangle
        bounds={toBounds(roi.sar_bbox)}
        pathOptions={{ color: "#7c3aed", weight: 2, fill: false }}
      >
        <Tooltip sticky>SAR imaged area</Tooltip>
      </Rectangle>
    </>
  );
}

export default function MapView({ roi, children }: { roi: Roi | null; children: ReactNode }) {
  return (
    <MapContainer center={[25.3, 56.8]} zoom={9} className="map">
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <Recenter bbox={roi?.ais_bbox ?? null} />
      {roi && <RoiBoxes roi={roi} />}
      {children}
    </MapContainer>
  );
}
