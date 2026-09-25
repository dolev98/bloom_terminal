import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { dateLabel, num } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Card, Empty, Help, Loading, Segmented } from '../../ui'
import CandleChart, { overlayColor, toTime, type Overlay } from '../../components/charts/CandleChart'

type Ohlcv = { ticker: string; n: number; ts: string[]; open: number[]; high: number[]; low: number[]; close: number[]; volume: number[]; indicators: string[]; [k: string]: any }
type Hit = { ticker: string; name: string }

const RANGES: [string, number][] = [
  ['1M', 31],
  ['3M', 92],
  ['6M', 183],
  ['YTD', -1],
  ['1Y', 366],
  ['5Y', 1827],
  ['Max', 0],
]
const INDICATORS: { id: string; label: string; help: string; cols: string[] }[] = [
  { id: 'sma50', label: '50-day average', help: 'Average closing price over the last 50 trading days.', cols: ['sma50'] },
  { id: 'sma200', label: '200-day average', help: 'Average closing price over the last 200 trading days — a common long-term trend line.', cols: ['sma200'] },
  { id: 'bb20', label: 'Bollinger bands', help: '20-day average ± 2 standard deviations; price near a band means an unusually large move.', cols: ['bb_upper', 'bb_mid', 'bb_lower'] },
  { id: 'rsi14', label: 'RSI', help: 'Relative Strength Index (14 days), 0–100. Above 70 is often called overbought, below 30 oversold. Shown in a separate pane.', cols: ['rsi14'] },
  { id: 'macd', label: 'MACD', help: 'Difference between the 12- and 26-day exponential averages, with a 9-day signal line. Shown in a separate pane.', cols: ['macd', 'macd_signal'] },
]
const COL_LABEL: Record<string, string> = { sma50: '50-day avg', sma200: '200-day avg', bb_upper: 'Upper band', bb_mid: '20-day avg', bb_lower: 'Lower band', rsi14: 'RSI', macd: 'MACD', macd_signal: 'Signal' }

function startDate(range: string): string | undefined {
  const d = RANGES.find((r) => r[0] === range)![1]
  if (d === 0) return undefined
  const now = new Date()
  if (d === -1) return `${now.getFullYear()}-01-01`
  return new Date(now.getTime() - d * 86400e3).toISOString().slice(0, 10)
}

function CompareInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDq(q.trim()), 180)
    return () => clearTimeout(t)
  }, [q])
  const hits = useQuery({ queryKey: ['search', dq], queryFn: () => api<Hit[]>(`/api/market/search?q=${encodeURIComponent(dq)}&limit=5`), enabled: dq.length >= 1 })
  if (value)
    return (
      <span className="chip on" onClick={() => onChange('')} title="Remove comparison">
        vs {value} ✕
      </span>
    )
  return (
    <div style={{ position: 'relative' }}>
      <input placeholder="Compare with… (e.g. SPY)" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && q.trim() && (onChange((hits.data?.find((h) => h.ticker === q.trim().toUpperCase())?.ticker ?? q.trim()).toUpperCase()), setQ(''))} style={{ width: 190 }} />
      {dq && (hits.data ?? []).length > 0 && (
        <div className="card" style={{ position: 'absolute', top: 36, left: 0, width: 340, zIndex: 20, padding: 6 }}>
          {(hits.data ?? []).map((h) => (
            <div key={h.ticker} className="nav-recent" onMouseDown={() => (onChange(h.ticker), setQ(''))}>
              <span className="ticker">{h.ticker}</span> {h.name}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Chart({ ticker }: { ticker: string }) {
  const qc = useQueryClient()
  const [range, setRange] = useState('1Y')
  const [type, setType] = useState<'Candles' | 'Line'>('Candles')
  const [inds, setInds] = useState<string[]>(['sma50', 'sma200'])
  const [adjusted, setAdjusted] = useState(false)
  const [compare, setCompare] = useState('')
  const start = startDate(range)
  const ohlcv = useQuery({
    queryKey: ['ohlcv', ticker, start, inds.join(','), adjusted],
    queryFn: () => api<Ohlcv>(`/api/market/ohlcv/${encodeURIComponent(ticker)}?indicators=${inds.join(',')}&adjusted=${adjusted}${start ? `&start=${start}` : ''}&limit=20000`),
  })
  const cmp = useQuery({
    queryKey: ['compare', ticker, compare, start],
    queryFn: () => api<{ n: number; ts: string[]; series: Record<string, number[]> }>(`/api/market/compare?tickers=${encodeURIComponent(`${ticker},${compare}`)}${start ? `&start=${start}` : ''}`),
    enabled: !!compare,
  })
  const load = useMutation({
    mutationFn: (t: string) => api<{ status: string; error?: string }>(`/api/market/ohlcv/${encodeURIComponent(t)}/refresh`, { method: 'POST' }),
    onSuccess: (r) => {
      if (r.status === 'error') toast(`Could not load prices: ${r.error}`, 'err')
      qc.invalidateQueries({ queryKey: ['ohlcv', ticker] })
      qc.invalidateQueries({ queryKey: ['compare', ticker] })
    },
  })

  const d = ohlcv.data
  const bars = useMemo(() => (d ? d.ts.map((t, i) => ({ time: toTime(t), open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] })) : []), [d])
  const volume = useMemo(() => (d ? d.ts.map((t, i) => ({ time: toTime(t), value: d.volume[i] ?? 0, color: d.close[i] >= d.open[i] ? 'rgba(47,191,113,.35)' : 'rgba(240,86,90,.35)' })) : []), [d])
  const overlays = useMemo<Overlay[]>(() => {
    if (!d) return []
    const out: Overlay[] = []
    let ci = 1
    const hasRsi = d.indicators.includes('rsi14')
    for (const c of d.indicators) {
      if (c === 'macd_hist') continue
      const pane = c === 'rsi14' ? 1 : c === 'macd' || c === 'macd_signal' ? (hasRsi ? 2 : 1) : 0
      const arr: number[] = d[c]
      if (!arr) continue
      out.push({ name: COL_LABEL[c] ?? c, color: overlayColor(ci++), pane, data: d.ts.map((t, i) => ({ time: toTime(t), value: arr[i] })).filter((p) => p.value !== null && p.value !== undefined && !Number.isNaN(p.value)) })
    }
    return out
  }, [d])

  const compareBars = useMemo(() => {
    const c = cmp.data
    if (!c || !c.n || !c.series[ticker]) return null
    return {
      bars: c.ts.map((t, i) => ({ time: toTime(t), open: c.series[ticker][i], high: c.series[ticker][i], low: c.series[ticker][i], close: c.series[ticker][i] })),
      overlays: Object.keys(c.series)
        .filter((k) => k !== ticker)
        .map((k, i) => ({ name: k, color: overlayColor(i + 1), data: c.ts.map((t, j) => ({ time: toTime(t), value: c.series[k][j] })) })),
      endA: c.series[ticker][c.n - 1],
      endB: c.series[compare]?.[c.n - 1],
    }
  }, [cmp.data, ticker, compare])

  const first = d?.n ? d.close[0] : null
  const lastC = d?.n ? d.close[d.n - 1] : null
  const rangeChange = first && lastC ? (lastC / first - 1) * 100 : null

  return (
    <Card
      title={compare ? `${ticker} vs ${compare}` : 'Price chart'}
      hint={compare ? 'both start at 100' : rangeChange !== null ? `${rangeChange > 0 ? '+' : ''}${num(rangeChange, 1)}% over the period shown` : undefined}
      foot={
        d?.n ? (
          <>
            Daily bars {dateLabel(d.ts[0])} – {dateLabel(d.ts[d.n - 1])} · Yahoo Finance{adjusted ? ' · adjusted for dividends and splits' : ''} ·{' '}
            <a href={`/api/market/ohlcv/${encodeURIComponent(ticker)}/export.csv?adjusted=${adjusted}`}>Download CSV</a>
          </>
        ) : undefined
      }
    >
      <div className="toolbar">
        <Segmented value={range} options={RANGES.map((r) => r[0])} onChange={setRange} />
        {!compare && <Segmented value={type} options={['Candles', 'Line'] as const} onChange={setType} />}
        <label className="check" title="Adjusts past prices for dividends and splits, so the chart shows total return.">
          <input type="checkbox" checked={adjusted} onChange={(e) => setAdjusted(e.target.checked)} /> Include dividends
        </label>
        <span className="spacer" />
        <CompareInput value={compare} onChange={setCompare} />
      </div>
      {!compare && (
        <div className="row" style={{ marginBottom: 12 }}>
          <span className="small muted">Indicators:</span>
          {INDICATORS.map((ind) => (
            <span key={ind.id} className={`chip ${inds.includes(ind.id) ? 'on' : ''}`} onClick={() => setInds((x) => (x.includes(ind.id) ? x.filter((y) => y !== ind.id) : [...x, ind.id]))} title={ind.help}>
              {ind.label}
            </span>
          ))}
          <Help text="Hover an indicator for what it means. RSI and MACD open in separate panes below the price." />
        </div>
      )}
      {ohlcv.isLoading ? (
        <Loading />
      ) : !d?.n ? (
        <Empty title="No price history stored yet" actions={<button className="primary" onClick={() => load.mutate(ticker)} disabled={load.isPending}>{load.isPending ? 'Loading…' : 'Load price history'}</button>} />
      ) : compare ? (
        cmp.isLoading ? (
          <Loading />
        ) : !compareBars ? (
          <Empty title={`No overlapping prices for ${compare}`} actions={<button onClick={() => load.mutate(compare)} disabled={load.isPending}>Load {compare} prices</button>}>
            Price history for {compare} hasn’t been downloaded yet.
          </Empty>
        ) : (
          <>
            <div className="row small" style={{ marginBottom: 8 }}>
              <span>
                <span style={{ color: overlayColor(0) }}>■</span> {ticker}: <b className="num">{num(compareBars.endA, 1)}</b>
              </span>
              <span>
                <span style={{ color: overlayColor(1) }}>■</span> {compare}: <b className="num">{num(compareBars.endB, 1)}</b>
              </span>
              <span className="faint">(100 = start of period; above 100 = gain)</span>
            </div>
            <CandleChart bars={compareBars.bars} overlays={compareBars.overlays} height={460} lineMode />
          </>
        )
      ) : (
        <>
          {overlays.length > 0 && (
            <div className="row small" style={{ marginBottom: 6 }}>
              {overlays.map((o) => (
                <span key={o.name}>
                  <span style={{ color: o.color }}>■</span> {o.name}
                </span>
              ))}
            </div>
          )}
          <CandleChart bars={bars} volume={type === 'Candles' ? volume : undefined} overlays={overlays} height={overlays.some((o) => (o.pane ?? 0) > 0) ? 560 : 460} lineMode={type === 'Line'} />
        </>
      )}
    </Card>
  )
}
