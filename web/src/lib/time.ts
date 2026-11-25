/**
 * Time formatting for operational data. Relative phrases are used for "how long ago / how long
 * until"; the exact timestamp is always available on hover or in the detail view.
 */

const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 365 * 24 * 60 * 60 * 1000],
  ['month', 30 * 24 * 60 * 60 * 1000],
  ['week', 7 * 24 * 60 * 60 * 1000],
  ['day', 24 * 60 * 60 * 1000],
  ['hour', 60 * 60 * 1000],
  ['minute', 60 * 1000],
]

/** "3 minutes ago", "in 54 minutes", "tomorrow". Returns null for an unparseable timestamp. */
export function formatRelative(iso: string | null, now: number): string | null {
  if (!iso) return null
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return null
  const diff = then - now
  if (Math.abs(diff) < 60 * 1000) return diff >= 0 ? 'in a moment' : 'just now'
  for (const [unit, size] of UNITS) {
    if (Math.abs(diff) >= size) {
      return rtf.format(Math.round(diff / size), unit)
    }
  }
  return rtf.format(Math.round(diff / (60 * 1000)), 'minute')
}

/** The exact local timestamp, for titles and detail views. */
export function formatExact(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}
