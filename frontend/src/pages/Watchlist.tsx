import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { navigate } from '../lib/router'
import { useQuoteMap, type QuoteMsg } from '../lib/ws'
import { quoteFreshness, useProfiles, useQuotesREST, useSparklines, useWatchlists } from '../lib/hooks'
import { dayLabel, num, price } from '../lib/format'
import { toast } from '../lib/toast'
import { Card, Change, Empty, Loading, Page } from '../ui'
import Sparkline from '../components/Sparkline'

type Hit = { ticker: string; name: string; kind: string }
type CalEvent = { id: number; kind: string; title: string; release_ts: string; affected_tickers?: string[]; event_key: string }

function AddTicker({ onAdd, busy }: { onAdd: (t: string) => void; busy: boolean }) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [dq, setDq] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDq(q.trim()), 180)
    return () => clearTimeout(t)
  }, [q])
  const hits = useQuery({ queryKey: ['search', dq], queryFn: () => api<Hit[]>(`/api/market/search?q=${encodeURIComponent(dq)}&limit=6`), enabled: dq.length >= 1 })
  const submit = (t: string) => {
    if (!t) return
    onAdd(t.toUpperCase())
    setQ('')
    setOpen(false)
  }
  return (
    <div style={{ position: 'relative' }}>
      <input
        placeholder="Add a ticker or company name…"
        value={q}
        style={{ width: 300 }}
        onChange={(e) => {
          setQ(e.target.value)
          setOpen(true)
        }}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return
          const typed = q.trim().toUpperCase()
          const exact = (hits.data ?? []).find((h) => h.ticker === typed)
          // a ticker-shaped entry is taken literally (e.g. a Tel Aviv symbol not in the name list); a name picks the best match
          const looksLikeTicker = /^[\^]?[A-Z0-9.\-=]{1,12}$/.test(q.trim())
          submit(exact?.ticker ?? (looksLikeTicker ? typed : (hits.data?.[0]?.ticker ?? '')))
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        disabled={busy}
      />
      {open && (hits.data ?? []).length > 0 && (
        <div className="card" style={{ position: 'absolute', top: 38, left: 0, width: 420, zIndex: 20, padding: 6, boxShadow: '0 12px 40px rgba(0,0,0,.5)' }}>
          {(hits.data ?? []).map((h) => (
            <div key={h.ticker} className="nav-recent" onMouseDown={() => submit(h.ticker)}>
              <span className="ticker">{h.ticker}</span>
              <span>{h.name}</span>
              <span className="faint xs" style={{ marginLeft: 'auto' }}>
                {h.kind}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Watchlist() {
  const qc = useQueryClient()
  const lists = useWatchlists()
  const [wid, setWid] = useState<number | null>(null)
  const current = lists.data?.find((w) => w.id === (wid ?? lists.data?.[0]?.id))
  const tickers = useMemo(() => (current?.items ?? []).map((i) => i.ticker), [current])
  const live = useQuoteMap()
  const rest = useQuotesREST(tickers)
  const quotes: Record<string, QuoteMsg> = useMemo(() => {
    const m: Record<string, QuoteMsg> = {}
    for (const q of rest.data ?? []) m[q.ticker] = q
    for (const [k, q] of Object.entries(live)) if (!m[k] || q.ts >= m[k].ts) m[k] = q
    return m
  }, [rest.data, live])
  const names = useProfiles(tickers)
  const spark = useSparklines(tickers, 30)
  const today = new Date().toISOString().slice(0, 10)
  const in90 = new Date(Date.now() + 90 * 86400e3).toISOString().slice(0, 10)
  const earnings = useQuery({ queryKey: ['wl-earnings', today], queryFn: () => api<{ items: CalEvent[] }>(`/api/calendar/events?from=${today}&to=${in90}&kinds=earnings&min_importance=1`), staleTime: 3600_000 })
  const nextEarnings = useMemo(() => {
    const m: Record<string, string> = {}
    for (const e of earnings.data?.items ?? []) {
      const t = e.event_key.replace('earnings.', '').toUpperCase()
      if (!m[t] || e.release_ts < m[t]) m[t] = e.release_ts
    }
    return m
  }, [earnings.data])

  const add = useMutation({
    mutationFn: (t: string) => api(`/api/watchlists/${current!.id}/items`, { method: 'POST', json: { ticker: t } }),
    onSuccess: (_d, t) => {
      qc.invalidateQueries({ queryKey: ['watchlists'] })
      toast(`${t} added`, 'ok')
    },
    onError: (e) => toast(String(e).includes('409') ? 'Already in this list' : String(e), 'err'),
  })
  const remove = useMutation({
    mutationFn: (t: string) => api(`/api/watchlists/${current!.id}/items/${encodeURIComponent(t)}`, { method: 'DELETE' }),
    onSuccess: (_d, t) => {
      qc.invalidateQueries({ queryKey: ['watchlists'] })
      toast(`${t} removed`, 'ok')
    },
  })
  const newList = useMutation({
    mutationFn: (name: string) => api<{ id: number }>('/api/watchlists', { method: 'POST', json: { name } }),
    onSuccess: (w) => {
      qc.invalidateQueries({ queryKey: ['watchlists'] })
      setWid(w.id)
    },
  })

  const liveCount = tickers.filter((t) => quotes[t] && !quotes[t].source.startsWith('yf')).length
  const delayedCount = tickers.filter((t) => quotes[t]?.source.startsWith('yf')).length
  const latestLive = tickers.map((t) => quotes[t]).filter((q) => q && !q.source.startsWith('yf')).map((q) => q!.ts).sort().at(-1)

  return (
    <Page
      title="Watchlist"
      sub="Prices update live while the market is open. Click a row to open the company."
      actions={
        <>
          {(lists.data ?? []).length > 1 && (
            <select value={current?.id ?? ''} onChange={(e) => setWid(Number(e.target.value))}>
              {(lists.data ?? []).map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          )}
          <button
            className="ghost"
            onClick={() => {
              const n = prompt('Name of the new list')
              if (n?.trim()) newList.mutate(n.trim())
            }}
          >
            New list
          </button>
          {current && <AddTicker onAdd={(t) => add.mutate(t)} busy={add.isPending} />}
        </>
      }
    >
      <Card
        flush
        foot={
          tickers.length
            ? [
                liveCount ? `${liveCount} real-time (Finnhub)${latestLive ? ` · latest: ${quoteFreshness(quotes[tickers.find((t) => quotes[t]?.ts === latestLive)!]).text.replace(/^Live · /, '').replace(/^L/, 'l').replace(/^P/, 'p').replace(/^A/, 'a')}` : ''}` : '',
                delayedCount ? `${delayedCount} delayed (Yahoo Finance)` : '',
              ]
                .filter(Boolean)
                .join(' · ')
            : undefined
        }
      >
        {lists.isLoading ? (
          <div style={{ padding: 18 }}>
            <Loading />
          </div>
        ) : !tickers.length ? (
          <div style={{ padding: 18 }}>
            <Empty title="This list is empty">Use “Add a ticker” above — for example AAPL, TEVA.TA (Tel Aviv) or ^GSPC (S&amp;P 500 index).</Empty>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Company</th>
                <th className="r">Price</th>
                <th className="r">Day change</th>
                <th>Last 30 days</th>
                <th>Next earnings</th>
                <th>Price status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {tickers.map((t) => {
                const q = quotes[t]
                const abs = q && q.prev_close ? q.last - q.prev_close : null
                const f = quoteFreshness(q)
                return (
                  <tr key={t} className="click" onClick={() => navigate(`company/${t}`)}>
                    <td>
                      <div className="strong">{names.data?.[t]?.name ?? t}</div>
                      <div className="xs faint ticker">{t}</div>
                    </td>
                    <td className="r num strong">{price(q?.last)}</td>
                    <td className="r">
                      <Change value={q?.change_pct} />
                      <div className="xs faint num">{abs === null ? '' : `${abs > 0 ? '+' : ''}${num(abs, 2)}`}</div>
                    </td>
                    <td>{spark.data?.[t] ? <Sparkline values={spark.data[t]} width={96} height={22} /> : <span className="faint xs">no history loaded</span>}</td>
                    <td className="muted small">{nextEarnings[t] ? dayLabel(nextEarnings[t]) : '—'}</td>
                    <td className="small" title={f.text}>
                      <span className={f.tone === 'live' && f.text.startsWith('Live') ? 'up' : 'muted'}>{f.tone === 'live' ? f.text.split(' ·')[0].replace(/ trade .*$/, '') : f.tone === 'delayed' ? 'Delayed (Yahoo)' : f.tone === 'none' ? 'No price yet' : f.text}</span>
                    </td>
                    <td className="r" onClick={(e) => e.stopPropagation()}>
                      <button className="ghost icon" title={`Remove ${t} from this list`} onClick={() => remove.mutate(t)}>
                        ✕
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Card>
    </Page>
  )
}
