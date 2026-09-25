import type { CSSProperties } from 'react'
import { href } from '../../lib/router'
import { dateLabel, dayLabel, timeIL } from '../../lib/format'
import { Flag, Help, Importance } from '../../ui'
import {
  COUNTRY_NAME,
  NO_ACTUAL_TEXT,
  type CalEvent,
  type CentralBank,
  addDaysKey,
  dayHeading,
  dayKeyIL,
  evNum,
  isPast,
  keyLabel,
  mondayKey,
  plainTitle,
  releasedWithoutActual,
  surpriseText,
  surpriseTone,
  timeNY,
  todayKeyIL,
} from './model'

const dayHead: CSSProperties = { background: 'var(--surface-2)', fontWeight: 600, fontSize: 13, color: 'var(--text)' }

function HolidayBadges({ items }: { items: CalEvent[] }) {
  if (!items.length) return null
  return (
    <>
      {items.map((h) => (
        <span key={h.id} className="badge" style={{ marginLeft: 8, fontWeight: 400 }} title={h.title}>
          <Flag cc={h.country} /> {plainTitle(h.title).replace(/\s*\(public holiday\)/i, '')}
        </span>
      ))}
    </>
  )
}

/** Agenda grouped by Israel day: time, country, event, importance, actual / forecast / previous. */
export function Agenda({ days, events, holidays, onSelect, selectedId }: { days: string[]; events: CalEvent[]; holidays: CalEvent[]; onSelect: (id: number) => void; selectedId: number | null }) {
  const byDay = new Map<string, CalEvent[]>()
  for (const e of events) {
    const k = dayKeyIL(e.release_ts)
    byDay.set(k, [...(byDay.get(k) ?? []), e])
  }
  const holByDay = new Map<string, CalEvent[]>()
  for (const h of holidays) {
    const k = dayKeyIL(h.release_ts)
    holByDay.set(k, [...(holByDay.get(k) ?? []), h])
  }
  const today = todayKeyIL()
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th style={{ width: 96 }}>
              Time <span className="faint">(Israel)</span>
            </th>
            <th style={{ width: 130 }}>Country</th>
            <th>Event</th>
            <th style={{ width: 90 }}>
              Importance <Help text="Three dots = market-moving: the release regularly moves bonds, currencies or stocks." />
            </th>
            <th className="r" style={{ width: 110 }}>
              Actual
            </th>
            <th className="r" style={{ width: 96 }}>
              Forecast <Help text="The consensus forecast: the typical economist estimate collected before the release. Actual is green when above it, red when below." />
            </th>
            <th className="r" style={{ width: 96 }}>
              Previous
            </th>
          </tr>
        </thead>
        {days.map((day) => {
          const rows = byDay.get(day) ?? []
          return (
            <tbody key={day}>
              <tr>
                <td colSpan={7} style={{ ...dayHead, color: day === today ? 'var(--accent)' : dayHead.color }}>
                  {dayHeading(day, today)}
                  <HolidayBadges items={holByDay.get(day) ?? []} />
                </td>
              </tr>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="faint small">
                    Nothing scheduled that matches your filters.
                  </td>
                </tr>
              )}
              {rows.map((e) => (
                <EventRow key={e.id} e={e} onSelect={onSelect} selected={e.id === selectedId} />
              ))}
            </tbody>
          )
        })}
      </table>
    </div>
  )
}

function EventRow({ e, onSelect, selected }: { e: CalEvent; onSelect: (id: number) => void; selected: boolean }) {
  const v = e.values
  const hasNumbers = v.actual !== null || v.consensus !== null || v.previous !== null
  const past = isPast(e.release_ts)
  const tone = surpriseTone(v)
  return (
    <tr className={`click ${selected ? 'selected' : ''}`} onClick={() => onSelect(e.id)}>
      <td>
        <div className={`num ${past ? 'muted' : ''}`}>{timeIL(e.release_ts)}</div>
        <div className="faint xs num">{timeNY(e.release_ts)} New York</div>
      </td>
      <td className="nowrap">
        <Flag cc={e.country} /> {COUNTRY_NAME[e.country] ?? e.country}
      </td>
      <td>
        {plainTitle(e.title)}
        {e.end_ts && <span className="faint small"> · until {dayLabel(e.end_ts)}</span>}
        {e.affected_tickers.length > 0 && e.kind !== 'ipo' && <span className="faint small ticker"> {e.affected_tickers.slice(0, 3).join(' ')}</span>}
      </td>
      <td>
        <Importance level={e.importance} />
      </td>
      <td className="r">
        {hasNumbers &&
          (v.actual !== null ? (
            <span className={`num ${tone}`} title={surpriseText(v)}>
              {evNum(v.actual, v.unit)}
            </span>
          ) : releasedWithoutActual(e) ? (
            <span className="faint xs" title={NO_ACTUAL_TEXT}>
              Released · n/a
            </span>
          ) : (
            <span className="faint">—</span>
          ))}
      </td>
      <td className="r num">{hasNumbers ? evNum(v.consensus, v.unit) : ''}</td>
      <td className="r num muted">{hasNumbers ? evNum(v.previous, v.unit) : ''}</td>
    </tr>
  )
}

