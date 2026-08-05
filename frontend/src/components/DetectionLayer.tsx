import { useMemo } from "react";
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

function DetectionMarker({
  d,
  mode,
  color,
  selected,
  ring,
  onSelect,
}: {
  d: Detection;
  mode: Roi["mode"];
  color: string;
  selected: boolean;
  ring: boolean;
  onSelect: (id: number) => void;
}) {
  const label = `${d.ship_name ?? contactId(d, mode)} / ${(d.confidence * 100).toFixed(0)}%`;
  // Rebuilding a Marker's icon on every tick (header clock / countdowns) restarts its
  // CSS animations — notably the dark-state dr-ring pulse — so memoize on the values
  // that actually change the icon's look.
  const icon = useMemo(() => detectionIcon({ color, label, selected, ring }), [color, label, selected, ring]);
  return (
    <Marker
      position={[d.lat, d.lon]}
      zIndexOffset={selected ? 1000 : 0}
      icon={icon}
      eventHandlers={{ click: () => onSelect(d.id) }}
    />
  );
}

export default function DetectionLayer({ scene, mode, detections, selectedId, onSelect, opacity }: Props) {
  return (
    <>
      {scene.has_overview && scene.imaged_bbox && (
        // In the tile pane it sits above the basemap but below every vector
        // layer, so AIS tracks and markers stay legible over the imagery.
        <ImageOverlay
          key={scene.id}
          pane="tilePane"
          url={`/api/scenes/${scene.id}/overview.png`}
          bounds={overlayBounds(scene.imaged_bbox)}
          opacity={opacity}
          zIndex={250}
          className="dr-sar-overlay"
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
          <DetectionMarker
            key={d.id}
            d={d}
            mode={mode}
            color={color}
            selected={selected}
            ring={state === "dark"}
            onSelect={onSelect}
          />
        );
      })}
    </>
  );
}
