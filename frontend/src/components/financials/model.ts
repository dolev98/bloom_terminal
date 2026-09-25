/** Types and plain-English helpers for the Financials tab (mirrors backend/app/statements responses). */
import { dateLabel } from '../../lib/format'

export type PeriodKind = 'FY' | 'Q' | 'TTM'

export type Provenance = {
  source: string
  source_concept?: string | null
  accession_or_url?: string | null
  filed_at?: string | null
  confidence: number
  restated: boolean
  approved: boolean
  page?: number | null
  unit_scale?: number
}
export type Period = { period_end: string; period_type: string; fiscal_year: number | null; fiscal_period: string | null; period_start: string | null; currency: string; label: string }
export type StatementsResp = {
  ticker: string
  entity_id: string
  period_type: string
  restated: boolean
  approved_only: boolean
  periods: Period[]
  fields: Record<string, (number | null)[]>
  provenance: Record<string, (Provenance | null)[]>
  currency: string
}
export type RatioRow = { period_end: string; label: string; fiscal_year: number | null; fiscal_period: string | null; [k: string]: number | string | null }
export type RatiosResp = { ticker: string; period_type: string; market_cap: number | null; periods: RatioRow[] }
export type CoverageSource = { source: string; n_fields: number; approved: number; pending: number; restated: boolean; filed_at: string | null; accession_or_url: string | null }
export type CoverageRow = { period_type: string; period_end: string; fiscal_year: number | null; fiscal_period: string | null; sources: CoverageSource[] }
export type ValidationCheck = { name: string; ok: boolean; diff_pct: number; tol_pct: number; note?: string }
export type StatementDoc = {
  id: number
  ticker: string | null
  kind: string
  path_or_url: string
  status: string
  fiscal_year: number | null
  period_type: string | null
  source: string
  validation: { passed?: boolean; error?: string; periods?: { period_end: string; checks: ValidationCheck[]; passed: boolean }[] } | null
  created_at: string
  decided_at: string | null
}
export type EntityResp = { entity_id: string; ticker: string; name: string; filer_type: string; cik: number | null; currency: string; fiscal_year_end_month: number | null }
export type Profile = { ticker: string; name: string | null; kind: string; currency: string; cik?: number; other_listing?: string }

/** Year-ago offset in rows: FY → previous row; quarters and TTM → four rows back. */
export const LAG: Record<string, number> = { FY: 1, Q: 4, TTM: 4, H: 2 }

export const NON_COMPANY: Record<string, string> = {
  index: 'an index',
  etf: 'a fund (ETF)',
  fund: 'a fund',
  fx: 'a currency pair',
  commodity: 'a commodity',
  crypto: 'a cryptocurrency',
}

// ---- line items ------------------------------------------------------------------------------------------
export type RowKind = 'money' | 'pershare' | 'shares'
export type Row = { key: string; label: string; kind?: RowKind; bold?: boolean; indent?: boolean; help?: string }
export type Section = { title?: string; rows: Row[] }

export const HELP = {
  fcf: 'Free cash flow: cash from operations minus capital expenditure.',
  ebitda: 'Earnings before interest, tax, depreciation and amortization: operating income plus depreciation and amortization.',
  netDebt: 'Debt (including lease liabilities) minus cash and short-term investments. Negative means the company holds more cash than debt.',
  roic: 'Return on invested capital: after-tax operating income divided by the capital invested in the business (equity plus net debt, averaged over the year).',
  yoy: 'Year over year: change versus the same period a year earlier.',
}

