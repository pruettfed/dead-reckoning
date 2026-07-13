export type SourceHealth = {
  state: string;
  last_message_at: string | null;
  lag_seconds: number | null;
  connected_since: string | null;
  reconnect_count: number;
  error_count: number;
  last_error: string | null;
};

export type Health = { status: string; sources: Record<string, SourceHealth> };

export type Roi = {
  name: string;
  label: string;
  bbox: [number, number, number, number]; // min_lon, min_lat, max_lon, max_lat
};

export type Vessel = {
  mmsi: number;
  time: string;
  lat: number;
  lon: number;
  sog: number | null;
  cog: number | null;
  ship_name: string | null;
  ship_type: number | null;
  callsign: string | null;
};

export type TrackPoint = {
  time: string;
  lat: number;
  lon: number;
  sog: number | null;
  cog: number | null;
  ship_name: string | null;
  ship_type: number | null;
  callsign: string | null;
};

export type Footprint = {
  type: "Polygon" | "MultiPolygon";
  coordinates: number[][][] | number[][][][];
};

export type Scene = {
  id: string;
  name: string;
  roi: string;
  sensed_at: string;
  platform: string;
  status: "processing" | "processed" | "failed";
  processed_at: string | null;
  error: string | null;
  footprint: Footprint;
  detection_count: number;
  dark_count: number;
};

export type Detection = {
  id: number;
  lat: number;
  lon: number;
  confidence: number;
  confidence_bucket: "high" | "medium" | "low";
  is_dark: boolean | null;
  matched_mmsi: number | null;
  match_distance_m: number | null;
  match_time_delta_s: number | null;
  ship_name: string | null;
  ship_type: number | null;
  callsign: string | null;
};

export type NextPass = {
  latest_scene_sensed_at: string | null;
  next_expected_at: string | null;
  last_processed_at: string | null;
};
