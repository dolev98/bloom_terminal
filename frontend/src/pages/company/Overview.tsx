import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { href } from '../../lib/router'
import { useQuote } from '../../lib/ws'
import { isIndexLike } from '../../lib/hooks'
import { dateLabel, dayLabel, money, num, pct, price, ratioPct, timeIL, times } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Card, Change, Empty, Loading, Segmented, Stat } from '../../ui'
import CandleChart, { toTime } from '../../components/charts/CandleChart'

type Ohlcv = { n: number; ts: string[]; close: number[]; open: number[]; high: number[]; low: number[] }
type Stm = { periods: { label: string; period_end: string }[]; fields: Record<string, (number | null)[]>; currency: string }
type Val = { reference: { name: string; value: number | null; upside: number | null; date: string } | null; price: number | null }
type NewsItem = { id: number; title: string; url: string; publisher?: string | null; first_seen: string; importance: number }
type CalEvent = { id: number; kind: string; title: string; release_ts: string; event_key: string }

const RANGES: [string, string, number][] = [
  ['1M', '1 month', 31],
  ['6M', '6 months', 183],
  ['1Y', '1 year', 366],
  ['5Y', '5 years', 1827],
]

/** % change from the last close on or before `daysAgo` (or Jan 1 for YTD) to the latest close. */
function perf(d: Ohlcv | undefined, from: Date): number | null {
  if (!d || d.n < 2) return null
  const cutoff = from.getTime()
  let i = -1
  for (let k = d.n - 1; k >= 0; k--) {
    if (new Date(d.ts[k] + 'Z').getTime() <= cutoff) {
      i = k
      break
    }
  }
  if (i < 0) return null
  return (d.close[d.n - 1] / d.close[i] - 1) * 100
}

function last(s: Stm | undefined, field: string): { v: number | null; label: string | null; end: string | null } {
  if (!s) return { v: null, label: null, end: null }
  const arr = s.fields[field] ?? []
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && arr[i] !== undefined) return { v: arr[i], label: s.periods[i].label, end: s.periods[i].period_end }
  return { v: null, label: null, end: null }
}