export const INCOME: Section[] = [
  {
    rows: [
      { key: 'revenue', label: 'Revenue', bold: true },
      { key: 'cost_of_revenue', label: 'Cost of revenue', indent: true },
      { key: 'gross_profit', label: 'Gross profit', bold: true },
      { key: 'rnd_expense', label: 'Research and development', indent: true },
      { key: 'sga_expense', label: 'Selling, general and administrative', indent: true },
      { key: 'other_operating_expense', label: 'Other operating expenses', indent: true },
      { key: 'operating_income', label: 'Operating income', bold: true },
      { key: 'interest_expense', label: 'Interest expense', indent: true },
      { key: 'interest_income', label: 'Interest income', indent: true },
      { key: 'other_nonoperating_income', label: 'Other non-operating income', indent: true },
      { key: 'pretax_income', label: 'Income before tax', bold: true },
      { key: 'income_tax_expense', label: 'Income tax', indent: true },
      { key: 'net_income', label: 'Net income', bold: true },
      { key: 'net_income_to_common', label: 'Net income to common shareholders', indent: true },
    ],
  },
  {
    title: 'Per share',
    rows: [
      { key: 'eps_basic', label: 'Earnings per share, basic', kind: 'pershare' },
      { key: 'eps_diluted', label: 'Earnings per share, diluted', kind: 'pershare' },
      { key: 'shares_basic_weighted', label: 'Average shares, basic (millions)', kind: 'shares' },
      { key: 'shares_diluted_weighted', label: 'Average shares, diluted (millions)', kind: 'shares' },
    ],
  },
  {
    title: 'Other items',
    rows: [
      { key: 'depreciation_amortization', label: 'Depreciation and amortization' },
      { key: 'ebitda', label: 'EBITDA', help: HELP.ebitda },
      { key: 'stock_based_comp', label: 'Stock-based compensation' },
    ],
  },
]

export const BALANCE: Section[] = [
  {
    title: 'Assets',
    rows: [
      { key: 'cash_and_equivalents', label: 'Cash and cash equivalents', indent: true },
      { key: 'short_term_investments', label: 'Short-term investments', indent: true },
      { key: 'accounts_receivable', label: 'Accounts receivable', indent: true },
      { key: 'inventory', label: 'Inventory', indent: true },
      { key: 'total_current_assets', label: 'Total current assets', bold: true },
      { key: 'ppe_net', label: 'Property, plant and equipment (net)', indent: true },
      { key: 'goodwill', label: 'Goodwill', indent: true },
      { key: 'intangibles', label: 'Intangible assets', indent: true },
      { key: 'long_term_investments', label: 'Long-term investments', indent: true },
      { key: 'total_assets', label: 'Total assets', bold: true },
    ],
  },
  {
    title: 'Liabilities',
    rows: [
      { key: 'accounts_payable', label: 'Accounts payable', indent: true },
      { key: 'short_term_debt', label: 'Short-term debt', indent: true },
      { key: 'total_current_liabilities', label: 'Total current liabilities', bold: true },
      { key: 'long_term_debt', label: 'Long-term debt', indent: true },
      { key: 'long_term_lease_liabilities', label: 'Long-term lease liabilities', indent: true },
      { key: 'total_liabilities', label: 'Total liabilities', bold: true },
    ],
  },
  {
    title: 'Equity',
    rows: [
      { key: 'retained_earnings', label: 'Retained earnings (accumulated deficit)', indent: true },
      { key: 'minority_interest', label: 'Minority interest', indent: true },
      { key: 'total_equity', label: 'Total equity', bold: true },
      { key: 'shares_outstanding', label: 'Shares outstanding (millions)', kind: 'shares' },
    ],
  },
]

export const CASHFLOW: Section[] = [
  { title: 'Operating', rows: [{ key: 'cfo', label: 'Cash from operations', bold: true }] },
  {
    title: 'Investing',
    rows: [
      { key: 'capex', label: 'Capital expenditure', indent: true },
      { key: 'acquisitions', label: 'Acquisitions', indent: true },
      { key: 'cfi', label: 'Cash from investing', bold: true },
    ],
  },
  {
    title: 'Financing',
    rows: [
      { key: 'dividends_paid', label: 'Dividends paid', indent: true },
      { key: 'share_repurchases', label: 'Share buybacks', indent: true },
      { key: 'share_issuance', label: 'Shares issued', indent: true },
      { key: 'debt_issued', label: 'Debt issued', indent: true },
      { key: 'debt_repaid', label: 'Debt repaid', indent: true },
      { key: 'cff', label: 'Cash from financing', bold: true },
    ],
  },
  {
    title: 'Total',
    rows: [
      { key: 'fx_effect', label: 'Exchange-rate effect', indent: true },
      { key: 'net_change_in_cash', label: 'Net change in cash', bold: true },
    ],
  },
  { title: 'Free cash flow', rows: [{ key: 'fcf', label: 'Free cash flow', bold: true, help: HELP.fcf }] },
]

