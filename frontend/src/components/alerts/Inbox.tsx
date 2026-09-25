import { useState } from 'react'
import { href } from '../../lib/router'
import { ago, dateTimeIL } from '../../lib/format'
import { Empty } from '../../ui'
import { condition, fmtValue, refLabel, valueNoun } from './model'
import type { Ctx, Fired } from './model'

const utc = (s: string) => new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`).getTime()
const isSnoozed = (h: Fired) => !!h.snoozed_until && utc(h.snoozed_until) > Date.now()
const sourceName = (s: string | null) => (!s ? 'unknown source' : s.startsWith('yf') ? 'Yahoo Finance, delayed' : s.startsWith('finnhub') ? 'Finnhub' : s)

function delivery(h: Fired): { text: string; cls: string } | null {
  const t = h.delivered?.telegram
  if (t === undefined) return null
  if (t === true) return { text: 'Sent to Telegram', cls: 'muted' }
  if (t === 'digest') return { text: 'Telegram: held for the 09:00 digest (quiet hours)', cls: 'muted' }
  return { text: 'Telegram message could not be sent', cls: 'down' }
}

/** "What fired, when, value vs threshold", with acknowledge / snooze. Handled alerts are hidden by default. */
export default function Inbox({ items, ctx, onAck, onSnooze, busy }: { items: Fired[]; ctx: Ctx; onAck: (id: number) => void; onSnooze: (id: number) => void; busy: boolean }) {
  const [showHandled, setShowHandled] = useState(false)
  const open = items.filter((h) => !h.acknowledged_at && !isSnoozed(h))
  const handled = items.length - open.length
  const shown = showHandled ? items : open
  if (!items.length)
    return <Empty title="Nothing has fired yet">When one of your rules becomes true it shows up here{' '}(and on Telegram if you chose it).</Empty>
  return (
    <div>
      {!shown.length && <div className="muted" style={{ padding: '8px 0' }}>You’re all caught up — no alerts waiting for you.</div>}
      <div className="list">
        {shown.map((h) => {
          const c = condition(h.rule_type)
          const unit = c?.unit ?? 'level'
          const done = !!h.acknowledged_at || isSnoozed(h)
          const d = delivery(h)
          const ref = h.payload?.reference
          return (
            <div key={h.id} className="list-item" style={{ opacity: done ? 0.6 : 1 }}>
              <div className="li-time" title={dateTimeIL(h.fired_at)}>
                {ago(h.fired_at)}
              </div>
              <div className="li-body">
                <div className="li-title">
                  <a className="ticker" href={href(`company/${h.ticker}`)} title={ctx.names[h.ticker] ?? h.ticker}>
                    {h.ticker}
                  </a>{' '}
                  {valueNoun(h.rule_type)} reached <b className="num">{fmtValue(unit, h.value, h.ticker, ctx)}</b>
                  {h.threshold !== null && c && !c.noThreshold && (
                    <span className="muted">
                      {' '}
                      — your threshold is <span className="num">{fmtValue(unit, h.threshold, h.ticker, ctx, '', true)}</span>
                    </span>
                  )}
                </div>
                <div className="li-meta">
                  {h.price !== null && (
                    <span>
                      Price <span className="num">{fmtValue('price', h.price, h.ticker, ctx)}</span> ({sourceName(h.price_source)}
                      {h.price_ts ? `, ${dateTimeIL(h.price_ts)}` : ''})
                    </span>
                  )}
                  {h.fair_value !== null && (
                    <span>
                      {h.rule_type === 'consensus_gap_gt' ? 'Analyst target' : `Fair value${ref ? ` (${refLabel(ref)})` : ''}`} <span className="num">{fmtValue('price', h.fair_value, h.ticker, ctx)}</span>
                    </span>
                  )}
                  {d && <span className={d.cls}>{d.text}</span>}
                  {h.acknowledged_at && <span>Acknowledged {ago(h.acknowledged_at)}</span>}
                  {!h.acknowledged_at && isSnoozed(h) && <span>Snoozed until {dateTimeIL(h.snoozed_until)}</span>}
                </div>
              </div>
              {!done && (
                <div className="row nowrap" style={{ gap: 4 }}>
                  <button onClick={() => onAck(h.id)} disabled={busy} title="Mark as seen and hide it">
                    Acknowledge
                  </button>
                  <button className="ghost" onClick={() => onSnooze(h.id)} disabled={busy} title="Hide it for 24 hours">
                    Snooze 24 h
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>
      {handled > 0 && (
        <button className="ghost small" style={{ marginTop: 6 }} onClick={() => setShowHandled((v) => !v)}>
          {showHandled ? 'Hide handled alerts' : `Show ${handled} handled alert${handled === 1 ? '' : 's'}`}
        </button>
      )}
    </div>
  )
}
