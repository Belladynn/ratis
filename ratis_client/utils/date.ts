/**
 * Formats a timestamp as a human-readable relative date string (French).
 * @param timestamp - Unix timestamp in milliseconds
 * @param now - Optional current time override (for testing), defaults to Date.now()
 */
export function formatRelativeDate(timestamp: number, now: number = Date.now()): string {
  const diffDays = Math.floor((now - timestamp) / 86400000)
  if (diffDays <= 0) return "aujourd'hui"
  if (diffDays === 1) return 'il y a 1 jour'
  if (diffDays < 7) return `il y a ${diffDays} jours`
  if (diffDays < 14) return 'il y a 1 sem.'
  if (diffDays < 30) return `il y a ${Math.floor(diffDays / 7)} sem.`
  if (diffDays < 60) return 'il y a 1 mois'
  return `il y a ${Math.floor(diffDays / 30)} mois`
}

// Short French month abbreviations matching `Intl.DateTimeFormat('fr-FR', {month:'short'})`
// output. Hardcoded so we don't depend on the host's ICU subset (Android JSC ships
// a minimal ICU on some devices and `Intl` may be unavailable or return English).
const FR_MONTHS_SHORT = [
  'janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
  'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.',
] as const

/**
 * Formats an ISO date string as a short French date like "27 avr.".
 *
 * Returns `null` when the input is `null` or unparseable — callers should treat
 * `null` as "no date to display" (no separator, no placeholder).
 *
 * Avoids `Intl.DateTimeFormat` to stay safe on Android JSC builds with a
 * minimal ICU. We hand-format using a static French month table.
 */
export function formatScanDate(iso: string | null): string | null {
  if (iso == null) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return `${d.getDate()} ${FR_MONTHS_SHORT[d.getMonth()]}`
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n)
}

/**
 * Formats an ISO timestamp with both day-context AND time of day, so users can
 * distinguish two scans on the same day. Three buckets, all in local time:
 *
 *   - same calendar day as `now` → `"Aujourd'hui HH:MM"`
 *   - one calendar day before `now` → `"Hier HH:MM"`
 *   - older → `"DD/MM HH:MM"`
 *
 * Year is intentionally dropped from the older bucket — receipts older than a
 * year are vanishingly rare in the scan history and the day/month is enough
 * context for triage. If we ever surface very old scans we'll add a year-aware
 * variant.
 *
 * Returns `null` for `null` / unparseable input — same contract as
 * `formatScanDate`. We avoid `Intl.DateTimeFormat` for the same Android JSC
 * portability reason.
 */
export function formatScanDateTime(
  iso: string | null,
  now: number = Date.now(),
): string | null {
  if (iso == null) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null

  const time = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`

  // Compare on local-calendar-day boundaries so "Aujourd'hui" survives the
  // user's local midnight, not UTC's. Math: build a same-Y/M/D timestamp at
  // 00:00 local for both dates and diff in days.
  const nowDate = new Date(now)
  const startOfToday = new Date(
    nowDate.getFullYear(), nowDate.getMonth(), nowDate.getDate(),
  ).getTime()
  const startOfThatDay = new Date(
    d.getFullYear(), d.getMonth(), d.getDate(),
  ).getTime()
  const dayDiff = Math.round((startOfToday - startOfThatDay) / 86400000)

  if (dayDiff <= 0) return `Aujourd'hui ${time}`
  if (dayDiff === 1) return `Hier ${time}`
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)} ${time}`
}