/** Outflows stored as positive magnitudes (the backend's POSITIVE_MAGNITUDE set). */
export const OUTFLOWS = new Set(['capex', 'acquisitions', 'dividends_paid', 'share_repurchases', 'debt_repaid'])

export const FIELD_LABEL: Record<string, string> = Object.fromEntries(
  [...INCOME, ...BALANCE, ...CASHFLOW].flatMap((s) => s.rows.map((r) => [r.key, r.label.replace(/ \((millions|net)\)$/, '')])),
)

// ---- ratios ----------------------------------------------------------------------------------------------
export type RatioKind = 'pct' | 'x' | 'days' | 'money' | 'num' | 'int'
export type RatioDef = { key: string; label: string; kind: RatioKind; help?: string }
export const RATIO_GROUPS: { title: string; rows: RatioDef[] }[] = [
  {
    title: 'Growth',
    rows: [
      { key: 'revenue_yoy', label: 'Revenue growth', kind: 'pct', help: HELP.yoy },
      { key: 'net_income_yoy', label: 'Net income growth', kind: 'pct' },
      { key: 'eps_yoy', label: 'Earnings per share growth (diluted)', kind: 'pct' },
    ],
  },
  {
    title: 'Profitability',
    rows: [
      { key: 'gross_margin', label: 'Gross margin', kind: 'pct', help: 'Gross profit as a share of revenue.' },
      { key: 'operating_margin', label: 'Operating margin', kind: 'pct', help: 'Operating income as a share of revenue.' },
      { key: 'ebitda_margin', label: 'EBITDA margin', kind: 'pct', help: HELP.ebitda },
      { key: 'net_margin', label: 'Net margin', kind: 'pct', help: 'Net income as a share of revenue.' },
      { key: 'effective_tax_rate', label: 'Effective tax rate', kind: 'pct', help: 'Income tax divided by income before tax.' },
    ],
  },
  {
    title: 'Returns',
    rows: [
      { key: 'roe', label: 'Return on equity (ROE)', kind: 'pct', help: 'Net income divided by average shareholders’ equity. Very high values often mean equity is small after buybacks, not that the business is unusually good.' },
      { key: 'roa', label: 'Return on assets (ROA)', kind: 'pct', help: 'Net income divided by average total assets.' },
      { key: 'roic', label: 'Return on invested capital (ROIC)', kind: 'pct', help: HELP.roic },
    ],
  },
  {
    title: 'Cash flow',
    rows: [
      { key: 'fcf', label: 'Free cash flow', kind: 'money', help: HELP.fcf },
      { key: 'fcf_margin', label: 'Free cash flow margin', kind: 'pct', help: 'Free cash flow as a share of revenue.' },
      { key: 'fcf_conversion', label: 'Cash conversion', kind: 'x', help: 'Free cash flow divided by net income. Above 1× means profits turn fully into cash.' },
      { key: 'capex_intensity', label: 'Capital expenditure / revenue', kind: 'pct' },
      { key: 'sbc_pct_revenue', label: 'Stock-based compensation / revenue', kind: 'pct' },
    ],
  },
  {
    title: 'Balance sheet',
    rows: [
      { key: 'net_debt', label: 'Net debt', kind: 'money', help: HELP.netDebt },
      { key: 'net_debt_to_ebitda', label: 'Net debt / EBITDA', kind: 'x', help: 'Years of EBITDA needed to pay off net debt. Below 0 means net cash.' },
      { key: 'debt_to_equity', label: 'Debt / equity', kind: 'x', help: 'Total debt (including leases) divided by shareholders’ equity.' },
      { key: 'interest_coverage', label: 'Interest coverage', kind: 'x', help: 'Operating income divided by interest expense: how many times profits cover interest.' },
      { key: 'working_capital', label: 'Working capital', kind: 'money', help: 'Current assets minus current liabilities.' },
      { key: 'current_ratio', label: 'Current ratio', kind: 'x', help: 'Current assets divided by current liabilities.' },
      { key: 'quick_ratio', label: 'Quick ratio', kind: 'x', help: 'Cash, short-term investments and receivables divided by current liabilities (excludes inventory).' },
    ],
  },
  {
    title: 'Efficiency',
    rows: [
      { key: 'dso', label: 'Days to collect from customers', kind: 'days', help: 'Days sales outstanding (DSO): receivables divided by revenue, in days.' },
      { key: 'dio', label: 'Days of inventory', kind: 'days', help: 'Days inventory outstanding (DIO): inventory divided by cost of revenue, in days.' },
      { key: 'dpo', label: 'Days to pay suppliers', kind: 'days', help: 'Days payables outstanding (DPO): payables divided by cost of revenue, in days.' },
      { key: 'asset_turnover', label: 'Asset turnover', kind: 'x', help: 'Revenue divided by average total assets.' },
    ],
  },
  {
    title: 'Scores',
    rows: [
      { key: 'altman_z2', label: 'Altman Z″-score', kind: 'num', help: 'Altman Z″ (book-value version): a bankruptcy-risk score from working capital, retained earnings, operating income and equity versus assets and liabilities. Above 2.6 = safe, 1.1–2.6 = grey zone, below 1.1 = distress.' },
      { key: 'piotroski_f', label: 'Piotroski F-score (0–9)', kind: 'int', help: 'Piotroski F-score: nine pass/fail tests of profitability, balance-sheet strength and efficiency versus a year earlier. 8–9 is strong, 0–2 is weak.' },
    ],
  },
]

