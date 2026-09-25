/** Types and plain-English helpers for the Valuation tab (mirrors backend/app/valuation responses). */

export type Run = {
  id: number
  model_id: string
  model_version: string
  scenario: string
  assumption_set_id: number | null
  inputs_snapshot_id: string
  value_per_share: number | null
  price: number | null
  price_source: string | null
  price_ts: string | null
  upside: number | null
  status: string
  created_at: string
  warnings: string[]
  low: number | null
  high: number | null
  implied_growth: number | null
}
export type FairValue = { ticker: string; date: string; model: string; scenario: string; value: number | null; price: number | null; price_source: string | null; price_ts: string | null; upside: number | null; implied_growth: number | null; assumption_set_id: number | null; currency: string }
export type FFRow = { method: string; low: number | null; base: number | null; high: number | null; reference: string }
export type Grid = { x: { path: string; values: number[] }; y: { path: string; values: number[] }; values: (number | null)[][]; center?: Record<string, number> }
export type AssumptionSet = { id: number; ticker: string; name: string; scenario: string; source: string; parent_id: number | null; payload: Record<string, any>; checksum: string; note: string | null; created_at: string }
export type ModelResult = {
  model_id: string
  value_per_share: number | null
  ev: number | null
  equity_value: number | null
  low: number | null
  high: number | null
  implied_growth: number | null
  currency: string
  components: Record<string, any>
  diagnostics: Record<string, any>
  warnings: string[]
}
export type Overview = {
  ticker: string
  price: number | null
  price_source: string | null
  price_ts: string | null
  quote_stale: boolean | null
  reference: { name: string | null; value: number | null; upside: number | null; date: string | null }
  fair_values: FairValue[]
  runs: Run[]
  latest: Record<string, ModelResult>
  football_field: FFRow[]
  sensitivity: { wacc_g?: Grid; growth_margin?: Grid } | null
  scenarios: Record<string, number | null>
  assumption_sets: AssumptionSet[]
}
export type ModelInfo = { id: string; name: string; version: string; inputs_required: string[]; reference: string | null; assumptions_schema: any; user: boolean }
export type ModelsResp = { models: ModelInfo[]; errors: Record<string, string>; loaded_at: string; user_dir: string }
export type MacroResp = { macro: Record<string, { value: number | null; as_of: string | null; source: string; url?: string; fetched_at?: string }> }
export type PeersResp = { ticker: string; peers: { ticker: string }[]; pins: string[]; excludes: string[]; effective: string[]; sic: string | null; industry: string | null }
export type Policies = { sbc_cash_expense: boolean; leases_as_debt: boolean; rd_capitalize: boolean; mid_year: boolean; g_cap: number; forecast_years: number }
export type AlertRule = { id: number; name: string; ticker: string; rule_type: string; reference: string; threshold: number; enabled: boolean }

export const DEFAULT_MODELS = ['fcff', 'reverse_dcf', 'multiples']

export const HELP = {
  dcf: 'Discounted cash flow (DCF): projects the company’s future free cash flows and discounts them back to today at the discount rate (WACC). Base case uses the automatic assumptions below; bear and bull shift growth, margin and the discount rate.',
  reverse:
    'Reverse DCF: instead of estimating a value, it works backwards from today’s share price to find the revenue growth the market is assuming, keeping every other DCF assumption the same.',
  multiples: 'Values the company at the price-to-earnings, price-to-sales, EV/EBITDA and similar multiples of comparable companies (or of its own history), from the 25th to the 75th percentile.',
  graham: 'Benjamin Graham’s conservative ceiling: √(22.5 × earnings per share × book value per share).',
  blended: 'Weighted median of the methods: DCF 40%, peer multiples 25%, imported fair value 25%, analyst targets 10% (re-weighted over the methods available).',
  wacc: 'Weighted average cost of capital: the yearly return investors require, blending the cost of equity and the after-tax cost of debt. Future cash flows are discounted at this rate.',
  beta: 'How strongly the share price moves with the overall market: 1 = in line with the market, above 1 = more volatile.',
  erp: 'Equity risk premium: the extra yearly return investors demand for owning shares instead of risk-free government bonds.',
  terminal: 'The growth rate assumed forever after the forecast years. It is capped at the risk-free rate.',
  upside: 'Fair value divided by the current price, minus 1. Negative means the price is above the estimated value.',
}

