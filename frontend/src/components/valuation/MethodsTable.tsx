import type { ReactNode } from 'react'
import { Change, Help } from '../../ui'
import { dateTimeIL } from '../../lib/format'
import { HELP, methodName, plainWarnings, type FairValue, type ModelInfo, type Overview, type Run } from './model'

const ps = (sym: string, v: number | null | undefined) =>
  v === null || v === undefined || !Number.isFinite(v) ? '—' : `${sym}${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const p1 = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`)

type Row = { id: string; name: ReactNode; value: ReactNode; sub?: ReactNode; upside: number | null; status: ReactNode; when?: string }

/** Every valuation method with its value per share, upside vs the current price and a plain-words status. */
export function MethodsTable({ ov, models, latest, price, sym, peersUsed }: { ov: Overview; models: ModelInfo[]; latest: Map<string, Run>; price: number | null; sym: string; peersUsed: boolean }) {
  const up = (v: number | null | undefined) => (v && price ? v / price - 1 : null)
  const ids = ['fcff', 'multiples', 'reverse_dcf', ...models.filter((m) => m.user).map((m) => m.id), 'external']
  const seen = new Set<string>()
  const rows: Row[] = []
  const dcfGrowth = ov.latest.fcff?.diagnostics?.resolved?.growth_path?.[0] as number | undefined

  const status = (run: Run | undefined, id: string): ReactNode => {
    if (!run) return <span className="faint">{id === 'external' ? 'Not used — import a fair value or assumptions under Advanced' : 'Not run yet — select it under Advanced and re-run'}</span>
    const ws = plainWarnings(run.warnings)
    if (run.status === 'ok' && run.value_per_share !== null) return ws.length ? <span className="muted">Calculated · {ws.join('; ')}</span> : <span className="muted">Calculated</span>
    return <span className="warn-text">{ws.join('; ') || 'Could not be calculated'}</span>
  }

  for (const id of ids) {
    if (seen.has(id)) continue
    seen.add(id)
    const run = latest.get(id)
    const m = models.find((x) => x.id === id)
    if (!m && !run) continue
    if (id === 'reverse_dcf') {
      const g = run?.implied_growth ?? null
      rows.push({
        id,
        name: (
          <>
            {methodName(id)} <Help text={HELP.reverse} />
          </>
        ),
        value: g === null ? '—' : `${p1(g)} a year`,
        sub:
          g !== null ? (
            <>
              Revenue growth next year that today’s price requires (fading afterwards)
              {dcfGrowth !== undefined ? `; the DCF assumes ${p1(dcfGrowth)}` : ''}
            </>
          ) : undefined,
        upside: null,
        status: status(run, id),
        when: run?.created_at,
      })
      continue
    }
    let sub: ReactNode
    let help: string | undefined
    if (id === 'fcff') {
      help = HELP.dcf
      if (ov.scenarios.bear != null && ov.scenarios.bull != null) sub = `Bear ${ps(sym, ov.scenarios.bear)} · bull ${ps(sym, ov.scenarios.bull)}`
    } else if (id === 'multiples') {
      help = HELP.multiples
      if (run?.low != null && run?.high != null) sub = `Range ${ps(sym, run.low)} – ${ps(sym, run.high)}, compared with ${peersUsed ? 'peers and its own history' : 'its own history'}`
    } else if (id === 'graham_number') help = HELP.graham
    if (m?.user) sub = 'Custom model from your user_models folder'
    rows.push({
      id,
      name: (
        <>
          {methodName(id, models)}
          {help && (
            <>
              {' '}
              <Help text={help} />
            </>
          )}
        </>
      ),
      value: ps(sym, run?.value_per_share),
      sub,
      upside: up(run?.value_per_share),
      status: status(run, id),
      when: run?.created_at,
    })
  }
  const blended: FairValue | undefined = ov.fair_values.find((f) => f.model === 'blended' && f.scenario === 'base')
  if (blended?.value != null)
    rows.push({
      id: 'blended',
      name: (
        <>
          {methodName('blended')} <Help text={HELP.blended} />
        </>
      ),
      value: ps(sym, blended.value),
      sub: 'Weighted median of the methods above',
      upside: up(blended.value),
      status: <span className="muted">Calculated</span>,
      when: undefined,
    })

  return (
    <div className="table-wrap" style={{ border: 0 }}>
      <table className="table">
        <thead>
          <tr>
            <th>Method</th>
            <th className="r">Value per share</th>
            <th className="r">
              <span style={{ display: 'inline-flex', gap: 5, alignItems: 'center' }}>
                Upside <Help text={HELP.upside} />
              </span>
            </th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>
                <div>{r.name}</div>
                {r.sub && <div className="faint xs">{r.sub}</div>}
              </td>
              <td className="r num strong">{r.value}</td>
              <td className="r">{r.upside === null ? <span className="faint">—</span> : <Change value={r.upside * 100} digits={1} />}</td>
              <td className="small" title={r.when ? `Last run ${dateTimeIL(r.when)}` : ''}>
                {r.status}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
