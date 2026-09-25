/** Plain-English labels, units and formatting for the macro dashboard (/api/macro/{cc}). */

export type Tile = {
  indicator: string
  series_id: string
  name: string
  unit: string
  transform: string
  freq: string
  last: number | null
  last_ts: string | null
  prev: number | null
  change: number | null
  yoy: number | null
  zscore_3y: number | null
  sparkline: { ts: string[]; value: (number | null)[] }
  stale: boolean
  n: number
}

export type Dashboard = {
  country: string
  name: string
  currency: string | null
  sections: { name: string; tiles: Tile[] }[]
  regime: { growth_z: number | null; inflation_z: number | null; label: string | null; trail: { ts: string; growth_z: number | null; inflation_z: number | null }[] }
  recessions: { start: string; end: string | null }[]
  asof: string
}

export type Country = { cc: string; name: string; currency: string }

/** Section order and membership mirror backend/app/macro/catalog.yaml `sections`. */
export const SECTIONS: { id: string; title: string; indicators: string[] }[] = [
  { id: 'growth', title: 'Growth', indicators: ['gdp_yoy', 'gdp_qoq_saar', 'nowcast', 'ip', 'retail'] },
  { id: 'inflation', title: 'Inflation', indicators: ['cpi', 'core_cpi', 'ppi', 'breakeven_10y'] },
  { id: 'labour', title: 'Jobs', indicators: ['unemployment', 'payrolls', 'claims', 'wages'] },
  { id: 'rates', title: 'Interest rates', indicators: ['policy', 'y2', 'y10', 'curve'] },
  { id: 'external', title: 'External', indicators: ['trade_balance', 'current_account', 'fx', 'reserves'] },
  { id: 'fiscal_money', title: 'Fiscal & money', indicators: ['debt_gdp', 'deficit', 'm2', 'credit'] },
  { id: 'sentiment_housing', title: 'Sentiment & housing', indicators: ['consumer', 'pmi', 'housing'] },
]

type Def = { label: string; short: string; help?: string; digits?: number }
const DEFS: Record<string, Def> = {
  gdp_yoy: { label: 'GDP growth, vs a year earlier', short: 'GDP growth', help: 'Real (inflation-adjusted) GDP compared with the same quarter a year earlier.' },
  gdp_qoq_saar: { label: 'GDP growth, annualised quarterly pace', short: 'quarterly GDP', help: 'Growth vs the previous quarter, scaled up to a yearly pace (seasonally adjusted annual rate).' },
  nowcast: { label: 'GDP nowcast for the current quarter', short: 'GDP nowcast', help: "A model estimate of this quarter's annualised GDP growth from the data released so far (Atlanta Fed GDPNow for the US)." },
  ip: { label: 'Industrial production, vs a year earlier', short: 'industrial production' },
  retail: { label: 'Retail sales, vs a year earlier', short: 'retail sales' },
  cpi: { label: 'Inflation (consumer prices), vs a year earlier', short: 'consumer-price inflation' },
  core_cpi: { label: 'Core inflation, vs a year earlier', short: 'core inflation', help: 'Consumer-price inflation excluding volatile items such as food and energy (the exact definition differs by country).' },
  ppi: { label: 'Producer prices, vs a year earlier', short: 'producer prices' },
  breakeven_10y: { label: 'Expected inflation over 10 years (market-implied)', short: 'market inflation expectations', help: 'The 10-year "breakeven": the gap between normal and inflation-protected government bond yields.', digits: 2 },
  unemployment: { label: 'Unemployment rate', short: 'unemployment' },
  payrolls: { label: 'Jobs added in the month', short: 'monthly job gains', help: 'Change in non-farm payroll employment vs the previous month, in thousands.' },
  claims: { label: 'New jobless claims (weekly)', short: 'jobless claims' },
  wages: { label: 'Average hourly earnings, vs a year earlier', short: 'wage growth' },
  policy: { label: 'Central bank policy rate', short: 'policy rate', digits: 2 },
  y2: { label: '2-year government bond yield', short: '2-year yield', digits: 2 },
  y10: { label: '10-year government bond yield', short: '10-year yield', digits: 2 },
  curve: { label: 'Yield curve: 10-year minus 2-year', short: 'yield curve', help: 'Positive means long-term rates are above short-term rates (normal). Negative ("inverted") has often come before recessions.' },
  trade_balance: { label: 'Trade balance (goods and services)', short: 'trade balance', help: 'Exports minus imports in the month. Negative = deficit.' },
  current_account: { label: 'Current account balance', short: 'current account' },
  fx: { label: 'Exchange rate', short: 'exchange rate' },
  reserves: { label: 'Foreign-currency reserves', short: 'FX reserves' },
  debt_gdp: { label: 'Government debt, % of GDP', short: 'government debt' },
  deficit: { label: 'Government budget balance', short: 'budget balance' },
  m2: { label: 'Money supply (M2), vs a year earlier', short: 'money supply' },
  credit: { label: 'Bank lending, vs a year earlier', short: 'bank lending' },
  consumer: { label: 'Consumer sentiment', short: 'consumer sentiment' },
  pmi: { label: 'Manufacturing PMI', short: 'manufacturing PMI', help: "Purchasing managers' survey: above 50 means factories are expanding, below 50 that they are shrinking." },
  housing: { label: 'Housing starts (annual pace)', short: 'housing starts' },
}