/** Plain names for the methods the backend knows. */
export function methodName(id: string, models?: ModelInfo[]): string {
  switch (id) {
    case 'fcff':
    case 'base_dcf':
      return 'Discounted cash flow (DCF)'
    case 'reverse_dcf':
      return 'Growth the price implies'
    case 'multiples':
      return 'Price multiples'
    case 'external':
    case 'imported_fv':
      return 'Imported fair value'
    case 'blended':
      return 'Blend of methods'
    case 'analyst':
      return 'Analyst price targets'
  }
  const id2 = id.startsWith('model:') ? id.slice(6) : id
  const m = models?.find((x) => x.id === id2)
  return (m?.name ?? id2.replace(/_/g, ' ')).replace(/\s*\((example )?user model\)\s*$/i, '')
}

/** What the reference value is called in the summary ("Base-case DCF"). */
export function referenceName(ref: string | null | undefined, models?: ModelInfo[]): { long: string; short: string } {
  switch (ref) {
    case 'base_dcf':
      return { long: 'Base-case DCF', short: 'DCF' }
    case 'blended':
      return { long: 'Blend of methods', short: 'blend of methods' }
    case 'multiples':
      return { long: 'Price multiples', short: 'multiples method' }
    case 'imported_fv':
    case 'external':
      return { long: 'Imported fair value', short: 'imported fair value' }
    default: {
      const n = ref ? methodName(ref, models) : 'Fair value'
      return { long: n, short: n }
    }
  }
}

const FIELD_WORDS: Record<string, string> = {
  eps_diluted: 'EPS',
  total_equity: 'book value',
  shares: 'the share count',
  revenue: 'revenue',
  operating_income: 'operating income',
}

