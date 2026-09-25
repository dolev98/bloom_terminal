import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { plainTitle } from '../components/calendar/model'
import { href, navigate } from '../lib/router'
import { useQuoteMap, type QuoteMsg } from '../lib/ws'
import { quoteFreshness, useProfiles, useQuotesREST, useSparklines, useWatchlists } from '../lib/hooks'
import { dayLabel, eventValue, num, pct, price, timeIL } from '../lib/format'
import { localDate } from '../lib/market'
import { Card, Change, Empty, Flag, Importance, Loading, Page } from '../ui'
import Sparkline from '../components/Sparkline'

type Tile = { t: string; name: string }
const MARKETS: { title: string; tiles: Tile[] }[] = [
  {
    title: 'United States',
    tiles: [
      { t: 'SPY', name: 'S&P 500' },
      { t: 'QQQ', name: 'Nasdaq-100' },
      { t: 'IWM', name: 'Russell 2000 (small caps)' },
      { t: 'DIA', name: 'Dow Jones' },
      { t: '^VIX', name: 'Volatility index (VIX)' },
    ],
  },
  {
    title: 'Israel',
    tiles: [
      { t: 'TA35.TA', name: 'TA-35 index' },
      { t: '^TA125.TA', name: 'TA-125 index' },
      { t: 'ILS=X', name: 'US dollar in shekels' },
    ],
  },
  {
    title: 'Currencies, commodities & crypto',
    tiles: [
      { t: 'DX-Y.NYB', name: 'US dollar index' },
      { t: 'EURUSD=X', name: 'Euro in dollars' },
      { t: 'GC=F', name: 'Gold ($/oz)' },
      { t: 'CL=F', name: 'Crude oil, WTI ($/bbl)' },
      { t: 'BTC-USD', name: 'Bitcoin ($)' },
      { t: 'EEM', name: 'Emerging-market stocks' },
    ],
  },
]
const TILE_TICKERS = MARKETS.flatMap((g) => g.tiles.map((x) => x.t))
const ETF_NOTE: Record<string, string> = { SPY: 'via SPY ETF', QQQ: 'via QQQ ETF', IWM: 'via IWM ETF', DIA: 'via DIA ETF', EEM: 'via EEM ETF' }

type RateRow = { id: string; name: string; unit: 'pct' | 'bp' | 'ils' | 'bn'; changeAs: 'bp' | 'pct' | 'abs' }
const RATES: RateRow[] = [
  { id: 'fred:DFF', name: 'Fed funds rate', unit: 'pct', changeAs: 'bp' },
  { id: 'boi:BR/MNT_RIB_BOI_D', name: 'Bank of Israel rate', unit: 'pct', changeAs: 'bp' },
  { id: 'fred:DGS2', name: 'US 2-year Treasury yield', unit: 'pct', changeAs: 'bp' },
  { id: 'fred:DGS10', name: 'US 10-year Treasury yield', unit: 'pct', changeAs: 'bp' },
  { id: 'derived:US2S10S_BP', name: 'US yield curve (10y minus 2y)', unit: 'bp', changeAs: 'bp' },
  { id: 'fred:T10YIE', name: 'US 10-year inflation expectation', unit: 'pct', changeAs: 'bp' },
  { id: 'fred:BAMLH0A0HYM2', name: 'US high-yield credit spread', unit: 'pct', changeAs: 'bp' },
  { id: 'boi:EXR/RER_USD_ILS', name: 'USD/ILS official rate (Bank of Israel)', unit: 'ils', changeAs: 'pct' },
]

type Obs = { n: number; ts: string[]; value: number[] }
type CalEvent = { id: number; kind: string; country: string; title: string; importance: number; release_ts: string; status: string; values?: { consensus?: number | null; actual?: number | null; previous?: number | null; unit?: string | null } | null }
type NewsItem = { id: number; title: string; url: string; publisher?: string | null; first_seen: string; importance: number; kind: string; tickers: { ticker: string }[] }
type Brief = { upside: { ticker: string; fair_value: number | null; price: number | null; upside: number | null; model: string }[] }

