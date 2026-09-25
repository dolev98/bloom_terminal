/** Calendar types (as returned by /api/calendar/*) and plain-English helpers shared by the calendar views. */

export type Values = {
  vintage_ts?: string
  consensus: number | null
  consensus_source?: string | null
  actual: number | null
  actual_source?: string | null
  previous: number | null
  surprise: number | null
  surprise_pct: number | null
  surprise_z: number | null
  unit: string | null
}

export type CalEvent = {
  id: number
  event_key: string
  kind: string
  country: string
  flag: string
  title: string
  category: string
  importance: number
  release_ts: string
  release_ts_il: string | null
  release_ts_et: string | null
  end_ts: string | null
  reference_period: string
  status: string
  source_provider: string
  source_url: string | null
  linked_series: string[]
  affected_tickers: string[]
  notes: string | null
  values: Values
}

export type EventDetail = CalEvent & {
  values_history: Values[]
  past_surprises: { event_id: number; release_ts: string; reference_period: string; consensus: number | null; actual: number | null; previous: number | null; surprise: number | null }[]
  linked: { series_id: string; name: string; unit: string; last: number | null; last_ts: string | null; sparkline: { ts: string[]; value: (number | null)[] } }[]
  reactions: { ticker: string; session: string; ret_pct: number; event_id: number; surprise_z: number | null }[]
}

export type CentralBank = {
  bank: string
  name: string
  country: string
  event_key: string
  rate_series: string | null
  source_url: string | null
  next_date: string | null
  next_ts: string | null
  days_to_go: number | null
  sep: boolean
  verified: boolean | null
  last_date: string | null
  current_rate: number | null
  rate_ts: string | null
}

export const COUNTRY_NAME: Record<string, string> = {
  US: 'US',
  IL: 'Israel',
  EA: 'Euro area',
  DE: 'Germany',
  GB: 'UK',
  JP: 'Japan',
  CN: 'China',
  CA: 'Canada',
  CH: 'Switzerland',
  AU: 'Australia',
  NZ: 'New Zealand',
  GLOBAL: 'Global',
}

/** Country filter chips → backend country codes (Euro area includes Germany). */
export const COUNTRY_CHIPS: { id: string; label: string; codes: string[] }[] = [
  { id: 'US', label: 'US', codes: ['US'] },
  { id: 'IL', label: 'Israel', codes: ['IL'] },
  { id: 'EA', label: 'Euro area', codes: ['EA', 'DE'] },
  { id: 'GB', label: 'UK', codes: ['GB'] },
  { id: 'JP', label: 'Japan', codes: ['JP'] },
  { id: 'CN', label: 'China', codes: ['CN'] },
  { id: 'CA', label: 'Canada', codes: ['CA'] },
  { id: 'CH', label: 'Switzerland', codes: ['CH'] },
  { id: 'AU', label: 'Australia', codes: ['AU'] },
]

export type KindGroup = 'data' | 'cb' | 'earnings' | 'auction' | 'holiday' | 'other'
export const KIND_GROUPS: { id: KindGroup; label: string; kinds: string[] }[] = [
  { id: 'data', label: 'Economic data', kinds: ['macro'] },
  { id: 'cb', label: 'Central banks', kinds: ['cb_decision', 'cb_minutes', 'speech'] },
  { id: 'earnings', label: 'Earnings', kinds: ['earnings', 'dividend'] },
  { id: 'auction', label: 'Auctions', kinds: ['auction'] },
  { id: 'holiday', label: 'Holidays', kinds: ['holiday'] },
  { id: 'other', label: 'Other', kinds: ['ipo', 'expiry', 'index_rebalance', 'election', 'geo', 'reporting_deadline', 'risk_window'] },
]

const CB_WORDS = /\b(FOMC|Fed|ECB|BOE|BoE|BOJ|BoJ|BOC|RBA|RBNZ|SNB|PBOC|BoI|BOI|Bank of|MPC|Gov|Governor|Buba|Lagarde|Powell|Bailey|Ueda|Nagel|Macklem|Bullock|Schlegel|Yaron)\b/
const CB_ACTS = /speaks|speech|testif|press conference|minutes|bulletin|monetary policy|beige book|policy report/i

/** Which filter group an event belongs to. Vendor feeds file speeches/auctions under "macro", so look at the title. */
export function kindGroup(e: Pick<CalEvent, 'kind' | 'title'>): KindGroup {
  const k = e.kind
  if (k === 'holiday') return 'holiday'
  if (k === 'auction' || /\bauction\b/i.test(e.title)) return 'auction'
  if (k === 'cb_decision' || k === 'cb_minutes' || k === 'speech') return 'cb'
  if (k === 'earnings' || k === 'dividend') return 'earnings'
  if (k === 'macro') {
    if (CB_ACTS.test(e.title) && CB_WORDS.test(e.title)) return 'cb'
    if (/speaks|testif/i.test(e.title)) return 'other'
    return 'data'
  }
  return 'other'
}

