// AIS ship_type is a self-declared hull category, so a category label is the
// finest granularity the field actually supports.
export function shipTypeLabel(code: number | null): string | null {
  if (code === null) return null;
  if (code >= 20 && code <= 29) return "Wing in ground";
  if (code === 30) return "Fishing";
  if (code === 31 || code === 32) return "Towing";
  if (code === 33) return "Dredging";
  if (code === 34) return "Diving ops";
  if (code === 35) return "Military";
  if (code === 36) return "Sailing";
  if (code === 37) return "Pleasure craft";
  if (code >= 40 && code <= 49) return "High-speed craft";
  if (code >= 50 && code <= 59) return "Special craft";
  if (code >= 60 && code <= 69) return "Passenger";
  if (code >= 70 && code <= 79) return "Cargo";
  if (code >= 80 && code <= 89) return "Tanker";
  if (code >= 90 && code <= 99) return "Other";
  return null;
}
