import { Fragment } from 'react'
import { Help } from '../../ui'
import { dateLabel, money, pct, ratioPct, times } from '../../lib/format'
import { LAG, OUTFLOWS, RATIO_GROUPS, cellText, periodName, periodShort, provTitle, yoyBlank, yoyChange, type Period, type RatioDef, type RatioRow, type Section, type StatementsResp } from './model'

const sectionRow = { background: 'var(--surface-2)', color: 'var(--muted)', fontSize: 12, fontWeight: 600, padding: '6px 10px' } as const

function periodTitle(p: Period): string {
  return p.period_start ? `${dateLabel(p.period_start)} – ${dateLabel(p.period_end)}` : `At ${dateLabel(p.period_end)}`
}

/** One statement as a dense table: plain-English line items, newest period on the right, YoY for the latest.
 * `full` is the untrimmed response, so the YoY column can reach a year back even when fewer columns are shown. */
export function StatementTable({ data, full, sections, filerType, note }: { data: StatementsResp; full: StatementsResp; sections: Section[]; filerType?: string; note?: string }) {
  const periods = data.periods
  const n = full.periods.length
  const lag = LAG[full.period_type] ?? 1
  const prevLabel = n > lag ? periodName(full.periods[n - 1 - lag]) : null
  const hidden: string[] = []
  const cols = periods.length + 2
  const firstHead = full.period_type === 'TTM' ? `${data.currency} millions · 12 months ending` : `${data.currency} millions`

  const body = sections.map((sec, si) => {
    const rows = sec.rows.filter((r) => {
      const vals = data.fields[r.key] ?? []
      const any = vals.some((v) => v !== null && v !== undefined)
      if (!any) hidden.push(r.label)
      return any
    })
    if (!rows.length) return null
    return (
      <Fragment key={si}>
        {sec.title && (
          <tr>
            <td colSpan={cols} style={sectionRow}>
              {sec.title}
            </td>
          </tr>
        )}
        {rows.map((r) => {
          const vals = data.fields[r.key] ?? []
          const prov = data.provenance[r.key] ?? []
          const all = full.fields[r.key] ?? []
          const g = n > lag ? yoyChange(all[n - 1], all[n - 1 - lag]) : null
          const label = r.kind === 'pershare' ? `${r.label} (${data.currency})` : r.label
          return (
            <tr key={r.key} className={r.bold ? 'total' : ''}>
              <td className="label-col" style={{ paddingLeft: r.indent ? 24 : 10, color: r.bold ? 'var(--text)' : undefined, whiteSpace: 'nowrap' }}>
                {label}
                {r.help && (
                  <>
                    {' '}
                    <Help text={r.help} />
                  </>
                )}
              </td>
              {vals.map((v, i) => (
                <td key={i} className={`num r ${v !== null && v !== undefined && v < 0 ? 'down' : ''}`} title={provTitle(prov[i], periods[i], filerType)}>
                  {cellText(v, r.kind ?? 'money')}
                </td>
              ))}
              <td className="num r muted" title={g === null && yoyBlank(all[n - 1], all[n - 1 - lag]) === 'n/m' ? 'Not meaningful: the value was zero or negative, or changed sign' : prevLabel ? `${periodName(full.periods[n - 1])} vs ${prevLabel}` : ''}>
                {g === null ? (n > lag ? yoyBlank(all[n - 1], all[n - 1 - lag]) : '—') : pct(g * 100, 1)}
              </td>
            </tr>
          )
        })}
      </Fragment>
    )
  })

  return (
    <>
      <div className="table-wrap">
        <table className="table compact">
          <thead>
            <tr>
              <th style={{ minWidth: 260 }}>{firstHead}</th>
              {periods.map((p) => (
                <th key={p.period_end} className="r" title={periodTitle(p)}>
                  {periodShort(p)}
                </th>
              ))}
              <th className="r">
                <span style={{ display: 'inline-flex', gap: 5, alignItems: 'center' }}>
                  YoY <Help text={`Year over year: the latest period versus the same period a year earlier${prevLabel ? ` (${prevLabel})` : ''}.`} />
                </span>
              </th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
      <div className="card-foot">
        {note ? `${note} ` : ''}
        {sections.some((s) => s.rows.some((r) => OUTFLOWS.has(r.key))) ? 'Outflows (capital expenditure, dividends, buybacks, repayments) are shown as positive amounts. ' : ''}
        {hidden.length > 0 ? `Not reported in these periods: ${hidden.join(', ')}.` : ''}
      </div>
    </>
  )
}

function ratioText(v: number | string | null | undefined, d: RatioDef, currency: string): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (!Number.isFinite(v)) return '—'
  switch (d.kind) {
    case 'pct':
      return ratioPct(v, 1)
    case 'x':
      return times(v, 2)
    case 'days':
      return Math.round(v).toLocaleString('en-US')
    case 'money':
      return money(v, currency)
    case 'int':
      return String(Math.round(v))
    default:
      return v.toFixed(2)
  }
}

/** Ratios grouped by theme, newest period on the right. */
export function RatiosTable({ rows, currency, periodType }: { rows: RatioRow[]; currency: string; periodType: string }) {
  const cols = rows.length + 1
  return (
    <>
      <div className="table-wrap">
        <table className="table compact">
          <thead>
            <tr>
              <th style={{ minWidth: 260 }}>{periodType === 'TTM' ? 'Ratio · 12 months ending' : 'Ratio'}</th>
              {rows.map((r) => (
                <th key={r.period_end} className="r" title={`Period ending ${dateLabel(r.period_end)}`}>
                  {periodShort({ period_type: periodType, label: r.label, period_end: r.period_end })}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {RATIO_GROUPS.map((g) => (
              <Fragment key={g.title}>
                <tr>
                  <td colSpan={cols} style={sectionRow}>
                    {g.title}
                  </td>
                </tr>
                {g.rows.map((d) => (
                  <tr key={d.key}>
                    <td className="label-col" style={{ paddingLeft: 24, whiteSpace: 'nowrap' }}>
                      {d.label}
                      {d.help && (
                        <>
                          {' '}
                          <Help text={d.help} />
                        </>
                      )}
                    </td>
                    {rows.map((r) => {
                      const v = r[d.key]
                      return (
                        <td key={r.period_end} className={`num r ${typeof v === 'number' && v < 0 ? 'down' : ''}`}>
                          {ratioText(v, d, currency)}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-foot">
        {periodType === 'Q'
          ? 'Returns, turnover and debt-to-EBITDA use quarterly figures annualized (× 4); growth compares with the same quarter a year earlier. '
          : periodType === 'TTM'
            ? 'Growth compares with the 12 months ending a year earlier. '
            : 'Growth compares with the previous fiscal year. '}
        Money in {currency}.
      </div>
    </>
  )
}