// ---- formatting ------------------------------------------------------------------------------------------
const monthFmt = new Intl.DateTimeFormat('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })
export function monthYear(iso: string): string {
  return monthFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`))
}

/** Full period name for sentences and stat cards: "FY2025", "Q3 FY26", "12 months to Jun 2026". */
export function periodName(p: Pick<Period, 'period_type' | 'label' | 'period_end'>): string {
  return p.period_type === 'TTM' ? `12 months to ${monthYear(p.period_end)}` : p.label
}

/** Column header: "FY2025", "Q3 FY26", "Jun 2026" (TTM tables say "12 months ending" once). */
export function periodShort(p: Pick<Period, 'period_type' | 'label' | 'period_end'>): string {
  return p.period_type === 'TTM' ? monthYear(p.period_end) : p.label
}

export function coverageLabel(r: Pick<CoverageRow, 'period_type' | 'fiscal_year' | 'fiscal_period' | 'period_end'>): string {
  if (r.period_type === 'FY') return `FY${r.fiscal_year ?? ''}`
  if (r.period_type === 'TTM') return `12 months to ${monthYear(r.period_end)}`
  const yy = r.fiscal_year ? String(r.fiscal_year).slice(2) : ''
  return `${r.fiscal_period ?? r.period_type} FY${yy}`
}

/** Statement cell: millions with one decimal; per-share with two; negatives in parentheses. */
export function cellText(v: number | null | undefined, kind: RowKind = 'money'): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  const scaled = kind === 'pershare' ? v : v / 1e6
  const d = kind === 'pershare' ? 2 : 1
  const s = Math.abs(scaled).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
  return v < 0 ? `(${s})` : s
}

export function yoyChange(cur: number | null | undefined, prev: number | null | undefined): number | null {
  if (cur === null || cur === undefined || prev === null || prev === undefined || prev <= 0) return null
  if (cur < 0) return null // positive → negative: a % change is not meaningful
  return (cur - prev) / prev
}

/** Why a YoY % is not shown: missing data vs. not meaningful (sign change / from zero or negative). */
export function yoyBlank(cur: number | null | undefined, prev: number | null | undefined): string {
  if (cur === null || cur === undefined || prev === null || prev === undefined) return '—'
  return 'n/m'
}

export const SOURCE_WORDS: Record<string, string> = {
  edgar_xbrl: 'SEC filings (XBRL)',
  xbrl_org: 'European ESEF filings (filings.xbrl.org)',
  '6k_parsed': '6-K press releases (read automatically)',
  '6k_llm': '6-K press releases (extracted by AI)',
  pdf_llm: 'PDF reports (extracted by AI)',
  manual: 'entered manually',
  derived: 'calculated from other lines',
}

function formulaWords(concept: string): string {
  return concept
    .replace(/^formula:/, '')
    .replace(/[a-z_]+/g, (k) => (FIELD_LABEL[k] ?? k.replace(/_/g, ' ')).toLowerCase())
    .replace(/ - /g, ' − ')
}

function secForm(per: Period, filerType: string | undefined, concept: string): string {
  const foreign = filerType === 'foreign_20f'
  if (per.period_type === 'FY' || concept.endsWith(':FY-9M') || per.fiscal_period === 'Q4') return foreign ? '20-F' : '10-K'
  if (per.period_type === 'TTM') return foreign ? '20-F' : '10-K / 10-Q'
  return foreign ? '6-K' : '10-Q'
}

/** Hover text with the provenance of one number. */
export function provTitle(p: Provenance | null | undefined, per: Period, filerType?: string): string {
  if (!p) return `Not reported for ${periodName(per)}`
  const concept = p.source_concept ?? ''
  const lines: string[] = []
  const filed = p.filed_at ? ` filed ${dateLabel(p.filed_at)}` : ''
  if (p.source === 'derived') lines.push(`Calculated: ${formulaWords(concept)}`)
  else if (p.source === 'edgar_xbrl') lines.push(`SEC EDGAR ${secForm(per, filerType, concept)}${filed}`)
  else lines.push(`${SOURCE_WORDS[p.source] ?? p.source}${filed}`)
  const clean = concept.replace(/^(derived|ttm):/, '').replace(/:(FY-9M|\d+M-\d+M)$/, '')
  if (clean && p.source !== 'derived') lines.push(`Concept ${clean}`)
  if (concept.endsWith(':FY-9M')) lines.push('Fourth quarter = full year minus the first nine months')
  else if (/:\d+M-\d+M$/.test(concept)) lines.push('Quarter = year-to-date minus the previous year-to-date')
  else if (concept.startsWith('ttm:')) lines.push('Sum of the last four quarters')
  if (p.accession_or_url) lines.push(p.accession_or_url.startsWith('http') ? p.accession_or_url : `Accession ${p.accession_or_url}`)
  if (p.page) lines.push(`Page ${p.page}`)
  if (p.restated) lines.push('Restated in a later filing')
  if (!p.approved) lines.push('Waiting for your approval')
  if (p.confidence < 1) lines.push(`Confidence ${Math.round(p.confidence * 100)}%`)
  return lines.join('\n')
}

/** Keep the last `n` periods of a statements response (n = 0 keeps all). */
export function lastPeriods(d: StatementsResp, n: number): StatementsResp {
  const total = d.periods.length
  if (!n || total <= n) return d
  const cut = <T,>(a: T[]) => a.slice(total - n)
  return {
    ...d,
    periods: cut(d.periods),
    fields: Object.fromEntries(Object.entries(d.fields).map(([k, v]) => [k, cut(v)])),
    provenance: Object.fromEntries(Object.entries(d.provenance).map(([k, v]) => [k, cut(v)])),
  }
}

export function secFilingUrl(accession: string, cik: number | null | undefined): string | undefined {
  if (accession.startsWith('http')) return accession
  if (!cik || !/^\d{10}-\d{2}-\d{6}$/.test(accession)) return undefined
  return `https://www.sec.gov/Archives/edgar/data/${cik}/${accession.replace(/-/g, '')}/`
}