const COUNTRY_LABELS: Record<string, Record<string, string>> = {
  US: { policy: 'Policy rate (effective fed funds)', fx: 'Dollar index vs major currencies', consumer: 'Consumer sentiment (University of Michigan)', pmi: 'Manufacturing PMI (ISM)' },
  IL: { policy: 'Bank of Israel rate', fx: 'Shekels per US dollar', core_cpi: 'Inflation excluding energy, vs a year earlier', curve: 'Israel 10-year yield minus US 10-year' },
  EA: { policy: 'ECB deposit rate', fx: 'US dollars per euro', y2: '2-year AAA euro-area yield', y10: '10-year AAA euro-area yield', consumer: 'Consumer confidence' },
  DE: { policy: 'ECB deposit rate', fx: 'US dollars per euro' },
  GB: { policy: 'Bank Rate (Bank of England)', fx: 'US dollars per pound' },
  JP: { policy: 'Bank of Japan policy rate', fx: 'Yen per US dollar' },
  CN: { policy: "People's Bank of China policy rate", fx: 'Yuan per US dollar' },
}

const CURVE_HELP_IL = 'The gap between Israeli and US 10-year government bond yields, in percentage points. A wider gap usually reflects Israeli fiscal or geopolitical risk.'

export function indicatorLabel(cc: string, key: string): string {
  return COUNTRY_LABELS[cc]?.[key] ?? DEFS[key]?.label ?? key
}
export function indicatorShort(key: string): string {
  return DEFS[key]?.short ?? key
}
export function indicatorHelp(cc: string, key: string): string | undefined {
  if (cc === 'IL' && key === 'curve') return CURVE_HELP_IL
  return DEFS[key]?.help
}

const FX_UNITS = new Set(['ils', 'usd', 'jpy', 'cny', 'eur', 'gbp'])

