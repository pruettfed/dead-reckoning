// Fixture data for stub mode (VITE_STUB=true). Illustrative, not a mirror of
// production: the regions use real bounding boxes so the map lands on real
// water, but the blurbs are short stand-ins and the vessels are invented.
//
// Every timestamp is derived from `now` at request time, never hardcoded — a
// fixed date would read "2 years ago" by next year and make the console look
// broken rather than stubbed.

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

const iso = (ms: number) => new Date(ms).toISOString();

type Bbox = [number, number, number, number];

type StubRoi = {
  name: string;
  label: string;
  ais_bbox: Bbox;
  sar_bbox: Bbox;
  mode: "fused" | "survey";
  passes_per_month: number;
  blurb: string;
};

export const ROIS: StubRoi[] = [
  {
    name: "north_taiwan",
    label: "North Taiwan",
    ais_bbox: [120.7, 24.9, 122.4, 26.3],
    sar_bbox: [120.9, 24.95, 122.2, 25.6],
    mode: "fused",
    passes_per_month: 7,
    blurb: "Stub data. Approaches to Taiwan's northern ports, where traffic is dense and AIS coverage is good.",
  },
  {
    name: "gulf_of_finland",
    label: "Gulf of Finland",
    ais_bbox: [24.5, 59.2, 28.6, 60.4],
    sar_bbox: [25.2, 59.45, 27.6, 60.28],
    mode: "fused",
    passes_per_month: 20,
    blurb: "Stub data. Tanker traffic out of the Baltic, a route with a long history of switched-off transponders.",
  },
  {
    name: "skagen_kattegat",
    label: "Skagen Anchorage",
    ais_bbox: [9.85, 57.15, 11.95, 58.45],
    sar_bbox: [10.0, 57.4, 11.6, 58.2],
    mode: "fused",
    passes_per_month: 20,
    blurb: "Stub data. A busy anchorage where ships wait, transfer cargo, and occasionally go quiet.",
  },
  {
    name: "hormuz_strait",
    label: "Strait of Hormuz",
    ais_bbox: [55.65, 26.0, 56.95, 27.0],
    sar_bbox: [55.95, 26.15, 56.85, 26.85],
    mode: "survey",
    passes_per_month: 10,
    blurb: "Stub data. No shore receivers reach this far out, so contacts here are observed vessels, never dark.",
  },
  {
    name: "kerch_strait",
    label: "Kerch Strait",
    ais_bbox: [36.3, 45.0, 36.8, 45.5],
    sar_bbox: [36.35, 45.05, 36.75, 45.45],
    mode: "survey",
    passes_per_month: 10,
    blurb: "Stub data. A contested chokepoint with no usable AIS ground truth.",
  },
];

const ROI_BY_NAME = new Map(ROIS.map((r) => [r.name, r]));

function footprint([w, s, e, n]: Bbox) {
  // Deliberately wider than the sar_bbox, as a real swath is.
  const pad = 0.25;
  return {
    type: "Polygon" as const,
    coordinates: [[[w - pad, s - pad], [e + pad, s - pad], [e + pad, n + pad], [w - pad, n + pad], [w - pad, s - pad]]],
  };
}

type SceneSpec = {
  roi: string;
  ageHours: number;
  platform: string;
  status: "processed" | "failed" | "processing";
  detection_count?: number;
  dark_count?: number;
  indeterminate_count?: number;
  land_count?: number;
  chance_match_rate?: number | null;
  recall?: [number, number] | null;
  failure_reason?: string;
};

