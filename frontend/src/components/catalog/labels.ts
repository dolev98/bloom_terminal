/** Plain-language labels and formatting for data series (catalog, series page, status page). */
import { useQuery } from '@tanstack/react-query'
import { api, type SeriesSpec } from '../../lib/api'
import { DASH, num, price, ratioPct } from '../../lib/format'

export type FreshState = 'ok' | 'late' | 'failed' | 'never' | 'paused'
export type SeriesMeta = {
  first_ts?: string | null
  last_ts?: string | null
  n_obs?: number | null
  fetched_at?: string | null
  last_status?: string | null
  last_error?: string | null
  last_value?: number | null
  freshness?: FreshState
  expected_by?: string | null
}
export type CatalogItem = SeriesSpec & { meta?: SeriesMeta }
export type ProviderInfo = { id: string; name: string; usable: boolean; reason: string; grey: boolean; egress: string; attribution: string | null; requires: string[] }

export function useCatalog() {
  return useQuery({ queryKey: ['catalog'], queryFn: () => api<{ count: number; items: CatalogItem[] }>('/api/catalog') })
}
export function useProviders() {
  return useQuery({ queryKey: ['providers'], queryFn: () => api<ProviderInfo[]>('/api/catalog/providers'), staleTime: 60_000 })
}

// ---------------------------------------------------------------- categories
/** Catalog category code → plain name. Order = chip order. */
export const CATEGORIES: [string, string][] = [
  ['rates', 'Interest rates'],
  ['inflation', 'Inflation'],
  ['labour', 'Jobs'],
  ['activity', 'Growth'],
  ['credit', 'Credit'],
  ['fx', 'FX'],
  ['commodity', 'Commodities'],
  ['equity', 'Equities'],
  ['liquidity', 'Liquidity'],
  ['sentiment', 'Sentiment'],
  ['housing', 'Housing'],
  ['trade', 'Trade'],
  ['fiscal', 'Government debt'],
  ['stress', 'Financial stress'],
  ['vol', 'Volatility'],
  ['valuation', 'Valuations'],
  ['fundamentals', 'Company data'],
  ['macro', 'Other'],
]
const CAT = Object.fromEntries(CATEGORIES)
export function categoryLabel(c: string | null | undefined): string {
  if (!c) return 'Other'
  return CAT[c] ?? c.charAt(0).toUpperCase() + c.slice(1)
}
export function categoryRank(c: string): number {
  const i = CATEGORIES.findIndex(([k]) => k === c)
  return i < 0 ? 99 : i
}

// ---------------------------------------------------------------- frequency, country, source
export const FREQ_LABEL: Record<string, string> = { '1d': 'Daily', '1w': 'Weekly', '1mo': 'Monthly', '1q': 'Quarterly', '1y': 'Yearly', irregular: 'Irregular' }
export const FREQ_NOUN: Record<string, string> = { '1d': 'day', '1w': 'week', '1mo': 'month', '1q': 'quarter', '1y': 'year', irregular: 'observation' }
export const freqLabel = (f: string) => FREQ_LABEL[f] ?? f

export const COUNTRY_NAME: Record<string, string> = {
  US: 'United States',
  IL: 'Israel',
  EA: 'Euro area',
  EU: 'European Union',
  DE: 'Germany',
  GB: 'United Kingdom',
  JP: 'Japan',
  CN: 'China',
  CA: 'Canada',
  CH: 'Switzerland',
  AU: 'Australia',
  GLOBAL: 'Global',
}
export const countryName = (cc?: string | null) => (cc ? (COUNTRY_NAME[cc] ?? cc) : 'Global / none')

/** Who publishes the data, in words. */
const SOURCE: Record<string, string> = {
  fred: 'FRED — Federal Reserve Bank of St. Louis',
  boi: 'Bank of Israel',
  cbs: 'Israel Central Bureau of Statistics',
  estat: 'Eurostat',
  ecb: 'European Central Bank',
  bis: 'Bank for International Settlements (BIS)',
  imf: 'International Monetary Fund (IMF)',
  oecd: 'OECD',
  ons: 'UK Office for National Statistics',
  edgar: 'SEC EDGAR company filings',
  yf: 'Yahoo Finance (unofficial)',
  manual: 'Entered by you',
  derived: 'Calculated in the terminal from other series',
  val: 'The terminal’s valuation model',
  finnhub: 'Finnhub',
}
export function sourceLabel(provider: string, providers?: ProviderInfo[]): string {
  return SOURCE[provider] ?? providers?.find((p) => p.id === provider)?.name ?? provider
}

