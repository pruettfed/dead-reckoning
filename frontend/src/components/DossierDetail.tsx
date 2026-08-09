import { contactState } from "../contactState";
import { flagEmoji } from "../flag";
import { navStatusLabel } from "../navStatus";
import { shipTypeLabel } from "../shipType";
import { C, MONO } from "../theme";
import { formatAge } from "../vesselAge";
import { Field } from "./ui";
import type { Detection, Roi, Scene, Vessel } from "../types";

type Props = { detection: Detection | null; vessel: Vessel | null; mode: Roi["mode"]; scene: Scene | null; color: string };

function flag(iso2: string | null, country: string | null): string {
  if (!iso2) return "—";
  return `${flagEmoji(iso2) ?? ""} ${country ?? iso2}`;
}

export default function DossierDetail({ detection, vessel, mode, scene, color }: Props) {
  const fields: { label: string; value: string }[] = [];
  let name = "";
  let sub = "";
  let type = "";
  let candidate = false;

  if (vessel) {
    name = vessel.ship_name ?? `MMSI ${vessel.mmsi}`;
    type = shipTypeLabel(vessel.ship_type) ?? "Unknown type";
    sub = formatAge(vessel.time, Date.now(), "live");
    fields.push(
      { label: "SOG", value: vessel.sog != null ? `${vessel.sog.toFixed(1)} kn` : "—" },
      { label: "COG", value: vessel.cog != null ? `${Math.round(vessel.cog)}°` : "—" },
      { label: "MMSI", value: String(vessel.mmsi) },
      { label: "FLAG", value: flag(vessel.flag_iso2, vessel.flag_country) },
      { label: "TYPE", value: type },
      { label: "NAV", value: navStatusLabel(vessel.nav_status) ?? "—" },
    );
  } else if (detection) {
    const state = contactState(detection, mode);
    // An unresolved contact's candidate is a lead, not an identity — italicised
    // below so it never reads as a confirmed name.
    candidate = state === "indeterminate" && detection.candidate_name !== null;
    name = detection.ship_name ?? (candidate ? detection.candidate_name! : state === "dark" ? "Unidentified hull" : "Unclassified contact");
    type = shipTypeLabel(detection.ship_type) ?? "Unknown type";
    sub =
      state === "matched"
        ? "Matched to an AIS position"
        : state === "dark"
          ? "No transponder return at acquisition"
          : state === "indeterminate"
            ? "Inside an AIS vessel's uncertainty envelope. Neither matched nor ruled out"
            : "Survey region. No AIS reference";
    fields.push(
      { label: "CONF", value: `${(detection.confidence * 100).toFixed(0)}%` },
      { label: "BUCKET", value: detection.confidence_bucket },
    );
    if (state === "matched") {
      fields.push(
        { label: "MMSI", value: String(detection.matched_mmsi) },
        { label: "FLAG", value: flag(detection.flag_iso2, detection.flag_country) },
        { label: "Δ DEAD-RECKON", value: detection.match_distance_m != null ? `${detection.match_distance_m.toFixed(0)} m` : "—" },
        { label: "FIX AGE", value: detection.match_time_delta_s != null ? `${Math.abs(detection.match_time_delta_s / 60).toFixed(1)} min` : "—" },
      );
    } else if (state === "dark") {
      fields.push(
        { label: "MARGIN", value: detection.dark_margin_m != null ? `${detection.dark_margin_m.toFixed(0)} m` : "—" },
        { label: "NOISE FLOOR", value: scene?.chance_match_rate != null ? `${(scene.chance_match_rate * 100).toFixed(1)}%` : "—" },
        { label: "LAT", value: detection.lat.toFixed(4) },
        { label: "LON", value: detection.lon.toFixed(4) },
      );
    } else if (state === "indeterminate") {
      fields.push(
        { label: "CANDIDATE", value: detection.candidate_mmsi != null ? String(detection.candidate_mmsi) : "—" },
        { label: "Δ DEAD-RECKON", value: detection.match_distance_m != null ? `${detection.match_distance_m.toFixed(0)} m` : "—" },
        { label: "MARGIN", value: detection.dark_margin_m != null ? `${detection.dark_margin_m.toFixed(0)} m` : "—" },
      );
    } else {
      fields.push(
        { label: "LAT", value: detection.lat.toFixed(4) },
        { label: "LON", value: detection.lon.toFixed(4) },
      );
    }
  }

  return (
    <div>
      <div style={{ fontSize: 14, fontWeight: 600, letterSpacing: ".04em", color: C.textHi, fontStyle: candidate ? "italic" : "normal" }}>
        {name}
        {candidate && <span style={{ fontFamily: MONO, fontSize: 8.5, fontStyle: "normal", letterSpacing: ".14em", color: C.label, marginLeft: 7 }}>CANDIDATE</span>}
      </div>
      <div style={{ fontSize: 12, fontWeight: 600, letterSpacing: ".03em", color, marginTop: 4 }}>{type}</div>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: "#5f6c72", letterSpacing: ".05em", marginTop: 4, lineHeight: 1.5 }}>{sub}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px 10px", marginTop: 14 }}>
        {fields.map((f) => (
          <Field key={f.label} label={f.label} value={f.value} color={C.text} />
        ))}
      </div>
    </div>
  );
}