function useRates() {
  return useQuery({
    queryKey: ['dash-rates'],
    queryFn: async () => {
      const out: Record<string, { last: number; prev: number | null; ts: string } | null> = {}
      await Promise.all(
        RATES.map(async (r) => {
          try {
            const o = await api<Obs>(`/api/series/${encodeURIComponent(r.id)}/observations?limit=2`)
            out[r.id] = o.n ? { last: o.value[o.n - 1], prev: o.n > 1 ? o.value[o.n - 2] : null, ts: o.ts[o.n - 1] } : null
          } catch {
            out[r.id] = null
          }
        }),
      )
      return out
    },
    staleTime: 300_000,
  })
}

function rateValue(r: RateRow, v: number) {
  if (r.unit === 'pct') return `${num(v, 2)}%`
  if (r.unit === 'bp') return `${num(v, 0)} bp`
  if (r.unit === 'ils') return `₪${num(v, 3)}`
  return num(v, 0)
}
function rateChange(r: RateRow, last: number, prev: number | null) {
  if (prev === null) return <span className="faint">—</span>
  if (r.changeAs === 'bp') {
    const bp = r.unit === 'bp' ? last - prev : (last - prev) * 100
    const cls = bp > 0 ? 'up' : bp < 0 ? 'down' : 'muted'
    return <span className={`num ${cls}`}>{bp > 0 ? '+' : ''}{num(bp, 0)} bp</span>
  }
  return <Change value={prev ? (last / prev - 1) * 100 : null} />
}

function MarketTile({ tile, q, spark }: { tile: Tile; q?: QuoteMsg; spark?: number[] }) {
  const chg = q?.change_pct ?? null
  return (
    <div className="tile" onClick={() => navigate(`company/${tile.t}`)} title={`Open ${tile.name}`}>
      <div className="row between nowrap">
        <span className="name">{tile.name}</span>
        {spark && <Sparkline values={spark} width={60} height={18} />}
      </div>
      <div className="row between nowrap" style={{ marginTop: 4 }}>
        <span className="price">{price(q?.last)}</span>
        <span className="chg">
          <Change value={chg} />
        </span>
      </div>
      <div className="meta">
        <span className="ticker">{tile.t}</span> {ETF_NOTE[tile.t] ?? ''}
      </div>
    </div>
  )
}

function freshnessFoot(quotes: (QuoteMsg | undefined)[]) {
  const qs = quotes.filter(Boolean) as QuoteMsg[]
  if (!qs.length) return 'No prices loaded yet.'
  const live = qs.filter((q) => !q.source.startsWith('yf'))
  const delayed = qs.length - live.length
  const parts: string[] = []
  if (live.length) {
    const latest = live.map((q) => q.ts).sort().at(-1)!
    parts.push(`Real-time prices (Finnhub): ${quoteFreshness(live.find((q) => q.ts === latest)).text.replace(/^Live · /, '').replace(/^Last/, 'last')}`)
  }
  if (delayed) parts.push(`${delayed === qs.length ? 'Prices are' : delayed === 1 ? '1 price is' : `${delayed} prices are`} delayed (Yahoo Finance, about 15–20 min during trading)`)
  return parts.join(' · ')
}

