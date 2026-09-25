import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { href } from '../../lib/router'
import { dateLabel, dayLabel, timeIL } from '../../lib/format'
import { ErrorBox, Flag, Help, Importance, Loading, Segmented } from '../../ui'
import EChart, { AXIS_STYLE, baseOption, type EChartsOption } from '../charts/EChart'
import { COUNTRY_NAME, type EventDetail, evNum, isPast, plainTitle, seriesNum, surpriseText, surpriseTone, timeNY, whatItMeasures } from './model'

const IMPORTANCE_WORDS = ['', 'Low importance', 'Medium importance', 'Market-moving']
const SOURCE_NAMES: Record<string, string> = {
  central_banks: 'central bank calendars',
  forexfactory: 'ForexFactory',
  treasury: 'TreasuryDirect',
  nager: 'Nager.Date',
  hebcal: 'Hebcal.com (CC BY 4.0)',
  fred_releases: 'FRED release calendar',
  bls_bea: 'BLS / BEA',
  fmp: 'Financial Modeling Prep',
  cbs_rules: 'CBS Israel release rules',
  opex: 'exchange rules',
  geo: 'geopolitical calendar',
  finnhub_corp: 'Finnhub',
  user: 'you',
}
const sourceName = (s: string | null | undefined) =>
  (s ?? '')
    .split(',')
    .filter(Boolean)
    .map((x) => SOURCE_NAMES[x] ?? x)
    .join(', ')

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (ev: KeyboardEvent) => ev.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
}

const monthLabel = (s: string) => new Date(s.endsWith('Z') ? s : `${s}Z`).toLocaleDateString('en-GB', { month: 'short', year: '2-digit', timeZone: 'UTC' })