// ---------------------------------------------------------------- units & values
type UnitInfo = { kind: 'pct' | 'bp' | 'money' | 'count' | 'price' | 'fraction' | 'flag' | 'plain'; label: string; scale?: number; currency?: string }
const SYMBOL: Record<string, string> = { USD: '$', ILS: '₪', EUR: '€', GBP: '£' }

export function unitInfo(unit: string | null | undefined): UnitInfo {
  const u = (unit ?? '').toLowerCase().trim()
  if (u === 'pct' || u === '%' || u.startsWith('percent')) return { kind: 'pct', label: 'percent' }
  if (u === 'bp') return { kind: 'bp', label: 'basis points' }
  const m = u.match(/^([a-z]{3})_(bn|mn)$/)
  if (m) return { kind: 'money', currency: m[1].toUpperCase(), scale: m[2] === 'bn' ? 1e9 : 1e6, label: `${m[1].toUpperCase()} ${m[2] === 'bn' ? 'billions' : 'millions'}` }
  if (u === 'thousands') return { kind: 'count', scale: 1e3, label: 'thousands' }
  if (u === 'count') return { kind: 'count', scale: 1, label: 'count' }
  if (/^[a-z]{3}$/.test(u) && u !== 'bal') return { kind: 'price', currency: u.toUpperCase(), label: u.toUpperCase() }
  if (u === 'fraction') return { kind: 'fraction', label: 'fraction (0.10 = 10%)' }
  if (u === 'flag') return { kind: 'flag', label: 'yes / no' }
  if (u === 'index') return { kind: 'plain', label: 'index' }
  if (u === 'z') return { kind: 'plain', label: 'z-score' }
  if (u === 'balance') return { kind: 'plain', label: 'net balance' }
  return { kind: 'plain', label: unit ?? '' }
}

/** Unit in words for a header/axis ("percent", "USD billions", "index"). */
export const unitLabel = (unit: string | null | undefined) => unitInfo(unit).label

function compact(v: number, digitsK = 1): string {
  const a = Math.abs(v)
  const s = v < 0 ? '-' : ''
  if (a >= 1e12) return `${s}${(a / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${s}${(a / 1e9).toFixed(a >= 1e11 ? 0 : 1)}B`
  if (a >= 1e6) return `${s}${(a / 1e6).toFixed(2)}M`
  if (a >= 1e4) return `${s}${(a / 1e3).toFixed(digitsK)}K`
  return `${s}${num(a, 0)}`
}

/** A stored value with its unit: 4.12%, 25 bp, $23.3T, ₪3.0170, 1.28M, 336.66. */
export function formatValue(v: number | null | undefined, unit: string | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return DASH
  const u = unitInfo(unit)
  switch (u.kind) {
    case 'pct':
      return `${num(v, 2)}%`
    case 'bp':
      return `${num(v, 0)} bp`
    case 'money': {
      const sym = SYMBOL[u.currency ?? ''] ?? ''
      const body = compact(v * (u.scale ?? 1))
      return sym ? (body.startsWith('-') ? `-${sym}${body.slice(1)}` : `${sym}${body}`) : `${body} ${u.currency}`
    }
    case 'count':
      return compact(v * (u.scale ?? 1))
    case 'price': {
      // company figures (e.g. revenue in USD) are large amounts, not prices
      const sym = SYMBOL[u.currency ?? '']
      const body = Math.abs(v) >= 1e6 ? compact(v) : price(v)
      if (!sym) return `${body} ${u.currency}`
      return body.startsWith('-') ? `-${sym}${body.slice(1)}` : `${sym}${body}`
    }
    case 'fraction':
      return ratioPct(v, 1)
    case 'flag':
      return v >= 0.5 ? 'Yes' : 'No'
    default:
      return num(v, Math.abs(v) >= 100000 ? 0 : 2)
  }
}