function fmt(v: number, digits: number): string {
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** Latest value with its unit ("3.7%", "196k", "−$88.6bn", "25 bp", "3.017"). */
export function valueText(t: Tile): string {
  const v = t.last
  if (v === null || !Number.isFinite(v)) return '—'
  const d = DEFS[t.indicator]?.digits
  switch (t.unit) {
    case '%':
      return `${fmt(v, d ?? 1)}%`
    case 'pp':
      return `${fmt(v, 2)} pp`
    case 'bp':
      return `${fmt(v, 0)} bp`
    case 'k':
      return `${t.transform === 'diff' && v > 0 ? '+' : ''}${fmt(v, 0)}k`
    case 'thousands':
      return `${fmt(v, 0)}k`
    case 'USD bn':
      return `${v < 0 ? '−' : ''}$${fmt(Math.abs(v), 1)}bn`
    case 'bn':
      return `${fmt(v, 1)}bn`
    case 'index':
    case 'balance':
      return fmt(v, 1)
    default:
      if (FX_UNITS.has(t.unit)) return fmt(v, Math.abs(v) < 10 ? 3 : 2)
      return fmt(v, 2)
  }
}

/** Change vs the previous observation in the tile's own unit (percentage points for rates, % for exchange rates). */
export function changeText(t: Tile): { text: string; sign: number } {
  const c = t.change
  if (c === null || !Number.isFinite(c)) return { text: '—', sign: 0 }
  if (Math.abs(c) < 1e-9) return { text: 'No change', sign: 0 }
  const s = c > 0 ? '+' : '−'
  const a = Math.abs(c)
  const sign = c > 0 ? 1 : -1
  switch (t.unit) {
    case '%':
    case 'pp':
      return { text: `${s}${fmt(a, 2)} pp`, sign }
    case 'bp':
      return { text: `${s}${fmt(a, 0)} bp`, sign }
    case 'k':
    case 'thousands':
      return { text: `${s}${fmt(a, 0)}k`, sign }
    case 'USD bn':
      return { text: `${s}$${fmt(a, 1)}bn`, sign }
    case 'bn':
      return { text: `${s}${fmt(a, 1)}bn`, sign }
    default:
      if ((FX_UNITS.has(t.unit) || t.unit === '') && t.prev) return { text: `${s}${fmt((a / Math.abs(t.prev)) * 100, 2)}%`, sign }
      return { text: `${s}${fmt(a, 1)}`, sign }
  }
}

const asDate = (s: string) => new Date(/[zZ]$/.test(s) ? s : `${s}Z`)

/** Observation period, not a fetch time: "Q2 2026", "Aug 2026", "Week of 12 Sep", "22 Sep 2026". */
export function periodLabel(ts: string | null, freq: string): string {
  if (!ts) return '—'
  const d = asDate(ts)
  const y = d.getUTCFullYear()
  if (freq === '1q') return `Q${Math.floor(d.getUTCMonth() / 3) + 1} ${y}`
  if (freq === '1mo') return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })
  if (freq === '1y') return String(y)
  if (freq === '1w') return `Week of ${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })}`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
}

export const REGIME_TEXT: Record<string, { title: string; text: string }> = {
  goldilocks: { title: 'Goldilocks', text: 'Growth is above its 3-year norm while inflation is below it — a mix that has usually been friendly to both stocks and bonds.' },
  reflation: { title: 'Running hot', text: 'Growth and inflation are both above their 3-year norms — an overheating (reflation) phase in which central banks tend to lean towards higher rates.' },
  stagflation: { title: 'Stagflation-like', text: 'Growth is below its 3-year norm while inflation is above it — the hardest mix for central banks, and usually for markets.' },
  'deflationary slowdown': { title: 'Slowdown', text: 'Growth and inflation are both below their 3-year norms — a cooling economy in which rate cuts become more likely.' },
}

export const COMPARE_COLUMNS: { key: string; label: string; help: string }[] = [
  { key: 'gdp_yoy', label: 'GDP growth', help: 'Real GDP, % change vs a year earlier' },
  { key: 'ip', label: 'Industrial output', help: 'Industrial production, % change vs a year earlier' },
  { key: 'retail', label: 'Retail sales', help: 'Retail sales, % change vs a year earlier' },
  { key: 'cpi', label: 'Inflation', help: 'Consumer prices, % change vs a year earlier' },
  { key: 'core_cpi', label: 'Core inflation', help: 'Consumer prices excluding volatile items, % change vs a year earlier' },
  { key: 'unemployment', label: 'Unemployment', help: 'Unemployment rate, % of the labour force' },
  { key: 'policy', label: 'Policy rate', help: 'Central bank policy rate, % per year' },
  { key: 'y2', label: '2-year yield', help: '2-year government bond yield, % per year' },
  { key: 'y10', label: '10-year yield', help: '10-year government bond yield, % per year' },
]
