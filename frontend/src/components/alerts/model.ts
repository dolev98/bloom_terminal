/** Alert types (fields as returned by /api/alerts/*) and the plain-English wording used on the Alerts page. */
import { dateTimeIL, num, price as fmtPrice } from '../../lib/format'

export type Rule = {
  id: number
  name: string
  ticker: string
  rule_type: string
  reference: string
  series_id: string | null
  threshold: number
  hysteresis_pp: number
  cooldown_hours: number
  daily_cap: number
  channels: string[]
  quiet_hours: { start: string; end: string } | null
  params: Record<string, any>
  enabled: boolean
}
export type RuleState = { rule_id: number; ticker: string; state: string; last_value: number | null; threshold: number; last_eval_at: string | null; last_fired_at: string | null; fired_today: number; last_reason: string | null }
export type Fired = {
  id: number
  rule_id: number | null
  ticker: string
  fired_at: string
  rule_type: string
  value: number | null
  threshold: number | null
  price: number | null
  price_source: string | null
  price_ts: string | null
  fair_value: number | null
  payload: { reference?: string } | null
  delivered: Record<string, boolean | string | number>
  acknowledged_at: string | null
  snoozed_until: string | null
}
export type EvalResult = { rule_id: number; ticker: string; value: number | null; state: string | null; action: string; reason: string | null }
export type EvalResponse = { rules: number; evaluated: number; fired: number; results: EvalResult[] }
export type RuleTypes = { defaults: { hysteresis_pp: number; cooldown_hours: number; daily_cap: number; quiet_hours: { start: string; end: string } }; rule_types: Record<string, { default_threshold: number }> }

export type Unit = '%' | 'price' | 'level'
export type Condition = { type: string; label: string; unit: Unit; needsRef: boolean; allowAll: boolean; noThreshold?: boolean; more?: boolean; help?: string }

/** The builder's conditions, in the order shown. `label` completes "Notify me when <subject> …". */
export const CONDITIONS: Condition[] = [
  { type: 'upside_gt', label: 'upside to fair value is above', unit: '%', needsRef: true, allowAll: true, help: 'Upside = how far fair value is above the current price: (fair value ÷ price − 1) × 100.' },
  { type: 'price_above', label: 'price rises above', unit: 'price', needsRef: false, allowAll: false },
  { type: 'price_below', label: 'price falls below', unit: 'price', needsRef: false, allowAll: false },
  { type: 'pct_change_gt', label: 'daily move is larger than', unit: '%', needsRef: false, allowAll: true, help: 'Today’s change versus the previous close, up or down.' },
  { type: 'series_threshold', label: 'data series crosses a level', unit: 'level', needsRef: false, allowAll: true },
  { type: 'price_below_fv', label: 'price is below fair value by more than', unit: '%', needsRef: true, allowAll: true, more: true },
  { type: 'mos_gt', label: 'margin of safety is above', unit: '%', needsRef: true, allowAll: true, more: true, help: 'Margin of safety = how far the price is below fair value: (1 − price ÷ fair value) × 100.' },
  { type: 'price_crosses_fv', label: 'price crosses fair value', unit: '%', needsRef: true, allowAll: true, noThreshold: true, more: true },
  { type: 'consensus_gap_gt', label: 'analyst target is above the price by more than', unit: '%', needsRef: false, allowAll: true, more: true },
  { type: 'implied_growth_lt', label: 'market-implied growth falls below', unit: '%', needsRef: false, allowAll: true, more: true, help: 'The first-year revenue growth the current price implies (reverse DCF).' },
]
export const condition = (t: string) => CONDITIONS.find((c) => c.type === t)

export const REFERENCES: [string, string][] = [
  ['base_dcf', 'DCF, base case'],
  ['blended', 'blended'],
  ['multiples', 'multiples'],
  ['imported_fv', 'imported'],
]
export const refLabel = (r: string) => (r.startsWith('model:') ? `model “${r.slice(6)}”` : (REFERENCES.find(([k]) => k === r)?.[1] ?? r))

export type Ctx = { names: Record<string, string | null | undefined>; seriesName: (id: string) => string; currency: (t: string) => string }

const money = (v: number, t: string, ctx: Ctx) => `${ctx.currency(t)}${fmtPrice(v)}`
/** A measured value (or, with `exact`, a threshold the user typed: 25 stays "25%", 4.25 stays "4.25%"). */
export function fmtValue(unit: Unit, v: number | null | undefined, t: string, ctx: Ctx, seriesUnit = '', exact = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const d = exact ? (Number.isInteger(v) ? 0 : Math.min(4, (String(v).split('.')[1] ?? '').length)) : unit === '%' ? 1 : 2
  if (unit === '%') return `${num(v, d)}%`
  if (unit === 'price') return money(v, t, ctx)
  return `${num(v, d)}${seriesUnit === '%' ? '%' : seriesUnit ? ` ${seriesUnit}` : ''}`
}