export function EventDrawer({ id, onClose, onDeleted }: { id: number; onClose: () => void; onDeleted: () => void }) {
  useEscape(onClose)
  const q = useQuery({ queryKey: ['cal-event', id], queryFn: () => api<EventDetail>(`/api/calendar/events/${id}`) })
  const e = q.data

  const historyOpt = useMemo<EChartsOption | null>(() => {
    const past = (e?.past_surprises ?? []).filter((p) => p.actual !== null)
    if (past.length < 2) return null
    return {
      grid: { left: 44, right: 12, top: 28, bottom: 36 },
      legend: { ...(baseOption.legend as object), data: ['Forecast', 'Actual'] },
      xAxis: { type: 'category', data: past.map((p) => p.reference_period || dayLabel(p.release_ts)), ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, rotate: 30 } },
      yAxis: { type: 'value', scale: true, ...AXIS_STYLE },
      series: [
        { name: 'Forecast', type: 'bar', data: past.map((p) => p.consensus), itemStyle: { color: '#626b7a', borderRadius: [3, 3, 0, 0] }, barGap: '10%' },
        { name: 'Actual', type: 'bar', data: past.map((p) => p.actual), itemStyle: { color: '#f5a524', borderRadius: [3, 3, 0, 0] } },
      ],
    }
  }, [e])

  if (!e)
    return (
      <aside className="drawer">
        <div className="row between" style={{ marginBottom: 12 }}>
          <span className="muted">Event details</span>
          <button className="ghost icon" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {q.error ? <ErrorBox error={q.error} what="this event" /> : <Loading lines={6} />}
      </aside>
    )

  const v = e.values
  const hasNumbers = v.actual !== null || v.consensus !== null || v.previous !== null
  const measures = whatItMeasures(e)
  const tone = surpriseTone(v)
  const when =
    e.kind === 'holiday'
      ? `${dayLabel(e.release_ts)} · all day`
      : `${dayLabel(e.release_ts)}, ${timeIL(e.release_ts)} Israel time · ${timeNY(e.release_ts)} New York`

  return (
    <aside className="drawer" aria-label="Event details">
      <div className="row between nowrap" style={{ alignItems: 'flex-start', marginBottom: 6 }}>
        <h2 style={{ fontSize: 17, lineHeight: 1.3 }}>
          <Flag cc={e.country} /> {plainTitle(e.title)}
        </h2>
        <button className="ghost icon" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <div className="muted small">
        {COUNTRY_NAME[e.country] ?? e.country} · {when}
        {e.end_ts && <> · until {dayLabel(e.end_ts)}, {timeIL(e.end_ts)}</>}
      </div>
      <div className="row small" style={{ marginTop: 6 }}>
        <Importance level={e.importance} />
        <span className="muted">{IMPORTANCE_WORDS[e.importance] ?? ''}</span>
      </div>

      {measures && (
        <>
          <div className="section-title">What it measures</div>
          <p className="small" style={{ color: 'var(--text-2)' }}>
            {measures}
          </p>
        </>
      )}

      {hasNumbers && (
        <>
          <div className="section-title">{v.actual === null && !isPast(e.release_ts) ? 'Expectations' : 'Result'}</div>
          <div className="stats">
            <div className="stat">
              <div className="k">Actual</div>
              <div className={`v num ${tone}`}>{evNum(v.actual, v.unit)}</div>
            </div>
            <div className="stat">
              <div className="k">
                Forecast
                <Help text="The consensus forecast: the typical economist estimate collected before the release." />
              </div>
              <div className="v num">{evNum(v.consensus, v.unit)}</div>
            </div>
            <div className="stat">
              <div className="k">Previous</div>
              <div className="v num">{evNum(v.previous, v.unit)}</div>
            </div>
          </div>
          <div className={`small ${tone || 'muted'}`} style={{ marginTop: 6 }}>
            {v.actual === null && !isPast(e.release_ts) ? 'Not released yet.' : surpriseText(v)}
          </div>
        </>
      )}

      <div className="section-title">Past releases</div>
      {historyOpt ? (
        <EChart option={historyOpt} height={170} />
      ) : (
        <div className="faint small">No earlier releases of this event with results are stored yet.</div>
      )}

      {e.linked.length > 0 && <div className="section-title">Related data</div>}
      {e.linked.map((l) => (
        <LinkedSeries key={l.series_id} l={l} />
      ))}

      {e.affected_tickers.length > 0 && (
        <>
          <div className="section-title">Affected tickers</div>
          <div className="row">
            {e.affected_tickers.map((t) => (
              <a key={t} className="chip ticker" href={href(`company/${t}`)}>
                {t}
              </a>
            ))}
          </div>
        </>
      )}

      {e.notes && (
        <>
          <div className="section-title">Notes</div>
          <p className="small muted">{e.notes}</p>
        </>
      )}

      {e.source_provider === 'user' && (
        <button
          className="danger"
          style={{ marginTop: 16 }}
          onClick={async () => {
            if (!confirm('Delete this event?')) return
            await api(`/api/calendar/events/${e.id}`, { method: 'DELETE' })
            onDeleted()
          }}
        >
          Delete this event
        </button>
      )}

      <div className="card-foot" style={{ marginTop: 18 }}>
        Schedule: {sourceName(e.source_provider)}
        {v.consensus_source ? ` · Forecast: ${sourceName(v.consensus_source)}` : ''}
        {e.source_url && (
          <>
            {' · '}
            <a href={e.source_url} target="_blank" rel="noreferrer">
              Official page ↗
            </a>
          </>
        )}
      </div>
    </aside>
  )
}

function LinkedSeries({ l }: { l: EventDetail['linked'][number] }) {
  const opt = useMemo<EChartsOption>(
    () => ({
      grid: { left: 48, right: 10, top: 8, bottom: 22 },
      legend: { show: false },
      tooltip: { ...(baseOption.tooltip as object), trigger: 'axis', valueFormatter: (x) => seriesNum(x as number, l.unit) },
      xAxis: { type: 'category', data: l.sparkline.ts.map(monthLabel), ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, hideOverlap: true } },
      yAxis: { type: 'value', scale: true, ...AXIS_STYLE, axisLabel: { ...AXIS_STYLE.axisLabel, formatter: (x: number) => seriesNum(x, l.unit) } },
      series: [{ type: 'line', data: l.sparkline.value, showSymbol: false, lineStyle: { width: 2, color: '#5aa9ff' }, itemStyle: { color: '#5aa9ff' } }],
    }),
    [l],
  )
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="row between">
        <span className="small" title={l.series_id}>
          {l.name}
        </span>
        <span className="num small">
          {seriesNum(l.last, l.unit)} <span className="faint">({l.last_ts ? dateLabel(l.last_ts) : 'no data'})</span>
        </span>
      </div>
      {l.sparkline.value.length > 1 ? <EChart option={opt} height={110} /> : <div className="faint small">No stored observations yet.</div>}
      <a className="small" href={href(`series/${l.series_id}`)}>
        Open the full series →
      </a>
    </div>
  )
}

