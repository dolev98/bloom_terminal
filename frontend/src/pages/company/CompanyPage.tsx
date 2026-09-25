import { useEffect, useMemo } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { href, navigate } from '../../lib/router'
import { pushRecentTicker } from '../../lib/store'
import { useQuote } from '../../lib/ws'
import { isIndexLike, quoteFreshness, useProfile, useQuotesREST, useWatchlists } from '../../lib/hooks'
import { num, price } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Change } from '../../ui'
import Overview from './Overview'
import Chart from './Chart'
import Financials from './Financials'
import Valuation from './Valuation'
import News from './News'
import Notes from './Notes'

const KIND_LABEL: Record<string, string> = { stock: 'Stock', etf: 'Exchange-traded fund', index: 'Index', fx: 'Currency', commodity: 'Commodity futures', crypto: 'Crypto' }

export default function CompanyPage({ ticker, tab }: { ticker: string; tab: string }) {
  const qc = useQueryClient()
  useEffect(() => pushRecentTicker(ticker), [ticker])
  useEffect(() => {
    document.getElementById('main')?.scrollTo({ top: 0 })
  }, [ticker, tab])
  const profile = useProfile(ticker)
  const liveQ = useQuote(ticker)
  const rest = useQuotesREST([ticker])
  const q = useMemo(() => {
    const r = rest.data?.[0]
    if (liveQ && (!r || liveQ.ts >= r.ts)) return { ...r, ...liveQ, prev_close: liveQ.prev_close ?? r?.prev_close }
    return r
  }, [liveQ, rest.data])
  const lists = useWatchlists()
  const main = lists.data?.[0]
  const inList = !!main?.items.some((i) => i.ticker === ticker)
  const add = useMutation({
    mutationFn: () => api(`/api/watchlists/${main!.id}/items`, { method: 'POST', json: { ticker } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watchlists'] })
      toast(`${ticker} added to ${main?.name ?? 'watchlist'}`, 'ok')
    },
  })

  const kind = profile.data?.kind ?? 'stock'
  const fundamentals = !isIndexLike(kind)
  const tabs: [string, string][] = [['overview', 'Overview'], ['chart', 'Chart'], ...(fundamentals ? ([['financials', 'Financials'], ['valuation', 'Valuation']] as [string, string][]) : []), ['news', 'News'], ['notes', 'Notes']]
  const active = tabs.some(([k]) => k === tab) ? tab : 'overview'
  const abs = q && q.prev_close ? q.last - q.prev_close : null
  const fresh = quoteFreshness(q)
  const cur = profile.data?.currency === 'ILS' ? '₪' : profile.data?.currency === 'USD' && kind !== 'fx' && kind !== 'index' ? '$' : ''

  const body =
    active === 'chart' ? (
      <Chart ticker={ticker} />
    ) : active === 'financials' ? (
      <Financials ticker={ticker} />
    ) : active === 'valuation' ? (
      <Valuation ticker={ticker} />
    ) : active === 'news' ? (
      <News ticker={ticker} otherListing={profile.data?.other_listing} />
    ) : active === 'notes' ? (
      <Notes ticker={ticker} />
    ) : (
      <Overview ticker={ticker} kind={kind} otherListing={profile.data?.other_listing} />
    )

  return (
    <div className="page">
      <div className="co-head">
        <div style={{ minWidth: 0 }}>
          <div className="name">{profile.data?.name ?? (profile.isLoading ? ' ' : ticker)}</div>
          <div className="sym">
            <span className="ticker">{ticker}</span> · {KIND_LABEL[kind] ?? kind}
            {ticker.endsWith('.TA') ? ' · Tel Aviv Stock Exchange' : ''}
            {profile.data?.other_listing && (
              <>
                {' · '}
                <a href={href(`company/${profile.data.other_listing}/${active}`)} title="The same company's shares on the other exchange">
                  {profile.data.other_listing.endsWith('.TA') ? 'Also listed in Tel Aviv' : 'Also listed in the US'}: {profile.data.other_listing}
                </a>
              </>
            )}
          </div>
        </div>
        <div>
          <div className="row nowrap" style={{ gap: 12, alignItems: 'baseline' }}>
            <span className="px">
              {q ? `${cur}${price(q.last)}` : '—'}
            </span>
            {q && (
              <span className="chg">
                <Change value={q.change_pct} />
                {abs !== null && <span className="num muted"> ({abs > 0 ? '+' : ''}{num(abs, 2)})</span>}
              </span>
            )}
          </div>
          <div className="asof" title={q?.source ? `Source: ${q.source.startsWith('yf') ? 'Yahoo Finance' : 'Finnhub'}` : ''}>
            {fresh.text}
            {q?.change_pct !== undefined && q?.change_pct !== null ? ' · change vs previous close' : ''}
          </div>
        </div>
        <div className="spacer" />
        <div className="row">
          {main && (inList ? <span className="badge ok">✓ In your watchlist</span> : <button onClick={() => add.mutate()} disabled={add.isPending}>+ Add to watchlist</button>)}
          <button className="ghost" onClick={() => navigate(`company/${ticker}/notes`)}>
            ✎ Note
          </button>
        </div>
      </div>
      <div className="tabs" style={{ marginTop: 14 }}>
        {tabs.map(([k, label]) => (
          <a key={k} className={active === k ? 'on' : ''} href={href(`company/${ticker}/${k}`)}>
            {label}
          </a>
        ))}
      </div>
      {body}
    </div>
  )
}