/** Month grid: per day, the number of market-moving events and any market holidays. */
export function MonthGrid({ month, events, holidays, selected, onPick }: { month: string; events: CalEvent[]; holidays: CalEvent[]; selected: string | null; onPick: (day: string) => void }) {
  const start = mondayKey(month)
  const cells = Array.from({ length: 42 }, (_, i) => addDaysKey(start, i))
  const high = new Map<string, number>()
  for (const e of events) if (e.importance >= 3) high.set(dayKeyIL(e.release_ts), (high.get(dayKeyIL(e.release_ts)) ?? 0) + 1)
  const hol = new Map<string, CalEvent[]>()
  for (const h of holidays) hol.set(dayKeyIL(h.release_ts), [...(hol.get(dayKeyIL(h.release_ts)) ?? []), h])
  const today = todayKeyIL()
  const ym = month.slice(0, 7)
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 4 }}>
        {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => (
          <div key={d} className="faint small" style={{ padding: '0 6px 4px' }}>
            {d}
          </div>
        ))}
        {cells.map((d) => {
          const n = high.get(d) ?? 0
          const hs = hol.get(d) ?? []
          const inMonth = d.slice(0, 7) === ym
          const isSel = d === selected
          return (
            <button
              key={d}
              onClick={() => onPick(d)}
              style={{
                minHeight: 78,
                textAlign: 'left',
                verticalAlign: 'top',
                display: 'flex',
                flexDirection: 'column',
                gap: 3,
                padding: '6px 8px',
                whiteSpace: 'normal',
                opacity: inMonth ? 1 : 0.4,
                background: isSel ? 'var(--accent-soft)' : 'var(--surface)',
                borderColor: isSel ? 'var(--accent)' : d === today ? 'var(--border-strong)' : 'var(--border)',
              }}
            >
              <span className={`num small ${d === today ? 'accent strong' : 'muted'}`}>{Number(d.slice(8))}</span>
              {n > 0 && (
                <span className="small" style={{ color: 'var(--text)' }}>
                  <Importance level={3} /> {n} market-moving
                </span>
              )}
              {hs.slice(0, 2).map((h) => (
                <span key={h.id} className="xs faint" title={h.title} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>
                  <Flag cc={h.country} /> {plainTitle(h.title).replace(/\s*\(public holiday\)/i, '')}
                </span>
              ))}
              {hs.length > 2 && <span className="xs faint">+{hs.length - 2} more holidays</span>}
            </button>
          )
        })}
      </div>
    </div>
  )
}

const RATE_NAME: Record<string, [string, string?]> = {
  fed: ['Effective fed funds rate', 'The Fed sets a target range; this is the overnight rate banks actually paid, which sits inside that range.'],
  ecb: ['Deposit facility rate', "The ECB's main policy rate: what banks earn on overnight deposits at the ECB."],
  boi: ['Bank of Israel rate'],
  boe: ['Bank Rate'],
  boj: ['Policy rate'],
  boc: ['Overnight rate target'],
  rba: ['Cash rate target'],
  snb: ['SNB policy rate'],
}

function inDays(n: number | null, ts?: string | null): string {
  if (n === null) return '—'
  if (n === 0) return ts && new Date(`${ts}Z`).getTime() < Date.now() ? 'Earlier today' : 'Today'
  if (n === 1) return 'Tomorrow'
  return `in ${n} days`
}

/** Central banks: current policy rate (from the linked data series), next decision, days to go. */
export function CentralBanksTable({ banks }: { banks: CentralBank[] }) {
  const rows = [...banks].sort((a, b) => (a.days_to_go ?? 9999) - (b.days_to_go ?? 9999))
  const year = new Date().getFullYear()
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Central bank</th>
            <th className="r">Policy rate</th>
            <th>Rate as of</th>
            <th>Next decision</th>
            <th>Time (Israel)</th>
            <th className="r">Countdown</th>
            <th>Last decision</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => {
            const [rateName, rateHelp] = RATE_NAME[b.bank] ?? ['Policy rate']
            const monthly = (b.rate_series ?? '').startsWith('bis:')
            const nextYear = b.next_ts ? new Date(`${b.next_ts}Z`).getFullYear() : year
            return (
              <tr key={b.bank}>
                <td>
                  <div className="nowrap">
                    <Flag cc={b.country} /> {b.name}
                  </div>
                  <div className="faint xs">
                    {rateName}
                    {rateHelp && <Help text={rateHelp} />}
                  </div>
                </td>
                <td className="r">
                  {b.current_rate === null ? (
                    <span className="faint" title="No stored data for the linked rate series yet">
                      —
                    </span>
                  ) : b.rate_series ? (
                    <a className="num" href={href(`series/${b.rate_series}`)} title="Open the rate history">
                      {b.current_rate.toFixed(2)}%
                    </a>
                  ) : (
                    <span className="num">{b.current_rate.toFixed(2)}%</span>
                  )}
                </td>
                <td className="muted small nowrap">
                  {b.rate_ts ? (monthly ? `End of ${new Date(`${b.rate_ts}Z`).toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })} (monthly data)` : dateLabel(b.rate_ts)) : '—'}
                </td>
                <td className="nowrap">
                  {b.next_ts ? `${dayLabel(b.next_ts)}${nextYear !== year ? ` ${nextYear}` : ''}` : <span className="faint">No date published</span>}
                  {b.sep && <div className="faint xs">With new economic projections</div>}
                  {b.verified === false && <div className="warn-text xs">Date not yet confirmed by the bank</div>}
                </td>
                <td className="num">{b.next_ts ? timeIL(b.next_ts) : '—'}</td>
                <td className={`r nowrap ${b.days_to_go !== null && b.days_to_go <= 7 ? 'accent' : ''}`}>{inDays(b.days_to_go, b.next_ts)}</td>
                <td className="muted small nowrap">{b.last_date ? keyLabel(b.last_date, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'}</td>
                <td className="small">
                  {b.source_url && (
                    <a href={b.source_url} target="_blank" rel="noreferrer">
                      Official calendar ↗
                    </a>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