/** Decimal places that suit the chart axis for a unit in its plain level form. */
export function chartPrecision(unit: string | null | undefined, sample: number | undefined): number {
  const u = unitInfo(unit)
  if (u.kind === 'bp' || u.kind === 'flag' || u.kind === 'count') return 0
  if (u.kind === 'fraction') return 3
  const a = Math.abs(sample ?? 0)
  if (a >= 10000) return 0
  if (a < 10 && u.kind === 'price') return 4
  return 2
}

// ---------------------------------------------------------------- dates
function utc(ts: string): Date {
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(ts) ? ts : `${ts}Z`)
}
/** The period an observation covers: "Aug 2026", "Q2 2026", "2025", "week of 16 Sep 2026", "23 Sep 2026". */
export function periodLabel(ts: string | null | undefined, freq: string): string {
  if (!ts) return DASH
  const d = utc(ts)
  const day = () => d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
  switch (freq) {
    case '1mo':
      return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })
    case '1q':
      return `Q${Math.floor(d.getUTCMonth() / 3) + 1} ${d.getUTCFullYear()}`
    case '1y':
      return String(d.getUTCFullYear())
    case '1w':
      return `week of ${day()}`
    default:
      return day()
  }
}

/** "2 min ago" (< 1 h), "today 16:10", "yesterday 03:00", "Tue 22 Sep, 03:00" — Israel time. */
export function whenLabel(ts: string | null | undefined): string {
  if (!ts) return 'never'
  const d = utc(ts)
  const mins = Math.round((Date.now() - d.getTime()) / 60000)
  if (mins >= 0 && mins < 1) return 'just now'
  if (mins >= 0 && mins < 60) return `${mins} min ago`
  const tz = 'Asia/Jerusalem'
  const key = (x: Date) => x.toLocaleDateString('en-CA', { timeZone: tz })
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: tz })
  const today = new Date()
  const yesterday = new Date(today.getTime() - 86400000)
  const tomorrow = new Date(today.getTime() + 86400000)
  if (key(d) === key(today)) return `today ${time}`
  if (key(d) === key(yesterday)) return `yesterday ${time}`
  if (key(d) === key(tomorrow)) return `tomorrow ${time}`
  return `${d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: tz })}, ${time}`
}

// ---------------------------------------------------------------- errors & freshness
/** Turn a raw provider/HTTP error into a short reason a person can act on. */
export function shortError(err: string | null | undefined): string {
  if (!err) return 'unknown error'
  const first = err.split('\n')[0]
  const code = Number(first.match(/'(\d{3}) [^']*'/)?.[1] ?? first.match(/\b(?:status|HTTP)\s*(?:code\s*)?(\d{3})\b/i)?.[1] ?? NaN)
  if (Number.isFinite(code)) {
    if (code === 401 || code === 403) return `access denied — check the API key (HTTP ${code})`
    if (code === 404) return `not found at the source (HTTP ${code})`
    if (code === 429) return `the source is limiting requests (HTTP ${code})`
    if (code >= 500) return `the source is having problems (HTTP ${code})`
    if (code >= 400) return `the source rejected the request (HTTP ${code})`
  }
  if (/missing settings/i.test(first)) return 'needs an API key — see Settings'
  if (/grey sources disabled/i.test(first)) return 'unofficial sources are switched off in Settings'
  if (/missing inputs/i.test(first)) return 'a series it is calculated from has no data'
  if (/timeout|timed out/i.test(first)) return 'the source did not respond in time'
  if (/ConnectError|getaddrinfo|nodename|Network is unreachable|Connection refused/i.test(first)) return 'could not reach the source'
  const cleaned = first.replace(/^[A-Za-z]+(Error|Exception):\s*/, '').replace(/^[a-z_]+:\s*/, '')
  return cleaned.length > 90 ? `${cleaned.slice(0, 88)}…` : cleaned
}

export type Freshness = { state: FreshState; text: string; cls: string; title?: string }

export function freshnessOf(item: { provider: string; freq: string; meta?: SeriesMeta | null }): Freshness {
  const m = item.meta ?? {}
  const n = m.n_obs ?? 0
  const state: FreshState = m.freshness ?? (m.last_status === 'error' ? 'failed' : !n ? 'never' : 'ok')
  const manual = item.provider === 'manual'
  const last = periodLabel(m.last_ts, item.freq)
  switch (state) {
    case 'ok':
      return { state, text: 'Up to date', cls: 'muted' }
    case 'late':
      return {
        state,
        text: manual ? `Needs new values — last entry ${last}` : `Late — last data ${last}`,
        cls: 'warn-text',
        title: m.expected_by ? `A newer value was normally expected by ${periodLabel(m.expected_by, '1d')}` : undefined,
      }
    case 'failed':
      return { state, text: `${n ? 'Last update failed' : 'Failed'}: ${shortError(m.last_error)}`, cls: 'down', title: m.last_error ?? undefined }
    case 'never':
      return { state, text: manual ? 'No values entered yet' : 'Never loaded', cls: 'faint' }
    default:
      return { state, text: 'Paused', cls: 'faint', title: 'This series is switched off and is not refreshed' }
  }
}

// ---------------------------------------------------------------- formulas & "show as"
/** "(fred:DGS10 - fred:DGS2)*100" → "(US 10Y Treasury yield − US 2Y Treasury yield) × 100". */
export function formulaText(formula: string, items: { series_id: string; name: string }[]): string {
  let s = formula
  const names: string[] = []
  const ids = items.map((i) => i.series_id).sort((a, b) => b.length - a.length)
  for (const id of ids) {
    if (!s.includes(id)) continue
    names.push(items.find((i) => i.series_id === id)!.name)
    s = s.split(id).join(`\u0000${names.length - 1}\u0000`)
  }
  s = s
    .replace(/\s*\*\s*/g, ' × ')
    .replace(/\s*\/\s*/g, ' ÷ ')
    .replace(/\s+-\s+/g, ' − ')
    .replace(/\s*\+\s*/g, ' + ')
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => names[Number(i)])
}

