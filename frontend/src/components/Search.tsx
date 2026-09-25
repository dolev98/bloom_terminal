import { useEffect, useState } from 'react'
import { Command } from 'cmdk'
import { useQuery } from '@tanstack/react-query'
import { api, type SeriesSpec } from '../lib/api'
import { navigate } from '../lib/router'
import { useUI } from '../lib/store'

type Hit = { ticker: string; name: string; kind: string }
const PAGES: [string, string, string][] = [
  ['Dashboard', '', 'Market overview, today’s events, watchlist'],
  ['Watchlist', 'watchlist', 'Your tickers with live prices'],
  ['Calendar', 'calendar', 'Economic releases, central banks, earnings'],
  ['Macro — United States', 'macro/US', 'Growth, inflation, jobs, rates'],
  ['Macro — Israel', 'macro/IL', 'CPI, Bank of Israel rate, shekel'],
  ['News', 'news', 'Filings and news for your watchlist'],
  ['Correlations', 'correlation', 'Compare any two series'],
  ['Notes', 'notes', 'Your research notes'],
  ['Alerts', 'alerts', 'Price and valuation alerts'],
  ['Data catalog', 'data', 'All data series; add new ones'],
  ['Status', 'status', 'Data sources, scheduled jobs, backups'],
  ['Settings', 'settings', 'API keys, Telegram, preferences'],
]

function useDebounced(v: string, ms = 180) {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms)
    return () => clearTimeout(t)
  }, [v, ms])
  return d
}

export default function Search() {
  const open = useUI((s) => s.searchOpen)
  const setOpen = useUI((s) => s.setSearchOpen)
  const [q, setQ] = useState('')
  const dq = useDebounced(q.trim())
  const tickers = useQuery({ queryKey: ['search', dq], queryFn: () => api<Hit[]>(`/api/market/search?q=${encodeURIComponent(dq)}&limit=8`), enabled: open && dq.length >= 1 })
  const catalog = useQuery({ queryKey: ['catalog'], queryFn: () => api<{ items: SeriesSpec[] }>('/api/catalog'), enabled: open, staleTime: 60_000 })

  useEffect(() => {
    if (open) setQ('')
  }, [open])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  if (!open) return null
  const go = (path: string, query?: Record<string, string>) => {
    navigate(path, query)
    setOpen(false)
  }
  const ql = dq.toLowerCase()
  const series = ql.length >= 2 ? (catalog.data?.items ?? []).filter((s) => s.series_id.toLowerCase().includes(ql) || s.name.toLowerCase().includes(ql)).slice(0, 6) : []
  const pages = PAGES.filter(([label, , desc]) => !ql || label.toLowerCase().includes(ql) || desc.toLowerCase().includes(ql)).slice(0, ql ? 4 : 12)
  const typed = q.trim().toUpperCase()
  const exact = typed && /^[\^]?[A-Z0-9.\-=]{1,12}$/.test(typed) && !(tickers.data ?? []).some((h) => h.ticker === typed)

  return (
    <div className="overlay" onMouseDown={() => setOpen(false)}>
      <div className="palette" onMouseDown={(e) => e.stopPropagation()}>
        <Command label="Search" shouldFilter={false}>
          <Command.Input autoFocus value={q} onValueChange={setQ} placeholder="Company name, ticker (AAPL, TEVA.TA, ^GSPC), data series or page…" />
          <Command.List>
            {ql && !tickers.isFetching && !(tickers.data ?? []).length && !series.length && !pages.length && !exact && <Command.Empty>No matches for “{q}”.</Command.Empty>}
            {((tickers.data ?? []).length > 0 || exact) && (
              <Command.Group heading="Companies & markets">
                {(tickers.data ?? []).map((h) => (
                  <Command.Item key={h.ticker} value={`t:${h.ticker}`} onSelect={() => go(`company/${h.ticker}`)}>
                    <span className="ticker">{h.ticker}</span>
                    <span>{h.name}</span>
                    <span className="sub">{h.kind}</span>
                  </Command.Item>
                ))}
                {exact && (
                  <Command.Item value={`t:${typed}`} onSelect={() => go(`company/${typed}`)}>
                    <span className="ticker">{typed}</span>
                    <span className="muted">Open {typed}</span>
                  </Command.Item>
                )}
              </Command.Group>
            )}
            {series.length > 0 && (
              <Command.Group heading="Data series">
                {series.map((s) => (
                  <Command.Item key={s.series_id} value={`s:${s.series_id}`} onSelect={() => go(`series/${s.series_id}`)}>
                    <span>{s.name}</span>
                    <span className="sub mono">{s.series_id}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
            {pages.length > 0 && (
              <Command.Group heading="Pages">
                {pages.map(([label, path, desc]) => (
                  <Command.Item key={label} value={`p:${label}`} onSelect={() => go(path)}>
                    <span>{label}</span>
                    <span className="sub">{desc}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>
          <div className="palette-foot">
            <span>
              <kbd>↑</kbd> <kbd>↓</kbd> move
            </span>
            <span>
              <kbd>↵</kbd> open
            </span>
            <span>
              <kbd>esc</kbd> close
            </span>
          </div>
        </Command>
      </div>
    </div>
  )
}