// Spans every card state the pass history can render: a fresh pass with darks,
// a clean one, a scene whose noise floor was never measured, a failed fetch,
// and a run still in flight.
const SCENE_SPECS: SceneSpec[] = [
  { roi: "north_taiwan", ageHours: 6, platform: "S1D", status: "processed", detection_count: 24, dark_count: 3, indeterminate_count: 2, land_count: 5, chance_match_rate: 0.012, recall: [7, 9] },
  { roi: "north_taiwan", ageHours: 54, platform: "S1C", status: "processed", detection_count: 31, dark_count: 0, indeterminate_count: 1, land_count: 4, chance_match_rate: 0.004, recall: [11, 12] },
  // Fusion never completed here: no noise floor, and therefore no dark calls —
  // a dark count without a measured floor is not a state the pipeline emits.
  { roi: "north_taiwan", ageHours: 102, platform: "S1D", status: "processed", detection_count: 18, dark_count: 0, indeterminate_count: 0, land_count: 2, chance_match_rate: null, recall: null },
  { roi: "north_taiwan", ageHours: 150, platform: "S1C", status: "failed", failure_reason: "Imagery coverage too low" },
  { roi: "gulf_of_finland", ageHours: 9, platform: "S1C", status: "processed", detection_count: 41, dark_count: 6, indeterminate_count: 3, land_count: 8, chance_match_rate: 0.021, recall: [14, 17] },
  { roi: "gulf_of_finland", ageHours: 33, platform: "S1D", status: "processing" },
  { roi: "skagen_kattegat", ageHours: 15, platform: "S1D", status: "processed", detection_count: 52, dark_count: 2, indeterminate_count: 4, land_count: 11, chance_match_rate: 0.008, recall: [19, 21] },
  // Survey regions never fuse: no noise floor, no recall, no dark calls.
  { roi: "hormuz_strait", ageHours: 20, platform: "S1D", status: "processed", detection_count: 37, dark_count: 0, indeterminate_count: 0, land_count: 6, chance_match_rate: null, recall: null },
  { roi: "kerch_strait", ageHours: 44, platform: "S1C", status: "processed", detection_count: 12, dark_count: 0, indeterminate_count: 0, land_count: 3, chance_match_rate: null, recall: null },
];

export function scenes(now: number, roi: string) {
  return SCENE_SPECS.filter((s) => s.roi === roi).map((s) => {
    const sensed = now - s.ageHours * HOUR;
    const box = ROI_BY_NAME.get(s.roi)!.sar_bbox;
    return {
      id: `${s.platform}_STUB_${s.roi}_${s.ageHours}H`,
      name: `${s.platform}_IW_GRDH_STUB_${s.ageHours}H`,
      roi: s.roi,
      sensed_at: iso(sensed),
      platform: s.platform,
      status: s.status,
      processed_at: s.status === "processing" ? null : iso(sensed + 40 * MIN),
      failure_reason: s.failure_reason ?? null,
      footprint: footprint(box),
      imaged_bbox: box,
      // No overview PNG is served in stub mode, so never claim one exists.
      has_overview: false,
      detection_count: s.detection_count ?? 0,
      dark_count: s.dark_count ?? 0,
      indeterminate_count: s.indeterminate_count ?? 0,
      land_count: s.land_count ?? 0,
      chance_match_rate: s.chance_match_rate ?? null,
      recall_large_total: s.recall ? s.recall[1] : null,
      recall_large_detected: s.recall ? s.recall[0] : null,
    };
  });
}

const NAMES = [
  ["EVER PROSPER", "V", 9, "PANAMA", "PA", 70],
  ["NORDIC AURORA", "C", 6, "LIBERIA", "LR", 80],
  ["HAI FENG 812", "B", 3, "CHINA", "CN", 70],
  ["BALTIC TRADER", "S", 7, "MALTA", "MT", 70],
  ["KAOHSIUNG STAR", "T", 4, "TAIWAN", "TW", 60],
  ["ORION SPIRIT", "M", 8, "MARSHALL ISLANDS", "MH", 80],
  ["SEA GUARDIAN", "R", 2, "SINGAPORE", "SG", 52],
  ["MERIDIAN BAY", "K", 5, "GREECE", "GR", 70],
] as const;

// Spread deterministically across the region's sar_bbox so the vessel layer,
// the contact rail and the map all have something to show.
export function vessels(now: number, roi: string) {
  const box = ROI_BY_NAME.get(roi)?.sar_bbox;
  if (!box) return [];
  const [w, s, e, n] = box;
  return NAMES.map(([name, initial, sog, country, flag, type], i) => {
    const fx = (i + 1) / (NAMES.length + 1);
    const fy = ((i * 3) % NAMES.length) / NAMES.length;
    return {
      mmsi: 412000000 + i * 1117,
      time: iso(now - (i * 4 + 2) * MIN),
      lat: s + (n - s) * (0.15 + fy * 0.7),
      lon: w + (e - w) * fx,
      sog,
      cog: (i * 47) % 360,
      ship_name: name,
      ship_type: type,
      callsign: `${initial}${4000 + i * 137}`,
      flag_iso2: flag,
      flag_country: country,
      nav_status: i % 5 === 0 ? 1 : 0,
    };
  });
}

