import { href } from '../../lib/router'
import { ago, dateTimeIL } from '../../lib/format'
import { Empty } from '../../ui'
import { condition, fmtValue, isSkip, reasonText, ruleSentence, valueNoun } from './model'
import type { Ctx, EvalResult, Rule, RuleState } from './model'

type Row = { ticker: string; state: string | null; value: number | null; reason: string | null; checked: string | null }

/** Latest known status per (rule, ticker): the stored state, overridden by this session's "Check now" result. */
function rowsFor(rule: Rule, states: RuleState[], evals: EvalResult[], evalAt: string | null): Row[] {
  const out = new Map<string, Row>()
  for (const s of states.filter((x) => x.rule_id === rule.id))
    out.set(s.ticker, { ticker: s.ticker, state: s.state, value: s.last_value, reason: s.last_reason, checked: s.last_eval_at })
  for (const e of evals.filter((x) => x.rule_id === rule.id)) {
    const prev = out.get(e.ticker)
    if (prev?.checked && evalAt && prev.checked > evalAt) continue
    out.set(e.ticker, { ticker: e.ticker, state: e.state ?? prev?.state ?? null, value: e.value ?? prev?.value ?? null, reason: e.action === 'skip' || e.action === 'error' ? `skip: ${e.reason ?? 'error'}` : e.reason, checked: evalAt })
  }
  return [...out.values()].sort((a, b) => a.ticker.localeCompare(b.ticker))
}

function status(rule: Rule, rows: Row[]): { label: string; cls: string } {
  if (!rule.enabled) return { label: 'Off', cls: '' }
  if (!rows.length) return { label: 'Not checked yet', cls: '' }
  if (rows.some((r) => r.state === 'fired')) return { label: 'Triggered', cls: 'warn' }
  if (rows.every((r) => isSkip(r.reason))) return { label: 'Can’t check', cls: 'warn' }
  return { label: 'Waiting', cls: 'ok' }
}

export default function RulesTable({
  rules,
  states,
  evals,
  evalAt,
  ctx,
  seriesUnit,
  onEdit,
  onToggle,
  onDelete,
  onCreate,
}: {
  rules: Rule[]
  states: RuleState[]
  evals: EvalResult[]
  evalAt: string | null
  ctx: Ctx
  seriesUnit: (id: string | null) => string
  onEdit: (r: Rule) => void
  onToggle: (r: Rule) => void
  onDelete: (r: Rule) => void
  onCreate: () => void
}) {
  if (!rules.length)
    return (
      <Empty title="No alert rules yet" actions={<button onClick={onCreate}>Create your first alert</button>}>
        An alert watches one condition — for example “AAPL upside to fair value above 25%” — and tells you when it becomes true.
      </Empty>
    )
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Rule</th>
            <th>Status</th>
            <th className="r">Latest value</th>
            <th>On</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => {
            const rows = rowsFor(r, states, r.enabled ? evals : [], evalAt)
            const st = status(r, rows)
            const unit = condition(r.rule_type)?.unit ?? 'level'
            const su = seriesUnit(r.series_id)
            const single = rows.length === 1 ? rows[0] : null
            const why = single ? reasonText(single.reason) : null
            const fired = rows.filter((x) => x.state === 'fired').map((x) => x.ticker)
            const skipped = rows.filter((x) => isSkip(x.reason))
            return (
              <tr key={r.id} style={{ opacity: r.enabled ? 1 : 0.6 }}>
                <td style={{ maxWidth: 520 }}>
                  {r.name && <div className="small muted">{r.name}</div>}
                  <div>{ruleSentence(r, ctx, su)}</div>
                  {r.enabled && why && <div className={`small ${why.tone === 'warn' ? 'warn-text' : 'muted'}`} style={{ marginTop: 2 }}>{why.text}{single && why.text.includes('fair value yet') && single.ticker !== 'ALL' ? <> <a href={href(`company/${single.ticker}/valuation`)}>Open valuation</a></> : null}</div>}
                  {r.enabled && rows.length > 1 && (
                    <details style={{ marginTop: 4 }}>
                      <summary className="small muted" style={{ cursor: 'pointer' }}>
                        {rows.length} companies{fired.length ? ` · triggered for ${fired.join(', ')}` : ''}
                        {skipped.length ? ` · ${skipped.length} can’t be checked` : ''}
                      </summary>
                      <table className="table compact" style={{ marginTop: 6 }}>
                        <tbody>
                          {rows.map((x) => (
                            <tr key={x.ticker}>
                              <td className="ticker">{x.ticker}</td>
                              <td className="r num">{fmtValue(unit, x.value, x.ticker, ctx, su)}</td>
                              <td className="small muted">{x.state === 'fired' ? 'Triggered' : (reasonText(x.reason)?.text ?? 'Waiting')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </details>
                  )}
                </td>
                <td>
                  <span className={`badge ${st.cls}`}>{st.label}</span>
                </td>
                <td className="r">
                  {single ? (
                    <>
                      <div className="num" title={valueNoun(r.rule_type)}>
                        {fmtValue(unit, single.value, single.ticker, ctx, su)}
                      </div>
                      {single.checked && (
                        <div className="xs faint" title={dateTimeIL(single.checked)}>
                          checked {ago(single.checked)}
                        </div>
                      )}
                    </>
                  ) : (
                    <span className="faint">{rows.length > 1 ? 'see list' : '—'}</span>
                  )}
                </td>
                <td>
                  <input type="checkbox" checked={r.enabled} onChange={() => onToggle(r)} aria-label={r.enabled ? 'Turn this alert off' : 'Turn this alert on'} title={r.enabled ? 'On — click to pause' : 'Off — click to turn on'} />
                </td>
                <td className="r nowrap">
                  <button className="ghost" onClick={() => onEdit(r)}>
                    Edit
                  </button>
                  <button className="ghost danger" onClick={() => onDelete(r)}>
                    Delete
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