export default function Overview({ ticker, kind, otherListing }: { ticker: string; kind: string; otherListing?: string }) {
  const qc = useQueryClient()
  const [range, setRange] = useState('1Y')
  const q = useQuote(ticker)
  const bars = useQuery({ queryKey: ['ohlcv-all', ticker], queryFn: () => api<Ohlcv>(`/api/market/ohlcv/${encodeURIComponent(ticker)}?limit=2600`), staleTime: 600_000 })
  const loadBars = useMutation({
    mutationFn: () => api<{ status: string; error?: string }>(`/api/market/ohlcv/${encodeURIComponent(ticker)}/refresh`, { method: 'POST' }),
    onSuccess: (r) => {
      if (r.status === 'error') toast(`Could not load prices: ${r.error}`, 'err')
      qc.invalidateQueries({ queryKey: ['ohlcv-all', ticker] })
    },
  })
  const company = !isIndexLike(kind)
  const ttm = useQuery({ queryKey: ['stm', ticker, 'TTM'], queryFn: () => api<Stm>(`/api/statements/${encodeURIComponent(ticker)}?period=TTM`), enabled: company, retry: false })
  const fy = useQuery({ queryKey: ['stm', ticker, 'FY'], queryFn: () => api<Stm>(`/api/statements/${encodeURIComponent(ticker)}?period=FY`), enabled: company, retry: false })
  const qtr = useQuery({ queryKey: ['stm', ticker, 'Q'], queryFn: () => api<Stm>(`/api/statements/${encodeURIComponent(ticker)}?period=Q`), enabled: company, retry: false })
  const val = useQuery({ queryKey: ['valuation', ticker], queryFn: () => api<Val>(`/api/valuation/${encodeURIComponent(ticker)}`), enabled: company, retry: false })
  const newsTickers = otherListing ? `${ticker},${otherListing}` : ticker
  const news = useQuery({ queryKey: ['co-news', newsTickers], queryFn: () => api<NewsItem[]>(`/api/news/feed?tickers=${encodeURIComponent(newsTickers)}&since=7d&min_importance=20&limit=6`), retry: false })
  const today = new Date().toISOString().slice(0, 10)
  const in120 = new Date(Date.now() + 120 * 86400e3).toISOString().slice(0, 10)
  const events = useQuery({ queryKey: ['co-events', ticker], queryFn: () => api<{ items: CalEvent[] }>(`/api/calendar/events?from=${today}&to=${in120}&kinds=earnings,dividend&q=${encodeURIComponent(ticker)}&min_importance=1`), enabled: company, retry: false })
  const loadStm = useMutation({
    mutationFn: () => api(`/api/statements/${encodeURIComponent(ticker)}/refresh`, { method: 'POST' }),
    onSuccess: () => {
      toast('Financial statements loaded', 'ok')
      qc.invalidateQueries({ queryKey: ['stm', ticker] })
    },
    onError: (e) => toast(`Could not load statements: ${e}`, 'err'),
  })

  const d = bars.data
  const days = RANGES.find((r) => r[0] === range)![2]
  const chartBars = useMemo(() => {
    if (!d?.n) return []
    const cut = Date.now() - days * 86400e3
    const out = []
    for (let i = 0; i < d.n; i++) if (new Date(d.ts[i] + 'Z').getTime() >= cut) out.push({ time: toTime(d.ts[i]), open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] })
    return out
  }, [d, days])
  const now = new Date()
  const perfRows: [string, number | null][] = [
    ['1 week', perf(d, new Date(now.getTime() - 7 * 86400e3))],
    ['1 month', perf(d, new Date(now.getTime() - 30 * 86400e3))],
    ['Year to date', perf(d, new Date(Date.UTC(now.getUTCFullYear() - 1, 11, 31, 23)))],
    ['1 year', perf(d, new Date(now.getTime() - 365 * 86400e3))],
    ['5 years', perf(d, new Date(now.getTime() - 5 * 365 * 86400e3))],
  ]
  const lastBar = d?.n ? d.ts[d.n - 1] : null

  // fundamentals
  const cur = ttm.data?.currency ?? 'USD'
  const rev = last(ttm.data, 'revenue')
  const ni = last(ttm.data, 'net_income')
  const fcf = last(ttm.data, 'fcf')
  const eps = last(ttm.data, 'eps_diluted')
  const sharesQ = last(qtr.data, 'shares_outstanding')
  const sharesFy = last(fy.data, 'shares_outstanding')
  const shares = [sharesQ, sharesFy].filter((x) => x.v).sort((a, b) => (b.end ?? '').localeCompare(a.end ?? ''))[0] ?? last(ttm.data, 'shares_diluted_weighted')
  const px = q?.last ?? (d?.n ? d.close[d.n - 1] : null)
  const mcap = px && shares?.v ? px * shares.v : null
  const pe = px && eps.v && eps.v > 0 ? px / eps.v : null
  const netMargin = rev.v && ni.v !== null ? ni.v / rev.v : null
  const ref = val.data?.reference
  const nextEarn = (events.data?.items ?? []).filter((e) => e.kind === 'earnings').sort((a, b) => a.release_ts.localeCompare(b.release_ts))[0]
  const noStatements = company && (ttm.isError || (ttm.isSuccess && !ttm.data.periods.length))

  return (
    <div className="stack">
      <Card
        title="Price"
        actions={<Segmented value={range} options={RANGES.map(([k]) => k)} onChange={setRange} />}
        foot={lastBar ? `Daily closes, last bar ${dateLabel(lastBar)}. Returns are price changes and exclude dividends.` : undefined}
      >
        {bars.isLoading ? (
          <Loading />
        ) : !d?.n ? (
          <Empty title="No price history stored yet" actions={<button className="primary" onClick={() => loadBars.mutate()} disabled={loadBars.isPending}>{loadBars.isPending ? 'Loading…' : 'Load price history'}</button>}>
            Downloads daily prices (Yahoo Finance) so charts and returns can be shown.
          </Empty>
        ) : (
          <>
            <div className="stats" style={{ marginBottom: 14 }}>
              {perfRows.map(([label, v]) => (
                <Stat key={label} label={label} value={<Change value={v} />} />
              ))}
            </div>
            <CandleChart bars={chartBars} height={300} lineMode />
          </>
        )}
      </Card>

      {company && (
        <Card
          title="Key figures"
          hint={rev.label ? `trailing 12 months to ${dateLabel(rev.end)}` : undefined}
          actions={<a href={href(`company/${ticker}/financials`)}>All financials →</a>}
          foot={!noStatements && rev.v ? `From the company’s filings. Market cap = price × ${num((shares?.v ?? 0) / 1e9, 2)}B shares (${shares?.label ?? ''}). P/E uses diluted earnings per share over the last 12 months.` : undefined}
        >
          {ttm.isLoading ? (
            <Loading />
          ) : noStatements ? (
            <Empty
              title="No financial statements loaded"
              actions={
                ticker.endsWith('.TA') && otherListing ? (
                  <a href={href(`company/${otherListing}/overview`)}>Open {otherListing} →</a>
                ) : ticker.endsWith('.TA') ? (
                  <a href={href(`company/${ticker}/financials`)}>Upload a report (PDF) →</a>
                ) : (
                  <button className="primary" onClick={() => loadStm.mutate()} disabled={loadStm.isPending}>
                    {loadStm.isPending ? 'Loading from SEC…' : 'Load from SEC filings'}
                  </button>
                )
              }
            >
              {ticker.endsWith('.TA') && otherListing
                ? `This company is also listed in the US as ${otherListing}, which files its reports with the SEC — its figures are shown there.`
                : ticker.endsWith('.TA')
                  ? 'Companies listed only in Tel Aviv publish reports as PDFs on Maya; they can be uploaded on the Financials tab and extracted.'
                  : 'Revenue, profit, cash flow and ratios come from the company’s SEC filings.'}
            </Empty>
          ) : (
            <div className="stats">
              <Stat label="Market cap" value={money(mcap, cur)} help="Current price × shares outstanding (latest reported)." />
              <Stat label="Revenue" value={money(rev.v, cur)} sub="last 12 months" />
              <Stat label="Net income" value={money(ni.v, cur)} sub={netMargin !== null ? `${ratioPct(netMargin)} net margin` : undefined} tone={ni.v !== null && ni.v < 0 ? 'down' : ''} />
              <Stat label="Free cash flow" value={money(fcf.v, cur)} help="Operating cash flow minus capital expenditure, last 12 months." sub="last 12 months" />
              <Stat label="P/E ratio" value={pe ? times(pe) : '—'} help="Price divided by diluted earnings per share over the last 12 months." sub={eps.v ? `EPS ${num(eps.v, 2)}` : undefined} />
              <Stat
                label="Fair value (est.)"
                value={ref?.value ? `${cur === 'ILS' ? '₪' : '$'}${price(ref.value)}` : '—'}
                sub={ref?.upside !== null && ref?.upside !== undefined ? <span className={ref.upside >= 0 ? 'up' : 'down'}>{pct(ref.upside * 100, 0)} vs price</span> : <a href={href(`company/${ticker}/valuation`)}>Run valuation →</a>}
                help="Base-case discounted cash flow estimate per share. See the Valuation tab for assumptions."
              />
              <Stat label="Next earnings" value={nextEarn ? dayLabel(nextEarn.release_ts) : '—'} sub={nextEarn ? `${timeIL(nextEarn.release_ts)} Israel time` : 'not scheduled yet'} />
            </div>
          )}
        </Card>
      )}

      <Card title="Recent news" hint="last 7 days" actions={<a href={href(`company/${ticker}/news`)}>All news →</a>}>
        {news.isLoading ? (
          <Loading />
        ) : !(news.data ?? []).length ? (
          <Empty title="No notable stories in the last 7 days">The News tab shows all stories, including minor ones.</Empty>
        ) : (
          <div className="list">
            {(news.data ?? []).map((n) => (
              <div key={n.id} className="list-item">
                <div className="li-time">{dayLabel(n.first_seen).replace(/^\w+ /, '')}</div>
                <div className="li-body">
                  <a className="li-title" href={n.url} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>
                    {n.title}
                  </a>
                  <div className="li-meta">{n.publisher}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