export function track(now: number, mmsi: number, hours: number) {
  // Walk backwards from the vessel's current position along its course.
  const all = ROIS.flatMap((r) => vessels(now, r.name));
  const v = all.find((x) => x.mmsi === mmsi) ?? all[0];
  const steps = Math.min(48, Math.max(6, Math.round(hours / 2)));
  return Array.from({ length: steps }, (_, i) => {
    const back = steps - 1 - i;
    return {
      time: iso(now - back * 30 * MIN),
      lat: v.lat - back * 0.004,
      lon: v.lon - back * 0.007,
      sog: v.sog,
      cog: v.cog,
      ship_name: v.ship_name,
      ship_type: v.ship_type,
      callsign: v.callsign,
      flag_iso2: v.flag_iso2,
      flag_country: v.flag_country,
    };
  });
}

// Detections are derived from the scene's own counts so the card, the map and
// the contact rail can never disagree about how many of each state exist.
export function detections(now: number, sceneId: string, includeLand: boolean) {
  const spec = SCENE_SPECS.find((s) => `${s.platform}_STUB_${s.roi}_${s.ageHours}H` === sceneId);
  if (!spec || spec.status !== "processed") return [];
  const roi = ROI_BY_NAME.get(spec.roi)!;
  const [w, s, e, n] = roi.sar_bbox;
  const survey = roi.mode === "survey";
  const crew = vessels(now, spec.roi);

  const total = spec.detection_count ?? 0;
  const dark = spec.dark_count ?? 0;
  const indeterminate = spec.indeterminate_count ?? 0;
  const out = [];

  for (let i = 0; i < total; i++) {
    const isDark = !survey && i < dark;
    const isIndet = !survey && !isDark && i < dark + indeterminate;
    const matched = !survey && !isDark && !isIndet;
    const mate = crew[i % crew.length];
    const fx = ((i * 37) % 100) / 100;
    const fy = ((i * 61) % 100) / 100;
    out.push({
      id: 100000 + i,
      lat: s + (n - s) * (0.1 + fy * 0.8),
      lon: w + (e - w) * (0.05 + fx * 0.9),
      confidence: 0.55 + ((i * 13) % 40) / 100,
      confidence_bucket: i % 3 === 0 ? "high" : i % 3 === 1 ? "medium" : "low",
      match_state: survey ? null : isDark ? "dark" : isIndet ? "indeterminate" : "matched",
      is_dark: survey ? null : isDark ? true : isIndet ? null : false,
      on_land: false,
      matched_mmsi: matched ? mate.mmsi : null,
      match_distance_m: matched ? 120 + ((i * 29) % 400) : null,
      match_time_delta_s: matched ? -((i * 17) % 300) : null,
      candidate_mmsi: isIndet ? mate.mmsi : null,
      dark_margin_m: isDark ? 300 + ((i * 23) % 900) : isIndet ? -(50 + ((i * 11) % 200)) : null,
      ship_name: matched ? mate.ship_name : null,
      candidate_name: isIndet ? mate.ship_name : null,
      ship_type: matched ? mate.ship_type : null,
      callsign: matched ? mate.callsign : null,
      flag_iso2: matched ? mate.flag_iso2 : null,
      flag_country: matched ? mate.flag_country : null,
    });
  }

  if (includeLand) {
    for (let i = 0; i < (spec.land_count ?? 0); i++) {
      out.push({
        id: 200000 + i,
        lat: s + (n - s) * (0.02 + ((i * 7) % 10) / 100),
        lon: w + (e - w) * (0.02 + ((i * 9) % 10) / 100),
        confidence: 0.5 + ((i * 7) % 30) / 100,
        confidence_bucket: "medium",
        match_state: null,
        is_dark: null,
        on_land: true,
        matched_mmsi: null,
        match_distance_m: null,
        match_time_delta_s: null,
        candidate_mmsi: null,
        dark_margin_m: null,
        ship_name: null,
        candidate_name: null,
        ship_type: null,
        callsign: null,
        flag_iso2: null,
        flag_country: null,
      });
    }
  }
  return out;
}