/** "Notify me when AAPL upside to fair value (DCF, base case) exceeds 25%". */
export function ruleSentence(r: Pick<Rule, 'ticker' | 'rule_type' | 'reference' | 'series_id' | 'threshold' | 'params'>, ctx: Ctx, seriesUnit = ''): string {
  const all = !r.ticker || r.ticker === 'ALL'
  const who = all ? 'any watchlist company' : r.ticker
  const whose = all ? 'any watchlist company’s' : r.ticker
  const thr = (u: Unit) => fmtValue(u, r.threshold, r.ticker, ctx, seriesUnit, true)
  const ref = refLabel(r.reference)
  switch (r.rule_type) {
    case 'upside_gt':
      return `Notify me when ${whose} upside to fair value (${ref}) exceeds ${thr('%')}`
    case 'price_above':
      return `Notify me when ${r.ticker} rises above ${thr('price')}`
    case 'price_below':
      return `Notify me when ${r.ticker} falls below ${thr('price')}`
    case 'pct_change_gt':
      return `Notify me when ${who} moves more than ${thr('%')} in a day (up or down)`
    case 'series_threshold': {
      const dir = r.params?.direction === 'below' ? 'falls below' : 'rises above'
      return `Notify me when ${r.series_id ? ctx.seriesName(r.series_id) : 'the data series'} ${dir} ${thr('level')}`
    }
    case 'price_below_fv':
      return `Notify me when ${who} trades more than ${thr('%')} below fair value (${ref})`
    case 'mos_gt':
      return `Notify me when ${whose} margin of safety to fair value (${ref}) exceeds ${thr('%')}`
    case 'price_crosses_fv':
      return `Notify me when ${whose} price crosses fair value (${ref})`
    case 'consensus_gap_gt':
      return `Notify me when the analyst target for ${who} is more than ${thr('%')} above the price`
    case 'implied_growth_lt':
      return `Notify me when ${whose} market-implied growth falls below ${thr('%')}`
    default:
      return `Notify me when ${who} meets “${r.rule_type}” at ${num(r.threshold, 2)}`
  }
}

/** What the value measures, for "Latest value" cells and the inbox. */
export function valueNoun(ruleType: string): string {
  return (
    {
      upside_gt: 'upside to fair value',
      price_above: 'price',
      price_below: 'price',
      pct_change_gt: 'daily move',
      series_threshold: 'value',
      price_below_fv: 'discount to fair value',
      mos_gt: 'margin of safety',
      price_crosses_fv: 'price vs fair value',
      consensus_gap_gt: 'analyst target vs price',
      implied_growth_lt: 'implied growth',
    } as Record<string, string>
  )[ruleType] ?? 'value'
}

const duration = (s: number) => (s < 5400 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} days`)

/** Engine reasons ("stale quote 60218s", "skip: no fair value for reference", …) in plain words. */
export function reasonText(reason: string | null | undefined): { text: string; tone: 'warn' | 'info' | 'muted' } | null {
  if (!reason) return null
  const r = reason.replace(/^skip:\s*/, '')
  let m = /^stale quote (\d+)s/.exec(r)
  if (m) return { text: `Skipped: the price is more than 15 minutes old (last update ${duration(Number(m[1]))} ago) while the market is open.`, tone: 'warn' }
  if (r.startsWith('grey-only')) return { text: 'Skipped: only a delayed Yahoo Finance price is available. Turn on “Allow delayed quotes” for this alert to use it.', tone: 'warn' }
  if (r === 'no quote') return { text: 'Skipped: no price is available yet.', tone: 'warn' }
  if (r.startsWith('no fair value')) return { text: 'Skipped: there is no fair value yet — run a valuation for this company first.', tone: 'warn' }
  if (r.startsWith('no analyst target')) return { text: 'Skipped: no analyst price target is available.', tone: 'warn' }
  if (r.startsWith('no reverse-DCF')) return { text: 'Skipped: no reverse DCF has been run for this company yet.', tone: 'warn' }
  if (r.startsWith('series has no data')) return { text: 'Skipped: this data series has no values yet.', tone: 'warn' }
  if (r.startsWith('no change_pct')) return { text: 'Skipped: today’s price change is not known yet.', tone: 'warn' }
  if (r.startsWith('fired; waiting')) return { text: 'Already triggered — it can fire again once the value moves back past the re-arm gap.', tone: 'muted' }
  if ((m = /^cooldown until (.+)$/.exec(r))) return { text: `Triggered recently — quiet until ${dateTimeIL(m[1])}.`, tone: 'muted' }
  if (r === 'cooldown') return { text: 'Triggered recently — waiting for the cool-down to pass.', tone: 'muted' }
  if ((m = /^daily cap (\d+) reached/.exec(r))) return { text: `Reached today’s limit of ${m[1]} alert${m[1] === '1' ? '' : 's'}.`, tone: 'muted' }
  if (r === 'daily cap') return { text: 'Reached today’s alert limit.', tone: 'muted' }
  if (r === 're-armed') return { text: 'Re-armed — ready to fire again.', tone: 'muted' }
  if (r.startsWith('fired')) return null
  return { text: `Could not check: ${r}`, tone: 'warn' }
}

export const isSkip = (reason: string | null | undefined) => !!reason && (reason.startsWith('skip:') || /^(stale quote|grey-only|no |series has no data)/.test(reason))

/** Catalog unit codes → what to print after a series threshold. */
export const SERIES_UNITS: Record<string, string> = { pct: '%', usd_bn: '$ bn', usd_mn: '$ mn', usd: '$', USD: '$', ils: '₪', ils_mn: '₪ mn', eur_mn: '€ mn', bp: 'bp', thousands: 'thousand', cad: 'CAD', cny: 'CNY', jpy: 'JPY' }
