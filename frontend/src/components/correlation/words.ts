/** Correlation API types (/api/correlation/*) and the plain-English wording used on the page. */

export type Nums = (number | null)[]
export type Side = { id: string; name: string; freq: string; unit: string; value_kind: string; kind: string; transform: string; n_raw: number; inverted?: boolean; category?: string; country?: string }
export type Station = { adf_p: number | null; kpss_p: number | null; verdict: string }
export type Stats = { n: number; n_eff: number | null; pearson: number | null; pearson_p: number | null; ci: [number | null, number | null]; spearman: number | null; spearman_p: number | null; pearson_p_eff: number | null; ci_eff: [number | null, number | null]; r2: number | null }
export type PairResult = {
  a: Side
  b: Side
  freq: string
  window: number
  lag_b: number
  n: number
  start: string
  end: string
  frame: { ts: string[]; a: Nums; b: Nums; a_t: Nums; b_t: Nums }
  stats: Stats
  stability?: { mean: number; std: number; ratio: number | null }
  rolling?: { ts: string[]; pearson: Nums; lo: Nums; hi: Nums; spearman: Nums; ewma: Nums; halflife: number }
  lead_lag?: { lags: { lag: number; corr: number | null; n: number }[]; band: number | null; best_lag: number | null; best_corr: number | null }
  ols?: { alpha: number | null; beta: number | null; t_beta: number | null; p_beta: number | null; r2: number | null; dw: number | null; hac_lags: number | null }
  rolling_beta?: { ts: string[]; beta: Nums }
  stationarity?: { a: Station; b: Station; a_level: Station; b_level: Station }
  granger_newbold?: { r2: number | null; dw: number | null; spurious: boolean; message: string | null }
  cointegration?: { eg_p: number | null; hedge_ratio: number | null; intercept: number | null; half_life: number | null; verdict: string; spread_adf_p: number | null; zscore: Nums; z_last: number | null; ts: string[] }
  granger?: { a_to_b: { lag: number; p: number | null } | null; b_to_a: { lag: number; p: number | null } | null; maxlag: number }
  regimes?: { id: string; name: string; rows: { regime: string; n: number; pearson: number | null; spearman: number | null; ci: [number | null, number | null]; n_eff?: number | null }[] }
  warnings: string[]
}
export type PairDef = { id: string; name: string; a: string; b: string; transform_a: string | null; transform_b: string | null; freq: string; lag_b: number; rationale: string; tags: string[]; country: string | null; is_seed: boolean }
export type RegimePreset = { id: string; name: string; series_id: string | null; kind: string }
export type MatrixResult = { labels: string[]; order?: number[]; matrix: (number | null)[][]; counts?: (number | null)[][]; clusters: number[]; pc1_share: number | null; n_series: number; notes: string[]; names?: Record<string, string>; cached: boolean; computed_at?: string; window_days?: number; dropped?: string[] }
export type DiscoverRow = { y: string; name?: string; transform?: string; pearson: number; spearman: number | null; p: number | null; n: number; n_eff: number | null; best_lag: number | null; best_corr: number | null; stability: number | null; coint_p: number | null; spark: number[] }
export type DiscoverResult = { x: string; x_name?: string; x_transform?: string; rows: DiscoverRow[]; cached: boolean; computed_at?: string; n_candidates: number; window_days?: number; freq?: string }

export const FREQ_WORD: Record<string, string> = { '1d': 'daily', '1w': 'weekly', '1mo': 'monthly', '1q': 'quarterly', '1y': 'yearly' }
export const PERIOD_WORD: Record<string, string> = { '1d': 'days', '1w': 'weeks', '1mo': 'months', '1q': 'quarters', '1y': 'years' }

export const TRANSFORM_OPTIONS: [string, string][] = [
  ['default', 'Automatic (recommended)'],
  ['log_ret', '% change'],
  ['pct', '% change (simple)'],
  ['diff', 'Change'],
  ['diff_bp', 'Change in basis points'],
  ['yoy', 'Change vs a year earlier (%)'],
  ['level', 'Level (raw values)'],
  ['zscore', 'z-score'],
]
export const FREQ_OPTIONS: [string, string][] = [
  ['auto', 'Automatic'],
  ['1d', 'Daily'],
  ['1w', 'Weekly'],
  ['1mo', 'Monthly'],
]

export type Period = '1Y' | '2Y' | '5Y' | '10Y' | 'All'
export const PERIODS: Period[] = ['1Y', '2Y', '5Y', '10Y', 'All']
export function periodStart(p: Period): string | undefined {
  if (p === 'All') return undefined
  const d = new Date()
  d.setFullYear(d.getFullYear() - Number(p.replace('Y', '')))
  return d.toISOString().slice(0, 10)
}
/** Sensible default look-back for a preset: two years of daily data, longer for slower data. */
export function periodForFreq(freq: string): Period {
  return freq === '1mo' || freq === '1q' ? 'All' : freq === '1w' ? '5Y' : '2Y'
}

export function strength(r: number | null | undefined): string {
  if (r === null || r === undefined || !Number.isFinite(r)) return 'Not enough data'
  const a = Math.abs(r)
  if (a < 0.1) return 'No meaningful relationship'
  const s = a < 0.3 ? 'Weak' : a < 0.5 ? 'Moderate' : a < 0.7 ? 'Strong' : 'Very strong'
  return `${s} ${r > 0 ? 'positive' : 'negative'} relationship`
}

