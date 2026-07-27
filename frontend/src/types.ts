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

export type Bbox = [number, number, number, number]; // min_lon, min_lat, max_lon, max_lat

export type Roi = {
  name: string;
  label: string;
  ais_bbox: Bbox; // subscribed on AISStream — free, so kept wide and coastal
  sar_bbox: Bbox; // imaged and clipped to — costs PU, so kept small and on water
  // "survey" regions have no AIS coverage: detections there are observed
  // vessels, never "dark".
  mode: "fused" | "survey";
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
  // Raw ITU-R M.1371 code (0-15) or null; see navStatusLabel for display.
  nav_status: number | null;
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
  imaged_bbox: Bbox | null; // the rectangle pixels were fetched for
  has_overview: boolean;
  detection_count: number; // excludes land-masked hits
  dark_count: number;
  indeterminate_count: number;
  land_count: number;
  // Noise floor the dark count is measured against; null for survey ROIs.
  chance_match_rate: number | null;
  // Recall against AIS-confirmed large vessels underway in the footprint.
  recall_large_total: number | null;
  recall_large_detected: number | null;
};

export type Detection = {
  id: number;
  lat: number;
  lon: number;
  confidence: number;
  confidence_bucket: "high" | "medium" | "low";
  match_state: "matched" | "dark" | "indeterminate" | null;
  // `match_state` narrowed for counting: null for indeterminate.
  is_dark: boolean | null;
  // Fell inside the coastline mask — a rock or shore structure, not a vessel.
  // Only ever present when the request asked for masked hits.
  on_land: boolean;
  matched_mmsi: number | null;
  match_distance_m: number | null; // from the vessel's dead-reckoned position
  match_time_delta_s: number | null; // signed age of the AIS fix used
  // Nearest AIS vessel by dead-reckoned position when the detection is
  // indeterminate (neither confidently matched nor ruled dark); null
  // otherwise.
  candidate_mmsi: number | null;
  // Metres outside the nearest vessel's uncertainty envelope; negative = inside.
  dark_margin_m: number | null;
  ship_name: string | null;
  ship_type: number | null;
  callsign: string | null;
};

export type NextPass = {
  latest_scene_sensed_at: string | null;
  next_expected_at: string | null;
  last_processed_at: string | null;
};

// analyzing        — a fetch/detect/fuse run is in flight right now
// awaiting_publication — the expected pass time has gone by; CDSE publishes GRD
//                    products hours after acquisition, so the wait is normal
// scheduled        — pass still ahead
// unknown          — fewer than three recent passes, so no interval to project
export type ScheduleState =
  | "analyzing"
  | "awaiting_publication"
  | "scheduled"
  | "unknown";

export type ScheduleRow = {
  name: string;
  label: string;
  mode: "fused" | "survey";
  latest_scene_sensed_at: string | null;
  // Median interval between recent passes rolled forward — an estimate from the
  // catalog, not an orbit prediction. Null below three passes.
  next_expected_at: string | null;
  last_processed_at: string | null;
  state: ScheduleState;
};

export type Schedule = {
  // Empty until the scheduler's first sweep lands, or while it is disabled.
  regions: ScheduleRow[];
  month_to_date_pu: number;
  pu_monthly_ceiling: number;
};
