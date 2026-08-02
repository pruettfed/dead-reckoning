// ISO 3166-1 alpha-2 -> regional indicator emoji ("PA" -> "🇵🇦"). The two
// letters map to the regional indicator symbols at U+1F1E6..U+1F1FF, which
// render as a flag when paired.
//
// Windows/Chrome ships no flag-emoji font and shows the bare letters "PA"
// instead. That is cosmetic only: callers display flagCountry alongside, so
// the country is never conveyed by the glyph alone.
const REGIONAL_INDICATOR_A = 0x1f1e6;

export function flagEmoji(iso2: string | null): string | null {
  if (iso2 === null || !/^[A-Za-z]{2}$/.test(iso2)) return null;
  return [...iso2.toUpperCase()]
    .map((c) => String.fromCodePoint(REGIONAL_INDICATOR_A + c.charCodeAt(0) - 65))
    .join("");
}
