// ratis_client/utils/text.ts
//
// Display-time normalization helpers for OCR-derived strings.
//
// Decision 2026-04-30 — uppercase everything coming out of OCR :
//   - Original receipts are printed in upper-case anyway (`INTERMARCHÉ`,
//     `MONOPRIX`, `RUE DE BEZONS`).
//   - Mixed-case OCR output ("InteRmaRché") is the artifact of confidence
//     drift on individual letters. Forcing one canonical case removes the
//     noise without losing information.
//   - Consistent casing simplifies dedup, fuzzy matching, and visual comparison
//     of repeated stores in scan-history.
// The backend keeps the raw OCR string for audit trail ; this normalization
// is purely display-time.

/**
 * Normalize an OCR-derived string for display: upper-case everything.
 *
 * Examples :
 *   "InteRmaRché"            → "INTERMARCHÉ"
 *   "MONOPRIX"               → "MONOPRIX"
 *   "18 TER RUE DE BEZONS"   → "18 TER RUE DE BEZONS"
 *   "saint-denis"            → "SAINT-DENIS"
 *
 * Edge cases : empty / whitespace-only input returned unchanged.
 */
export function toDisplayCase(raw: string): string {
  if (!raw || raw.trim().length === 0) return raw;
  return raw.toUpperCase();
}