/** Raw model warnings → plain words ("Needs EPS and book value — not available"). */
export function plainWarning(w: string): string {
  let m = /^needs (.+)$/i.exec(w)
  if (m) {
    const parts = m[1].split(/,\s*|\s+and\s+/).map((p) => FIELD_WORDS[p.trim()] ?? p.trim().replace(/_/g, ' '))
    const list = parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}` : parts[0]
    return `Needs ${list} — not available`
  }
  if (/no peer or history multiples/i.test(w)) return 'Needs peer or historical multiples — none available'
  if (/revenue missing|no revenue/i.test(w)) return 'Needs revenue from the financial statements — not loaded'
  if (/operating_income missing/i.test(w)) return 'Needs operating income from the financial statements'
  if (/share count missing/i.test(w)) return 'Needs the share count'
  m = /no growth in \[([-\d.]+),\s*([-\d.]+)\] reproduces the price/i.exec(w)
  if (m) return `No revenue growth between ${Math.round(Number(m[1]) * 100)}% and ${Math.round(Number(m[2]) * 100)}% a year reproduces the current price`
  if (/terminal g .* capped at rf/i.test(w)) return 'Terminal growth was capped at the risk-free rate'
  if (/no price/i.test(w)) return 'Needs a current share price'
  return w.charAt(0).toUpperCase() + w.slice(1)
}

export function plainWarnings(ws: string[] | undefined): string[] {
  const out = (ws ?? []).map(plainWarning)
  // "revenue missing" and "cannot value: no revenue" say the same thing
  return [...new Set(out)]
}

/** Latest base-scenario run per model id (runs arrive newest first). */
export function latestRuns(runs: Run[]): Map<string, Run> {
  const out = new Map<string, Run>()
  for (const r of runs) if (r.scenario === 'base' && !out.has(r.model_id)) out.set(r.model_id, r)
  return out
}

// ---- nested assumption payloads ----------------------------------------------------------------------------
export function getPath(obj: any, path: string): any {
  return path.split('.').reduce((cur, k) => (cur && typeof cur === 'object' ? cur[k] : undefined), obj)
}
export function setPath(obj: Record<string, any>, path: string, value: any): void {
  const parts = path.split('.')
  let cur = obj
  for (const p of parts.slice(0, -1)) cur = cur[p] && typeof cur[p] === 'object' ? cur[p] : (cur[p] = {})
  cur[parts[parts.length - 1]] = value
}
export function deletePath(obj: Record<string, any>, path: string): void {
  const parts = path.split('.')
  let cur = obj
  for (const p of parts.slice(0, -1)) {
    if (!cur[p] || typeof cur[p] !== 'object') return
    cur = cur[p]
  }
  delete cur[parts[parts.length - 1]]
}
/** Drop nulls, empty objects and the default scenario so an untouched set reads as "no overrides". */
export function prune(obj: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(obj ?? {})) {
    if (v === null || v === undefined || v === '') continue
    if (k === 'scenario' && v === 'base') continue
    if (typeof v === 'object' && !Array.isArray(v)) {
      const p = prune(v)
      if (Object.keys(p).length) out[k] = p
    } else out[k] = v
  }
  return out
}
export function flatten(obj: Record<string, any>, prefix = ''): [string, any][] {
  return Object.entries(obj).flatMap(([k, v]) => (v && typeof v === 'object' && !Array.isArray(v) ? flatten(v, `${prefix}${k}.`) : [[`${prefix}${k}`, v] as [string, any]]))
}

/** Pydantic JSON schema → leaf fields (dotted paths). User-model schemas live under custom.* */
export type SchemaField = { path: string; label: string; kind: 'number' | 'boolean' | 'string' | 'enum' | 'list'; options?: string[]; description?: string }
const SKIP = ['peer_stats', 'history_stats', 'custom', 'external_source', 'note', 'fair_value_low', 'fair_value_high', 'scenario']
export function schemaFields(schema: any, prefix = ''): SchemaField[] {
  if (!schema?.properties) return []
  const defs = schema.$defs ?? {}
  const out: SchemaField[] = []
  for (const [key, raw] of Object.entries<any>(schema.properties)) {
    if (!prefix && SKIP.includes(key)) continue
    let prop = raw
    if (prop.$ref) prop = defs[prop.$ref.split('/').pop()]
    if (prop?.anyOf) {
      const nonNull = prop.anyOf.find((p: any) => p.type !== 'null' || p.$ref)
      prop = nonNull?.$ref ? { ...defs[nonNull.$ref.split('/').pop()], description: prop.description, title: prop.title } : { ...nonNull, description: prop.description, title: prop.title }
    }
    if (!prop) continue
    const path = prefix + key
    const label = String(raw.title ?? prop.title ?? key).replace(/\bPct\b/g, '%').replace(/\bEbit\b/g, 'EBIT').replace(/\bDa\b/g, 'D&A').replace(/\bNwc\b/g, 'NWC').replace(/\bSbc\b/g, 'SBC').replace(/\bErp\b/g, 'ERP').replace(/\bCrp\b/g, 'CRP').replace(/\bRf\b/g, 'Risk-free rate').replace(/^G$/, 'Growth')
    if (prop.properties) {
      out.push(...schemaFields({ ...prop, $defs: defs }, `${path}.`))
      continue
    }
    const description = prop.description ?? raw.description
    if (prop.type === 'array') out.push({ path, label, kind: 'list', description })
    else if (prop.enum) out.push({ path, label, kind: 'enum', options: prop.enum, description })
    else if (prop.type === 'boolean') out.push({ path, label, kind: 'boolean', description })
    else if (prop.type === 'number' || prop.type === 'integer') out.push({ path, label, kind: 'number', description })
    else if (prop.type === 'string') out.push({ path, label, kind: 'string', description })
  }
  return out
}
