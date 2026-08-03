import { useQuery } from "@tanstack/react-query";
import { Marker } from "react-leaflet";

import { apiGet } from "../api";
import { vesselIcon } from "./mapIcons";
import type { Vessel } from "../types";

// Same resolvable-hull range as LARGE_VESSEL_TYPE_MIN/MAX in fusion.py:
// passenger, cargo, tanker — the hulls 10 m/px resolves.
const LARGE_MIN = 60;
const LARGE_MAX = 89;

function isLarge(v: Vessel): boolean {
  return v.ship_type !== null && v.ship_type >= LARGE_MIN && v.ship_type <= LARGE_MAX;
}

type Props = {
  roi: string;
  at: string | null;
  hideSmallVessels: boolean;
  matchedMmsis: Set<number> | null;
  show: boolean;
  selectedMmsi: number | null;
  onSelect: (mmsi: number) => void;
  onData?: (vessels: Vessel[]) => void;
};

export default function VesselLayer({ roi, at, hideSmallVessels, matchedMmsis, show, selectedMmsi, onSelect, onData }: Props) {
  const vessels = useQuery({
    queryKey: ["vessels", roi, at],
    queryFn: async () => {
      const data = await apiGet<Vessel[]>("/vessels", at ? { roi, at } : { roi });
      onData?.(data);
      return data;
    },
    refetchInterval: at ? false : 15_000,
  });

  if (!show) return null;

  const visible = (vessels.data ?? []).filter((v) =>
    matchedMmsis ? matchedMmsis.has(v.mmsi) : !hideSmallVessels || isLarge(v),
  );

  return (
    <>
      {visible.map((v) => (
        <Marker
          key={v.mmsi}
          position={[v.lat, v.lon]}
          icon={vesselIcon(v.cog ?? 0, v.mmsi === selectedMmsi)}
          eventHandlers={{ click: () => onSelect(v.mmsi) }}
        />
      ))}
    </>
  );
}
