import { formatRelativeDate, formatScanDate, formatScanDateTime } from '@/utils/date'

describe('formatRelativeDate', () => {
  const NOW = 1_700_000_000_000 // fixed reference point
  const DAY = 86400000

  it('returns "aujourd\'hui" for today (0 days)', () => {
    expect(formatRelativeDate(NOW, NOW)).toBe("aujourd'hui")
  })

  it('returns "aujourd\'hui" for future timestamps (clock skew)', () => {
    expect(formatRelativeDate(NOW + DAY, NOW)).toBe("aujourd'hui")
  })

  it('returns "il y a 1 jour" for 1 day ago', () => {
    expect(formatRelativeDate(NOW - DAY, NOW)).toBe('il y a 1 jour')
  })

  it('returns "il y a 3 jours" for 3 days ago', () => {
    expect(formatRelativeDate(NOW - 3 * DAY, NOW)).toBe('il y a 3 jours')
  })

  it('returns "il y a 1 sem." for 7 days ago', () => {
    expect(formatRelativeDate(NOW - 7 * DAY, NOW)).toBe('il y a 1 sem.')
  })

  it('returns "il y a 2 sem." for 14 days ago', () => {
    expect(formatRelativeDate(NOW - 14 * DAY, NOW)).toBe('il y a 2 sem.')
  })

  it('returns "il y a 1 mois" for 30 days ago', () => {
    expect(formatRelativeDate(NOW - 30 * DAY, NOW)).toBe('il y a 1 mois')
  })

  it('returns "il y a 3 mois" for 90 days ago', () => {
    expect(formatRelativeDate(NOW - 90 * DAY, NOW)).toBe('il y a 3 mois')
  })
})

describe('formatScanDate', () => {
  it('formats a typical ISO string as "<day> <month-fr-short>"', () => {
    expect(formatScanDate('2026-04-27T15:30:00Z')).toBe('27 avr.')
  })

  it('uses correct French month abbreviations across the year', () => {
    expect(formatScanDate('2026-01-05T00:00:00Z')).toBe('5 janv.')
    expect(formatScanDate('2026-08-15T00:00:00Z')).toBe('15 août')
    expect(formatScanDate('2026-12-31T00:00:00Z')).toBe('31 déc.')
  })

  it('returns null for null input (no orphan separator on the screen)', () => {
    expect(formatScanDate(null)).toBeNull()
  })

  it('returns null for an unparseable string', () => {
    expect(formatScanDate('not-a-date')).toBeNull()
  })
})

describe('formatScanDateTime', () => {
  // Use a fixed reference "now" so today/yesterday windows are deterministic.
  // 2026-05-01 (Friday) at 16:00 local. Tests pass `now` explicitly so they
  // are not flaky across timezones / DST.
  const NOW = new Date('2026-05-01T16:00:00').getTime()

  it('returns "Aujourd\'hui HH:MM" for an iso happening earlier today', () => {
    // 14:32 same calendar day as NOW
    const iso = new Date('2026-05-01T14:32:00').toISOString()
    expect(formatScanDateTime(iso, NOW)).toBe("Aujourd'hui 14:32")
  })

  it('returns "Hier HH:MM" for an iso happening on the previous calendar day', () => {
    const iso = new Date('2026-04-30T09:15:00').toISOString()
    expect(formatScanDateTime(iso, NOW)).toBe('Hier 09:15')
  })

  it('returns "DD/MM HH:MM" for an iso older than yesterday', () => {
    const iso = new Date('2026-04-25T14:32:00').toISOString()
    expect(formatScanDateTime(iso, NOW)).toBe('25/04 14:32')
  })

  it('zero-pads single-digit minutes', () => {
    const iso = new Date('2026-05-01T09:05:00').toISOString()
    expect(formatScanDateTime(iso, NOW)).toBe("Aujourd'hui 09:05")
  })

  it('returns null for null input', () => {
    expect(formatScanDateTime(null, NOW)).toBeNull()
  })

  it('returns null for an unparseable string', () => {
    expect(formatScanDateTime('not-a-date', NOW)).toBeNull()
  })
})
