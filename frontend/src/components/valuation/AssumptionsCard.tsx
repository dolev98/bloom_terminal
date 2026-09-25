import type { ReactNode } from 'react'
import { Card, Help } from '../../ui'
import { dateLabel } from '../../lib/format'
import { HELP, getPath } from './model'

export type Unit = 'pct' | 'num' | 'int'
type Ctx = { r: Record<string, any>; d: Record<string, string>; rfAsOf: string | null }
export type KeyDef = { path: string; dkey: string; label: string; help?: string; unit: Unit; indent?: boolean; value: (r: Record<string, any>) => number | null | undefined; auto: (c: Ctx) => string; detail?: (r: Record<string, any>) => ReactNode }

const p1 = (v: number | null | undefined, d = 1) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(d)}%`)
const last = <T,>(a: T[] | undefined) => (a && a.length ? a[a.length - 1] : undefined)

export const KEY_ASSUMPTIONS: KeyDef[] = [
  {
    path: 'wacc.wacc', dkey: 'wacc',
    label: 'Discount rate (WACC)',
    help: HELP.wacc,
    unit: 'pct',
    value: (r) => r.wacc,
    auto: () => 'Cost of equity and cost of debt, weighted by their share of capital',
    detail: (r) => (
      <>
        Cost of equity {p1(r.cost_of_equity, 2)}
        {r.weight_debt !== undefined && r.weight_debt !== null && (
          <>
            {' '}
            · debt is {p1(r.weight_debt)} of capital at {p1(r.cost_of_debt)} before tax
          </>
        )}
      </>
    ),
  },
  {
    path: 'wacc.rf', dkey: 'rf',
    label: 'Risk-free rate',
    unit: 'pct',
    indent: true,
    value: (r) => r.rf,
    auto: (c) => `US 10-year Treasury yield${c.rfAsOf ? `, ${dateLabel(c.rfAsOf)}` : ''}`,
  },
  {
    path: 'wacc.beta', dkey: 'beta',
    label: 'Beta',
    help: HELP.beta,
    unit: 'num',
    indent: true,
    value: (r) => r.beta,
    auto: (c) => (/own_2y_weekly/.test(c.d.beta ?? '') ? 'From 2 years of weekly price moves versus the market, adjusted toward 1' : 'Estimated from price history'),
  },
  {
    path: 'wacc.erp', dkey: 'erp',
    label: 'Equity risk premium',
    help: HELP.erp,
    unit: 'pct',
    indent: true,
    value: (r) => r.erp,
    auto: (c) => (/default/.test(c.d.erp ?? '') ? 'Standard default' : 'Damodaran estimate'),
    detail: (r) => (r.crp ? <>Plus a country risk premium of {p1(r.crp, 2)}</> : null),
  },
  {
    path: 'revenue_growth', dkey: 'growth_path',
    label: 'Revenue growth, year 1',
    unit: 'pct',
    value: (r) => r.growth_path?.[0],
    auto: (c) => (/historical revenue CAGR/.test(c.d.growth_path ?? '') ? 'Past revenue growth, fading each year to the terminal rate' : /consensus/i.test(c.d.growth_path ?? '') ? 'Analyst consensus, fading to the terminal rate' : 'Fades each year to the terminal rate'),
    detail: (r) => (r.growth_path?.length > 1 ? <>Then {r.growth_path.slice(1).map((g: number) => p1(g)).join(' → ')}</> : null),
  },
  {
    path: 'target_ebit_margin', dkey: 'margin_path',
    label: 'Operating margin target',
    unit: 'pct',
    value: (r) => last(r.margin_path),
    auto: (c) => (/5y average/.test(c.d.margin_path ?? '') ? '5-year average operating margin' : 'Current operating margin'),
    detail: (r) => (r.base_margin !== undefined ? <>From {p1(r.base_margin)} today, reached in year {r.margin_path?.length ?? '—'}</> : null),
  },
  {
    path: 'terminal.g', dkey: 'terminal_g',
    label: 'Terminal growth',
    help: HELP.terminal,
    unit: 'pct',
    value: (r) => r.terminal_g,
    auto: (c) => {
      const cap = /policy cap ([\d.]+)/.exec(c.d.terminal_g ?? '')
      return cap ? `The lower of the risk-free rate and the ${p1(Number(cap[1]))} cap` : 'Capped at the risk-free rate'
    },
  },
  { path: 'forecast_years', dkey: 'forecast_years', label: 'Forecast years', unit: 'int', value: (r) => r.forecast_years, auto: () => 'Default setting' },
]

export function toDisplay(v: number | null | undefined, unit: Unit): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return ''
  if (unit === 'pct') return String(Number((v * 100).toFixed(2)))
  if (unit === 'int') return String(Math.round(v))
  return String(Number(v.toFixed(3)))
}
export function toModel(s: string, unit: Unit): number {
  const n = Number(s)
  return unit === 'pct' ? n / 100 : unit === 'int' ? Math.round(n) : n
}
export function invalid(s: string, unit: Unit): boolean {
  if (s.trim() === '') return false
  const n = Number(s)
  if (!Number.isFinite(n)) return true
  if (unit === 'int') return n < 1 || n > 20 || !Number.isInteger(n)
  if (unit === 'pct') return n < -50 || n > 150
  return n < -5 || n > 10
}

function shown(v: number | null | undefined, unit: Unit): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  if (unit === 'pct') return p1(v, v !== 0 && Math.abs(v) < 0.1 ? 2 : 1)
  if (unit === 'int') return `${Math.round(v)} years`
  return v.toFixed(2)
}

/** The assumptions that drive the DCF, in plain English, each editable inline. */
export function AssumptionsCard({
  resolved,
  derivation,
  overrides,
  edits,
  setEdit,
  onRun,
  onReset,
  running,
  rfAsOf,
  foot,
}: {
  resolved: Record<string, any>
  derivation: Record<string, string>
  overrides: Record<string, any>
  edits: Record<string, string>
  setEdit: (path: string, v: string) => void
  onRun: () => void
  onReset: () => void
  running: boolean
  rfAsOf: string | null
  foot?: ReactNode
}) {
  const ctx: Ctx = { r: resolved, d: derivation, rfAsOf }
  const anyOverride = KEY_ASSUMPTIONS.some((k) => getPath(overrides, k.path) !== undefined)
  const pending = Object.keys(edits).some((p) => {
    const k = KEY_ASSUMPTIONS.find((x) => x.path === p)
    return k && edits[p] !== toDisplay(getPath(overrides, p), k.unit)
  })
  const bad = KEY_ASSUMPTIONS.some((k) => edits[k.path] !== undefined && invalid(edits[k.path], k.unit))
  const waccOverride = (edits['wacc.wacc'] ?? toDisplay(getPath(overrides, 'wacc.wacc'), 'pct')).trim() !== ''

  return (
    <Card title="Key assumptions" hint="Base case" foot={foot}>
      <div className="table-wrap" style={{ border: 0 }}>
        <table className="table compact">
          <thead>
            <tr>
              <th>Assumption</th>
              <th className="r">Used</th>
              <th className="r" style={{ width: 120 }}>
                Your value
              </th>
              <th>Where the value comes from</th>
            </tr>
          </thead>
          <tbody>
            {KEY_ASSUMPTIONS.map((k) => {
              const ov = getPath(overrides, k.path)
              const hasOv = ov !== undefined && ov !== null
              const val = edits[k.path] ?? toDisplay(hasOv ? ov : null, k.unit)
              const changed = edits[k.path] !== undefined && edits[k.path] !== toDisplay(hasOv ? ov : null, k.unit)
              const detail = k.detail?.(resolved)
              const ignored = k.indent && waccOverride
              return (
                <tr key={k.path}>
                  <td style={{ paddingLeft: k.indent ? 26 : 10, whiteSpace: 'nowrap' }} className={ignored ? 'faint' : ''}>
                    {k.label}
                    {k.help && (
                      <>
                        {' '}
                        <Help text={k.help} />
                      </>
                    )}
                  </td>
                  <td className="r">
                    <div className="num strong">{shown(k.value(resolved), k.unit)}</div>
                    {detail && <div className="faint xs" style={{ whiteSpace: 'nowrap' }}>{detail}</div>}
                  </td>
                  <td className="r">
                    <span className="row nowrap" style={{ justifyContent: 'flex-end', gap: 4 }}>
                      <input
                        className="num"
                        type="number"
                        step={k.unit === 'int' ? 1 : k.unit === 'pct' ? 0.1 : 0.01}
                        value={val}
                        placeholder={toDisplay(k.value(resolved), k.unit)}
                        onChange={(e) => setEdit(k.path, e.target.value)}
                        aria-label={`${k.label}${k.unit === 'pct' ? ' (percent)' : ''}`}
                        style={{ width: 76, textAlign: 'right', borderColor: invalid(val, k.unit) ? 'var(--down)' : undefined }}
                      />
                      <span className="faint xs" style={{ width: 12 }}>
                        {k.unit === 'pct' ? '%' : ''}
                      </span>
                    </span>
                  </td>
                  <td className="small">
                    {changed ? (
                      <span className="accent">Changed — re-run to apply</span>
                    ) : hasOv ? (
                      <span className="badge info">Your value</span>
                    ) : ignored ? (
                      <span className="faint">Not used: your discount rate replaces this calculation</span>
                    ) : (
                      <span className="muted" title={derivation[k.dkey] ?? ''}>
                        <span className="faint">Automatic · </span>
                        {k.auto(ctx)}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button className="primary" onClick={onRun} disabled={running || bad}>
          {running ? 'Running…' : 'Re-run with these assumptions'}
        </button>
        {(anyOverride || pending) && (
          <button onClick={onReset} disabled={running}>
            Reset to automatic
          </button>
        )}
        <span className="faint small">{bad ? 'Some values are out of range.' : 'Leave a field empty to use the automatic value. Rates are in percent.'}</span>
      </div>
    </Card>
  )
}
