import { ImageOverlay, Marker, Polygon } from "react-leaflet";

import { contactId, contactState } from "../contactState";
import { stateColor } from "../theme";
import { detectionIcon } from "./mapIcons";
import type { Bbox, Detection, Footprint, Roi, Scene } from "../types";

type Props = {
  scene: Scene;
  mode: Roi["mode"];
  detections: Detection[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  opacity: number;
};

function toLatLng(ring: number[][]): [number, number][] {
  return ring.map(([lon, lat]) => [lat, lon]);
}

function footprintPositions(f: Footprint): [number, number][][] {
  return f.type === "Polygon"
    ? (f.coordinates as number[][][]).map(toLatLng)
    : (f.coordinates as number[][][][]).flatMap((p) => p.map(toLatLng));
}

function overlayBounds(b: Bbox): [[number, number], [number, number]] {
  return [[b[1], b[0]], [b[3], b[2]]];
}

export default function DetectionLayer({ scene, mode, detections, selectedId, onSelect, opacity }: Props) {
  return (
    <>
      {scene.has_overview && scene.imaged_bbox && (
        <ImageOverlay
          key={scene.id}
          url={`/api/scenes/${scene.id}/overview.png`}
          bounds={overlayBounds(scene.imaged_bbox)}
          opacity={opacity}
          zIndex={250}
        />
      )}
      <Polygon
        positions={footprintPositions(scene.footprint)}
        pathOptions={{ color: "#64748b", weight: 1, fillOpacity: 0.04, interactive: false }}
      />
      {detections.map((d) => {
        const state = contactState(d, mode);
        const color = stateColor(state);
        const selected = d.id === selectedId;
        return (
          <Marker
            key={d.id}
            position={[d.lat, d.lon]}
            zIndexOffset={selected ? 1000 : 0}
            icon={detectionIcon({
              color,
              label: `${d.ship_name ?? contactId(d, mode)} / ${(d.confidence * 100).toFixed(0)}%`,
              selected,
              ring: state === "dark",
            })}
            eventHandlers={{ click: () => onSelect(d.id) }}
          />
        );
      })}
    </>
  );
}
