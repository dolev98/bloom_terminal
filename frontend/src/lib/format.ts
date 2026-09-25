/** Formatting helpers. Rule: never invent precision; show '—' for missing values. */

export const DASH = '—'

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** Price: 2 decimals, 4 for values under 10 (FX), 0 above 10k (indices) keeps 2 for readability. */
export function price(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const a = Math.abs(v)
  return num(v, a < 10 ? 4 : 2)
}

export function pct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const s = v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return `${signed && v > 0 ? '+' : ''}${s}%`
}

/** Ratio (0.123) → 12.3% */
export function ratioPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  return pct(v * 100, digits, false)
}

/** Large money amounts in compact form with explicit unit: 391035000000 → "391.0B". */
export function money(v: number | null | undefined, currency = ''): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const a = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  const cur = currency === 'USD' ? '$' : currency === 'ILS' ? '₪' : currency ? `${currency} ` : ''
  if (a >= 1e12) return `${sign}${cur}${(a / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${sign}${cur}${(a / 1e9).toFixed(1)}B`
  if (a >= 1e6) return `${sign}${cur}${(a / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${sign}${cur}${(a / 1e3).toFixed(1)}K`
  return `${sign}${cur}${a.toFixed(2)}`
}

export function times(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  return `${v.toFixed(digits)}×`
}

function toDate(s: string | Date): Date {
  if (s instanceof Date) return s
  // backend sends naive UTC ISO strings; treat them as UTC
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`)
}

export function dateLabel(s: string | null | undefined): string {
  if (!s) return DASH
  return toDate(s).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Jerusalem' })
}

export function dayLabel(s: string | null | undefined): string {
  if (!s) return DASH
  return toDate(s).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Asia/Jerusalem' })
}

export function timeIL(s: string | null | undefined): string {
  if (!s) return DASH
  return toDate(s).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Jerusalem' })
}

export function dateTimeIL(s: string | null | undefined): string {
  if (!s) return DASH
  return `${dayLabel(s)}, ${timeIL(s)}`
}

export function ago(s: string | null | undefined): string {
  if (!s) return 'never'
  const ms = Date.now() - toDate(s).getTime()
  const m = Math.round(ms / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 36) return `${h} h ago`
  return `${Math.round(h / 24)} days ago`
}

export function isStale(s: string | null | undefined, maxMinutes: number): boolean {
  if (!s) return true
  return Date.now() - toDate(s).getTime() > maxMinutes * 60000
}

/** Economic-release value with its unit: 21.5 + 'k' → "21.5K", -0.5 + '%' → "-0.5%". No invented decimals. */
export function eventValue(v: number | null | undefined, unit?: string | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const n = Number.isInteger(v) ? v.toLocaleString('en-US') : v.toLocaleString('en-US', { maximumFractionDigits: 3 })
  const u = (unit ?? '').trim()
  if (!u) return n
  if (u === '%') return `${n}%`
  if (/^[kKmMbBtT]$/.test(u)) return `${n}${u.toUpperCase()}`
  return `${n} ${u}`
}
