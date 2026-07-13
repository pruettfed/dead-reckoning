import { ReactNode, useEffect } from "react";
import { MapContainer, TileLayer, useMap } from "react-leaflet";

import type { Roi } from "../types";

function Recenter({ bbox }: { bbox: [number, number, number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (bbox) {
      map.fitBounds([
        [bbox[1], bbox[0]],
        [bbox[3], bbox[2]],
      ]);
    }
  }, [map, bbox]);
  return null;
}

export default function MapView({ roi, children }: { roi: Roi | null; children: ReactNode }) {
  return (
    <MapContainer center={[25.3, 56.8]} zoom={9} className="map">
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <Recenter bbox={roi?.bbox ?? null} />
      {children}
    </MapContainer>
  );
}