export type ShowAs = 'level' | 'change' | 'pct' | 'yoy'
export type ShowAsOption = { key: ShowAs; label: string; transform: string; explain: string; unit: string }

const GROWTH_KINDS = ['price', 'level_index', 'stock', 'flow', 'count']

/** Only the views that make sense for this kind of series. */
export function showAsOptions(spec: Pick<SeriesSpec, 'value_kind' | 'unit' | 'freq'>): ShowAsOption[] {
  const u = unitInfo(spec.unit)
  const per = spec.freq === '1d' ? 'business day' : (FREQ_NOUN[spec.freq] ?? 'observation')
  const opts: ShowAsOption[] = [{ key: 'level', label: 'Level', transform: 'level', explain: `The value as published, in ${u.label || 'its own units'}.`, unit: spec.unit }]
  if (u.kind === 'flag') return opts
  if (u.kind === 'pct' && ['yield', 'spread'].includes(spec.value_kind)) {
    opts.push({ key: 'change', label: 'Change', transform: 'diff_bp', explain: `Change from the previous ${per}, in basis points (1 bp = 0.01 percentage point).`, unit: 'bp' })
  } else if (u.kind === 'pct') {
    opts.push({ key: 'change', label: 'Change', transform: 'diff', explain: `Change from the previous ${per}, in percentage points.`, unit: 'pp' })
  } else {
    opts.push({ key: 'change', label: 'Change', transform: 'diff', explain: `Change from the previous ${per}, in ${u.label || 'the series’ own units'}.`, unit: spec.unit })
  }
  if (GROWTH_KINDS.includes(spec.value_kind) && ['money', 'count', 'price', 'plain'].includes(u.kind)) {
    opts.push({ key: 'pct', label: '% change', transform: 'pct', explain: `Percent change from the previous ${per}.`, unit: 'pct' })
    if (spec.freq !== 'irregular') opts.push({ key: 'yoy', label: 'Year-over-year', transform: 'yoy', explain: 'Percent change from a year earlier.', unit: 'pct' })
  }
  return opts
}

export function defaultShowAs(spec: Pick<SeriesSpec, 'value_kind' | 'unit' | 'freq' | 'default_transform'>): ShowAs {
  const opts = showAsOptions(spec)
  return spec.default_transform === 'yoy' && opts.some((o) => o.key === 'yoy') ? 'yoy' : 'level'
}