export const rho = (v: number | null | undefined, d = 2) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : `${v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`)
export const pval = (p: number | null | undefined) => (p === null || p === undefined ? '—' : p < 0.001 ? '< 0.001' : p.toFixed(3))

const UNIT_WORD: Record<string, string> = { pct: 'percentage points', bp: 'basis points', index: 'index points', usd_bn: '$ billions', usd_mn: '$ millions', thousands: 'thousands', count: 'units', usd: 'dollars', ils: 'shekels' }

/** "daily % change of SPDR S&P 500 ETF (SPY)", "daily change in US 10Y Treasury yield, in basis points". */
export function describeSide(s: Side, freq: string, label: string): string {
  const fw = FREQ_WORD[freq] ?? ''
  const inv = s.inverted ? ' (inverted)' : ''
  switch (s.transform) {
    case 'log_ret':
    case 'pct':
      return `${fw} % change of ${label}${inv}`
    case 'diff_bp':
      return `${fw} change in ${label}${inv}, in basis points`
    case 'diff':
      return `${fw} change in ${label}${inv}${UNIT_WORD[s.unit] ? `, in ${UNIT_WORD[s.unit]}` : ''}`
    case 'yoy':
      return `year-over-year % change of ${label}${inv} (${fw} data)`
    case 'zscore':
      return `z-score of ${label}${inv} (${fw})`
    default:
      return `${fw} level of ${label}${inv}`
  }
}

const SHORT_UNIT: Record<string, string> = { pct: '%', bp: 'bp', index: 'pts', usd_bn: '$bn', usd_mn: '$mn', thousands: 'k', price: '$', usd: '$', ils: '₪' }
/** How a transformed side is shown on charts: multiplier and unit label. log returns are shown as %. */
export function displayScale(s: Side): { k: number; unit: string } {
  switch (s.transform) {
    case 'log_ret':
      return { k: 100, unit: '%' }
    case 'pct':
    case 'yoy':
      return { k: 1, unit: '%' }
    case 'diff_bp':
      return { k: 1, unit: 'bp' }
    case 'diff':
      return { k: 1, unit: s.unit === 'pct' ? 'pp' : (SHORT_UNIT[s.unit] ?? '') }
    case 'zscore':
      return { k: 1, unit: 'z' }
    default:
      return { k: 1, unit: SHORT_UNIT[s.unit] ?? '' }
  }
}
export function levelUnit(s: Side): string {
  return s.unit === 'pct' ? '%' : s.unit === 'price' ? 'Price' : (SHORT_UNIT[s.unit] ?? s.unit)
}

export function spanText(start: string, end: string): string {
  const days = (new Date(`${end.slice(0, 10)}T00:00:00Z`).getTime() - new Date(`${start.slice(0, 10)}T00:00:00Z`).getTime()) / 86400000
  const years = days / 365.25
  if (years < 1) return `${Math.max(1, Math.round(days / 30.4))} months`
  const r = Math.round(years)
  return Math.abs(years - r) < 0.1 ? `${r} year${r === 1 ? '' : 's'}` : `${years.toFixed(1)} years`
}

export function verdictWord(v: string | undefined): string {
  if (v === 'stationary') return 'Stable (no trend)'
  if (v === 'I(1)-like') return 'Trends or wanders'
  if (v === 'ambiguous') return 'Unclear'
  return v ?? '—'
}

/** Backend warnings in plain English; the levels / spurious-regression ones are covered by the page's own notice. */
export function plainWarnings(ws: string[]): string[] {
  const out: string[] = []
  for (const w of ws) {
    const m = w.match(/effective n ≈ (\d+) of (\d+)/)
    if (m) {
      out.push(`Consecutive data points are very similar, so the ${m[2]} observations carry about as much information as ${m[1]} independent ones. The confidence interval already allows for this, but treat the result with caution.`)
      continue
    }
    if (/spurious|still looks I\(1\)/.test(w)) continue
    out.push(w)
  }
  return out
}

export function lagWords(k: number | null, freq: string): string {
  if (k === null) return '—'
  if (k === 0) return 'Same period'
  const unit = PERIOD_WORD[freq] ?? 'periods'
  const n = Math.abs(k)
  return k > 0 ? `Moves first, by ${n} ${n === 1 ? unit.replace(/s$/, '') : unit}` : `Follows, by ${n} ${n === 1 ? unit.replace(/s$/, '') : unit}`
}

export const HELP = {
  pearson: 'Pearson correlation: +1 = always move together, −1 = always move in opposite directions, 0 = no straight-line relationship.',
  spearman: 'Rank correlation: like Pearson, but based on the order of the values, so a few extreme days matter less.',
  ci: 'The range the true correlation very likely lies in (95% confidence), given how much data there is. If it includes 0, the relationship may not be real.',
  n: "Number of paired observations. 'Effective' corrects for observations that are not independent of each other.",
  rolling: 'The correlation measured over a moving window. A line that swings a lot means the relationship is unstable.',
  stationarity: 'Whether a series hovers around a stable average (fine to correlate) or trends and wanders (correlating it can give misleading results). Tested with the ADF and KPSS tests.',
  coint: 'Cointegration: two trending series that are tied together in the long run, so the gap between them keeps returning to normal.',
  granger: "Granger causality: whether past values of one series help predict the other. It is about prediction, not true cause and effect.",
  lag: 'Shift series B in time. A positive lag pairs today’s A with B from k periods earlier (B moves first).',
  pc1: 'The share of all the movement in this set of series that a single common factor explains (first principal component).',
}