const COUNTRIES_FOR_FORM = ['IL', 'US', 'EA', 'GB', 'JP', 'CN', 'CA', 'CH', 'AU', 'GLOBAL']

/** "Add event / risk window" form in a side drawer. Times are entered in the browser's local time zone. */
export function AddEventDrawer({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  useEscape(onClose)
  const [kind, setKind] = useState<'risk_window' | 'geo'>('risk_window')
  const [f, setF] = useState({ title: '', country: 'IL', start: '', end: '', tickers: '', notes: '' })
  const [importance, setImportance] = useState<'1' | '2' | '3'>('2')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f) => (ev: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setF({ ...f, [k]: ev.target.value })
  const toUtc = (local: string) => (local ? new Date(local).toISOString() : null)
  const canSave = f.title.trim() && f.start && (kind !== 'risk_window' || !f.end || f.end > f.start)

  const save = async () => {
    setBusy(true)
    setErr(null)
    try {
      await api('/api/calendar/events', {
        method: 'POST',
        json: {
          title: f.title.trim(),
          kind,
          country: f.country,
          release_ts: toUtc(f.start),
          end_ts: kind === 'risk_window' ? toUtc(f.end) : null,
          importance: Number(importance),
          affected_tickers: f.tickers.split(/[,\s]+/).filter(Boolean),
          notes: f.notes.trim() || null,
        },
      })
      onSaved()
    } catch (e) {
      setErr(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="drawer" aria-label="Add event">
      <div className="row between" style={{ marginBottom: 4 }}>
        <h2 style={{ fontSize: 17 }}>Add an event or risk window</h2>
        <button className="ghost icon" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <p className="small muted" style={{ marginBottom: 14 }}>
        A risk window marks a period you want to treat carefully (e.g. an election or a budget vote). It shows on the calendar and can be used to split correlations by regime.
      </p>
      <div className="stack" style={{ gap: 12 }}>
        <Segmented value={kind} options={[['risk_window', 'Risk window'], ['geo', 'One-off event']]} onChange={setKind} />
        <label className="stack" style={{ gap: 4 }}>
          <span className="small muted">Title</span>
          <input value={f.title} onChange={set('title')} placeholder="e.g. Knesset budget vote" autoFocus />
        </label>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small muted">Country</span>
          <select value={f.country} onChange={set('country')}>
            {COUNTRIES_FOR_FORM.map((c) => (
              <option key={c} value={c}>
                {COUNTRY_NAME[c] ?? c}
              </option>
            ))}
          </select>
        </label>
        <div className="grid-2" style={{ gap: 10 }}>
          <label className="stack" style={{ gap: 4 }}>
            <span className="small muted">Starts (your local time)</span>
            <input type="datetime-local" value={f.start} onChange={set('start')} />
          </label>
          {kind === 'risk_window' && (
            <label className="stack" style={{ gap: 4 }}>
              <span className="small muted">Ends (optional)</span>
              <input type="datetime-local" value={f.end} min={f.start || undefined} onChange={set('end')} />
            </label>
          )}
        </div>
        <div className="stack" style={{ gap: 4 }}>
          <span className="small muted">Importance</span>
          <Segmented value={importance} options={[['1', 'Low'], ['2', 'Medium'], ['3', 'Market-moving']]} onChange={setImportance} />
        </div>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small muted">Affected tickers (optional)</span>
          <input value={f.tickers} onChange={set('tickers')} placeholder="TEVA.TA, ESLT" />
        </label>
        <label className="stack" style={{ gap: 4 }}>
          <span className="small muted">Notes (optional)</span>
          <textarea rows={3} value={f.notes} onChange={set('notes')} />
        </label>
        {err && <ErrorBox error={err} what="the event" />}
        <div className="row">
          <button className="primary" disabled={!canSave || busy} onClick={save}>
            {busy ? 'Saving…' : 'Add to calendar'}
          </button>
          <button className="ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </aside>
  )
}