export default function Dashboard() {
  const live = useQuoteMap()
  const wl = useWatchlists()
  const watch = useMemo(() => (wl.data?.[0]?.items ?? []).map((i) => i.ticker), [wl.data])
  const all = useMemo(() => Array.from(new Set([...TILE_TICKERS, ...watch])), [watch])
  const rest = useQuotesREST(all)
  const quotes: Record<string, QuoteMsg> = useMemo(() => {
    const m: Record<string, QuoteMsg> = {}
    for (const q of rest.data ?? []) m[q.ticker] = q
    for (const [k, q] of Object.entries(live)) if (!m[k] || q.ts >= m[k].ts) m[k] = q
    return m
  }, [rest.data, live])
  const spark = useSparklines(all, 30)
  const names = useProfiles(watch)
  const rates = useRates()
  const today = localDate('Asia/Jerusalem')
  const tomorrow = localDate('Asia/Jerusalem', new Date(Date.now() + 86400e3))
  const events = useQuery({ queryKey: ['dash-events', today], queryFn: () => api<{ items: CalEvent[] }>(`/api/calendar/events?from=${today}&to=${tomorrow}&min_importance=2`), staleTime: 300_000 })
  const news = useQuery({
    queryKey: ['dash-news', watch.join(',')],
    queryFn: () => api<NewsItem[]>(`/api/news/feed?tickers=${encodeURIComponent(watch.join(','))}&since=48h&min_importance=30&limit=6`),
    enabled: watch.length > 0,
    staleTime: 300_000,
  })
  const brief = useQuery({ queryKey: ['dash-brief'], queryFn: () => api<Brief>('/api/brief'), staleTime: 600_000 })
  const health = useQuery({ queryKey: ['providers-health'], queryFn: () => api<{ id: string; usable: boolean; reason: string }[]>('/api/health/providers'), staleTime: 300_000 })
  const requiredMissing = (health.data ?? []).filter((p) => ['fred', 'edgar', 'finnhub'].includes(p.id) && !p.usable)

  const movers = watch
    .map((t) => ({ t, q: quotes[t] }))
    .filter((x) => x.q?.change_pct !== undefined && x.q?.change_pct !== null)
    .sort((a, b) => Math.abs(b.q!.change_pct!) - Math.abs(a.q!.change_pct!))
    .slice(0, 8)
  const upcoming = (events.data?.items ?? []).filter((e) => e.kind !== 'holiday').slice(0, 9)
  const holidays = (events.data?.items ?? []).filter((e) => e.kind === 'holiday')
  const latestRateTs = Object.values(rates.data ?? {}).map((r) => r?.ts).filter(Boolean).sort().at(-1)

  return (
    <Page title="Dashboard" sub="Markets at a glance, today’s key events and your watchlist.">
      {requiredMissing.length > 0 && (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          <span>⚠</span>
          <div className="grow">
            Some data can’t load until you add a key: {requiredMissing.map((p) => ({ fred: 'FRED (US economic data)', edgar: 'SEC contact (company filings)', finnhub: 'Finnhub (live US prices)' })[p.id as 'fred']).join(', ')}.
          </div>
          <a href={href('settings')}>Open settings</a>
        </div>
      )}

      <div className="stack">
        {MARKETS.map((g) => (
          <Card key={g.title} title={g.title} foot={freshnessFoot(g.tiles.map((x) => quotes[x.t]))}>
            {rest.isLoading && !Object.keys(quotes).length ? (
              <Loading />
            ) : (
              <div className="tiles">
                {g.tiles.map((tile) => (
                  <MarketTile key={tile.t} tile={tile} q={quotes[tile.t]} spark={spark.data?.[tile.t]} />
                ))}
              </div>
            )}
          </Card>
        ))}

        <div className="grid-2">
          <Card title="Key events today & tomorrow" hint="Israel time" actions={<a href={href('calendar')}>Full calendar →</a>} foot={holidays.length ? `Market holidays: ${holidays.map((h) => h.title).join('; ')}` : undefined}>
            {events.isLoading ? (
              <Loading />
            ) : upcoming.length === 0 ? (
              <Empty title="No medium- or high-importance events">Nothing scheduled for today or tomorrow.</Empty>
            ) : (
              <div className="list">
                {upcoming.map((e) => {
                  const v = e.values ?? {}
                  return (
                    <div key={e.id} className="list-item click" onClick={() => navigate('calendar')}>
                      <div className="li-time">
                        <div className="xs faint">{localDate('Asia/Jerusalem', new Date(e.release_ts + 'Z')) === today ? 'Today' : 'Tomorrow'}</div>
                        {timeIL(e.release_ts)}
                      </div>
                      <div className="li-body">
                        <div className="li-title">
                          <Flag cc={e.country} /> {plainTitle(e.title)}
                        </div>
                        <div className="li-meta">
                          <Importance level={e.importance} />
                          {v.actual !== null && v.actual !== undefined ? (
                            <span>
                              Actual <b className="num">{eventValue(v.actual, v.unit)}</b>
                              {v.consensus !== null && v.consensus !== undefined ? <> · forecast {eventValue(v.consensus, v.unit)}</> : null}
                            </span>
                          ) : new Date(e.release_ts + 'Z').getTime() < Date.now() ? (
                            <span>Released — actual not available from our sources{v.consensus !== null && v.consensus !== undefined ? ` · forecast was ${eventValue(v.consensus, v.unit)}` : ''}</span>
                          ) : v.consensus !== null && v.consensus !== undefined ? (
                            <span>
                              Forecast {eventValue(v.consensus, v.unit)}
                              {v.previous !== null && v.previous !== undefined ? ` · previous ${eventValue(v.previous, v.unit)}` : ''}
                            </span>
                          ) : v.previous !== null && v.previous !== undefined ? (
                            <span>Previous {eventValue(v.previous, v.unit)}</span>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>

          <Card title="Watchlist movers" hint="change vs previous close" actions={<a href={href('watchlist')}>Open watchlist →</a>}>
            {wl.isLoading ? (
              <Loading />
            ) : !watch.length ? (
              <Empty title="Your watchlist is empty" actions={<button onClick={() => navigate('watchlist')}>Add tickers</button>} />
            ) : (
              <table className="table">
                <tbody>
                  {movers.map(({ t, q }) => (
                    <tr key={t} className="click" onClick={() => navigate(`company/${t}`)}>
                      <td>
                        <div className="strong">{names.data?.[t]?.name ?? t}</div>
                        <div className="xs faint ticker">{t}</div>
                      </td>
                      <td className="r num">{price(q?.last)}</td>
                      <td className="r" style={{ width: 90 }}>
                        <Change value={q?.change_pct} />
                      </td>
                      <td style={{ width: 80 }}>{spark.data?.[t] && <Sparkline values={spark.data[t]} width={70} height={18} />}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <div className="grid-2">
          <Card title="Interest rates & currency" actions={<a href={href('macro/US')}>Macro →</a>} foot={latestRateTs ? `Latest observation ${dayLabel(latestRateTs)}. Change = versus the previous observation (daily for most series). Sources: FRED, Bank of Israel.` : undefined}>
            {rates.isLoading ? (
              <Loading />
            ) : (
              <table className="table">
                <tbody>
                  {RATES.map((r) => {
                    const d = rates.data?.[r.id]
                    return (
                      <tr key={r.id} className="click" onClick={() => navigate(`series/${r.id}`)} title={r.id}>
                        <td>{r.name}</td>
                        <td className="r num strong">{d ? rateValue(r, d.last) : '—'}</td>
                        <td className="r" style={{ width: 90 }}>
                          {d ? rateChange(r, d.last, d.prev) : ''}
                        </td>
                        <td className="r xs faint" style={{ width: 90 }}>
                          {d ? dayLabel(d.ts).replace(/^\w+ /, '') : 'no data'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Important news for your watchlist" hint="last 48 hours" actions={<a href={href('news')}>All news →</a>}>
            {news.isLoading ? (
              <Loading />
            ) : !(news.data ?? []).length ? (
              <Empty title="No important stories in the last 48 hours">Less important stories are on the News page.</Empty>
            ) : (
              <div className="list">
                {(news.data ?? []).map((n) => (
                  <div key={n.id} className="list-item">
                    <div className="li-time">{timeIL(n.first_seen)}</div>
                    <div className="li-body">
                      <a className="li-title" href={n.url} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>
                        {n.title}
                      </a>
                      <div className="li-meta">
                        {n.publisher && <span>{n.publisher}</span>}
                        {n.tickers.slice(0, 3).map((x) => (
                          <a key={x.ticker} className="ticker xs" href={href(`company/${x.ticker}`)}>
                            {x.ticker}
                          </a>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <Card title="Valuation: upside to estimated fair value" hint="your watchlist" actions={<a href={href('alerts')}>Alerts →</a>}>
          {brief.isLoading ? (
            <Loading />
          ) : !(brief.data?.upside ?? []).length ? (
            <Empty title="No valuations yet">Open a company and use its Valuation tab to estimate a fair value. Results for your watchlist will appear here.</Empty>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th className="r">Price</th>
                  <th className="r">Fair value</th>
                  <th className="r">Upside</th>
                  <th>Method</th>
                </tr>
              </thead>
              <tbody>
                {(brief.data?.upside ?? []).map((u) => (
                  <tr key={u.ticker} className="click" onClick={() => navigate(`company/${u.ticker}/valuation`)}>
                    <td>
                      <span className="ticker">{u.ticker}</span> <span className="muted">{names.data?.[u.ticker]?.name ?? ''}</span>
                    </td>
                    <td className="r num">{price(u.price)}</td>
                    <td className="r num">{price(u.fair_value)}</td>
                    <td className="r">
                      {/* upside is a ratio from the API (-0.65 = -65%) */}
                      <span className={`num ${(u.upside ?? 0) >= 0 ? 'up' : 'down'}`}>{u.upside === null ? '—' : pct(u.upside * 100, 0)}</span>
                    </td>
                    <td className="muted">{u.model === 'fcff' ? 'Discounted cash flow' : u.model}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </Page>
  )
}