export function sightings(now: number, mmsi: number) {
  // The same hull seen across several passes — what the dossier's history tab
  // is for. Deterministic in the MMSI so repeat views agree.
  const seed = mmsi % 7;
  return SCENE_SPECS.filter((s) => s.status === "processed")
    .slice(0, 4)
    .map((s, i) => {
      const roi = ROI_BY_NAME.get(s.roi)!;
      const survey = roi.mode === "survey";
      const isDark = !survey && (i + seed) % 4 === 0;
      const isIndet = !survey && (i + seed) % 4 === 1;
      return {
        detection_id: 100000 + i,
        scene_id: `${s.platform}_STUB_${s.roi}_${s.ageHours}H`,
        roi: s.roi,
        label: roi.label,
        sensed_at: iso(now - s.ageHours * HOUR),
        match_state: survey ? null : isDark ? "dark" : isIndet ? "indeterminate" : "matched",
        is_dark: survey ? null : isDark ? true : isIndet ? null : false,
        confidence: 0.6 + ((i * 11) % 30) / 100,
        matched: !survey && !isDark && !isIndet,
      };
    });
}

export function nextPass(now: number, roi: string) {
  const mine = SCENE_SPECS.filter((s) => s.roi === roi);
  const latest = mine.length ? Math.min(...mine.map((s) => s.ageHours)) : null;
  const box = ROI_BY_NAME.get(roi);
  const interval = box ? (30 * DAY) / box.passes_per_month : 3 * DAY;
  return {
    latest_scene_sensed_at: latest === null ? null : iso(now - latest * HOUR),
    next_expected_at: latest === null ? null : iso(now - latest * HOUR + interval),
    last_processed_at: latest === null ? null : iso(now - latest * HOUR + 40 * MIN),
  };
}

export function schedule(now: number) {
  const newest = SCENE_SPECS.filter((s) => s.status === "processed").reduce((a, b) => (a.ageHours < b.ageHours ? a : b));
  const newestRoi = ROI_BY_NAME.get(newest.roi)!;
  return {
    scheduler: { state: "idle", detail: "stub mode — no analysis is scheduled" },
    regions: ROIS.map((r, i) => {
      const mine = SCENE_SPECS.filter((s) => s.roi === r.name);
      const latest = mine.length ? Math.min(...mine.map((s) => s.ageHours)) : null;
      const sensed = latest === null ? null : now - latest * HOUR;
      return {
        name: r.name,
        label: r.label,
        mode: r.mode,
        latest_scene_sensed_at: sensed === null ? null : iso(sensed),
        next_expected_at: sensed === null ? null : iso(sensed + (30 * DAY) / r.passes_per_month),
        last_processed_at: sensed === null ? null : iso(sensed + 40 * MIN),
        state: i === 1 ? "analyzing" : i === 2 ? "awaiting_publication" : "scheduled",
      };
    }),
    most_recent: {
      roi: newest.roi,
      label: newestRoi.label,
      mode: newestRoi.mode,
      sensed_at: iso(now - newest.ageHours * HOUR),
      processed_at: iso(now - newest.ageHours * HOUR + 40 * MIN),
      detection_count: newest.detection_count ?? 0,
      dark_count: newest.dark_count ?? 0,
    },
    month_to_date_pu: 1840,
    pu_monthly_ceiling: 24000,
  };
}

export function health(now: number, version: string) {
  return {
    status: "ok",
    database: "ok",
    version,
    sources: {
      ais: {
        state: "connected",
        last_message_at: iso(now - 20_000),
        lag_seconds: 20,
        connected_since: iso(now - 6 * HOUR),
        reconnect_count: 0,
        error_count: 0,
      },
      sar_sentinel1: {
        state: "connected",
        last_message_at: iso(now - 6 * HOUR),
        lag_seconds: null,
        connected_since: iso(now - 6 * HOUR),
        reconnect_count: 0,
        error_count: 0,
      },
    },
  };
}

export function vesselCount(now: number, roi: string) {
  return { count: vessels(now, roi).length };
}

export const STATUS_MESSAGE = { active: false, message: null, level: "info", title: null, updated_at: null };