/** "Core Retail Sales m/m" → "Core Retail Sales (vs previous month)". Keeps names, expands shorthand. */
export function plainTitle(t: string): string {
  return t
    .replace(/\s*\[(filed|expected|priced)\]\s*$/i, '')
    .replace(/\s*\((MoM)\)|\s+m\/m\b/g, ' (vs previous month)')
    .replace(/\s*\((YoY)\)|\s+y\/y\b/g, ' (vs a year ago)')
    .replace(/\s*\((QoQ)\)|\s+q\/q\b/g, ' (vs previous quarter)')
    .replace(/\s*\(QoQ SAAR\)/g, ' (annualised, vs previous quarter)')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

const MEASURES: [RegExp, string][] = [
  [/rate decision|policy rate|cash rate/i, 'The central bank sets its main interest rate. A surprise moves bonds, the currency and stocks within seconds.'],
  [/press conference/i, 'The central bank explains its decision and takes questions — the tone often matters as much as the decision.'],
  [/minutes/i, 'The detailed record of the last policy meeting. Markets read it for hints about the next move.'],
  [/beige book/i, "The Fed's summary of anecdotal reports on business conditions from its 12 districts."],
  [/speaks|testif/i, 'A scheduled speech or testimony. Markets listen for hints about future interest-rate moves.'],
  [/non-?farm|payrolls|employment change/i, 'How many jobs the economy added or lost in the period.'],
  [/unemployment rate/i, 'The share of people in the labour force who are looking for work but have none.'],
  [/jobless claims|initial claims|continuing claims/i, 'How many people filed for unemployment benefits in the week — an early, weekly read on layoffs.'],
  [/core (cpi|inflation|pce)/i, 'Inflation excluding volatile food and energy prices — the measure central banks use to judge the underlying trend.'],
  [/\bpce\b/i, "The Fed's preferred inflation gauge, based on what households actually spend."],
  [/\bcpi\b|consumer price|inflation rate|hicp/i, 'How much the prices consumers pay changed — the headline inflation measure.'],
  [/inflation expectations/i, 'What households expect inflation to be over the coming years.'],
  [/\bppi\b|producer price/i, 'Prices received by producers. Often an early signal for consumer inflation.'],
  [/\bgdp\b/i, 'The total value of goods and services the economy produced — the broadest measure of growth.'],
  [/retail sales/i, 'How much consumers spent in shops and online — a direct read on consumer demand.'],
  [/\bpmi\b|\bism\b/i, "A survey of purchasing managers. Above 50 means activity is growing, below 50 that it is shrinking. 'Flash' is the early estimate."],
  [/consumer (confidence|sentiment|climate)|umich|gfk/i, 'A survey of how households feel about their finances and the economy.'],
  [/business (climate|confidence)|\bifo\b|\bzew\b|\bcbi\b|\bnab\b|tankan|richmond|empire state|philly/i, "A survey of companies' view of current conditions and the months ahead."],
  [/industrial production|industrial output/i, 'Output of factories, mines and utilities.'],
  [/durable goods/i, 'New orders for long-lasting goods such as machinery and aircraft — a gauge of business investment.'],
  [/housing starts|building permits|home sales|house price|home price/i, 'Activity and prices in the housing market.'],
  [/trade balance/i, 'Exports minus imports. A negative number is a deficit.'],
  [/current account/i, "The broadest measure of a country's transactions with the rest of the world (trade, income and transfers)."],
  [/crude oil inventories|natural gas storage|api weekly/i, 'Weekly change in US energy stockpiles. Big surprises move oil and gas prices.'],
  [/money supply|\bm2\b|\bm3\b|private loans|bank lending/i, 'Growth in money and bank lending — a read on credit conditions.'],
  [/net borrowing|budget|fiscal/i, 'How much more the government spent than it collected.'],
  [/leading index/i, 'A composite of indicators that tend to turn before the economy does.'],
  [/credit card spending/i, 'Spending on credit cards — a timely read on consumers.'],
  [/bulletin|monthly report/i, 'A regular economic report from the central bank.'],
  [/daylight saving/i, 'Clocks change, so the time difference between markets shifts.'],
  [/\bauction\b/i, 'The government sells new debt. The result shows how much demand there was, and at what yield.'],
]

export function whatItMeasures(e: Pick<CalEvent, 'kind' | 'title'>): string | null {
  if (e.kind === 'holiday') return 'A public or exchange holiday: the market is closed or trading hours are shorter.'
  if (e.kind === 'ipo') return "A company's first listing on the stock market."
  if (e.kind === 'earnings') return 'The company reports its quarterly results.'
  if (e.kind === 'risk_window') return 'A period you marked as risky for your positions.'
  for (const [re, text] of MEASURES) if (re.test(e.title)) return text
  return null
}

// ------------------------------------------------------------------ time helpers (Israel / New York)
const utc = (s: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`)

/** YYYY-MM-DD of an instant in Israel. */
export function dayKeyIL(s: string | Date): string {
  const d = typeof s === 'string' ? utc(s) : s
  return d.toLocaleDateString('en-CA', { timeZone: 'Asia/Jerusalem' })
}

export function timeNY(s: string | null | undefined): string {
  if (!s) return '—'
  return utc(s).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' })
}

export function todayKeyIL(): string {
  return dayKeyIL(new Date())
}

/** Date-only arithmetic on YYYY-MM-DD strings (UTC noon avoids DST edge cases). */
export function addDaysKey(key: string, n: number): string {
  const d = new Date(`${key}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function mondayKey(key: string): string {
  const d = new Date(`${key}T12:00:00Z`)
  return addDaysKey(key, -((d.getUTCDay() + 6) % 7))
}

export function keyLabel(key: string, opts: Intl.DateTimeFormatOptions = { weekday: 'short', day: 'numeric', month: 'short' }): string {
  return new Date(`${key}T12:00:00Z`).toLocaleDateString('en-GB', { ...opts, timeZone: 'UTC' })
}

export function dayHeading(key: string, today = todayKeyIL()): string {
  if (key === today) return `Today · ${keyLabel(key)}`
  if (key === addDaysKey(today, 1)) return `Tomorrow · ${keyLabel(key)}`
  if (key === addDaysKey(today, -1)) return `Yesterday · ${keyLabel(key)}`
  return keyLabel(key, { weekday: 'long', day: 'numeric', month: 'short' })
}

export function isPast(s: string): boolean {
  return utc(s).getTime() < Date.now()
}

// ------------------------------------------------------------------ numbers
/** Event values with their unit: '%' → 4.1%, 'k' → 201k, otherwise the plain number. */
export function evNum(v: number | null | undefined, unit: string | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const maxDigits = Number.isInteger(v) ? 0 : Math.abs(v) >= 100 ? 1 : 3
  const s = v.toLocaleString('en-US', { maximumFractionDigits: maxDigits })
  if (unit === '%') return `${s}%`
  if (unit === 'k') return `${s}k`
  if (unit === 'bn') return `${s}bn`
  if (unit === 'M') return `${s}M`
  if (unit === 'tn') return `${s}tn`
  return s
}

/** The release time has passed but no actual value reached us (vendor feeds carry forecasts only). */
export function releasedWithoutActual(e: Pick<CalEvent, 'release_ts' | 'values'>): boolean {
  return e.values.actual === null && isPast(e.release_ts)
}
export const NO_ACTUAL_TEXT = 'Released — actual not available from our sources'

/** 'up' when the actual came in above the forecast, 'down' below, '' when there is nothing to compare. */
export function surpriseTone(v: Values): 'up' | 'down' | '' {
  if (v.actual === null || v.consensus === null) return ''
  if (Math.abs(v.actual - v.consensus) < 1e-9) return ''
  return v.actual > v.consensus ? 'up' : 'down'
}

export function surpriseText(v: Values): string {
  if (v.actual === null) return NO_ACTUAL_TEXT
  if (v.consensus === null) return 'No forecast was available'
  const d = v.actual - v.consensus
  if (Math.abs(d) < 1e-9) return 'In line with the forecast'
  return `${d > 0 ? 'Above' : 'Below'} the forecast by ${evNum(Math.abs(d), v.unit)}`
}

/** Unit-aware value for linked data series (catalog units like pct, count, index, thousands, usd_bn). */
export function seriesNum(v: number | null | undefined, unit: string): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  if (unit === 'pct') return `${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}%`
  if (unit === 'count') return v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : String(v)
  if (unit === 'thousands') return `${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}k`
  if (unit === 'usd_bn') return `$${v.toLocaleString('en-US', { maximumFractionDigits: 1 })}bn`
  if (unit === 'usd_mn') return `$${(v / 1000).toLocaleString('en-US', { maximumFractionDigits: 1 })}bn`
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
}
