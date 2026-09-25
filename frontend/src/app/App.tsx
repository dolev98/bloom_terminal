import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { href, useRoute } from '../lib/router'
import { recentTickers, useUI } from '../lib/store'
import { startQuoteSocket, useQuotes } from '../lib/ws'
import { localDate, taseMarketOpen, usMarketOpen } from '../lib/market'
import Search from '../components/Search'
import Toasts from '../components/Toasts'
import { Outlet } from './routes'

type NavItem = { path: string; label: string; ico: string; match: (p: string[]) => boolean }
const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: 'Overview',
    items: [
      { path: '', label: 'Dashboard', ico: '◧', match: (p) => p.length === 0 },
      { path: 'watchlist', label: 'Watchlist', ico: '☆', match: (p) => p[0] === 'watchlist' },
    ],
  },
  {
    section: 'Markets',
    items: [
      { path: 'calendar', label: 'Calendar', ico: '▦', match: (p) => p[0] === 'calendar' },
      { path: 'macro/US', label: 'Macro by country', ico: '◍', match: (p) => p[0] === 'macro' },
      { path: 'news', label: 'News', ico: '≡', match: (p) => p[0] === 'news' },
      { path: 'correlation', label: 'Correlations', ico: '⤧', match: (p) => p[0] === 'correlation' },
    ],
  },
  {
    section: 'Research',
    items: [
      { path: 'notes', label: 'Notes', ico: '✎', match: (p) => p[0] === 'notes' },
      { path: 'alerts', label: 'Alerts', ico: '⚑', match: (p) => p[0] === 'alerts' },
      { path: 'data', label: 'Data catalog', ico: '⌸', match: (p) => p[0] === 'data' || p[0] === 'series' },
    ],
  },
  {
    section: 'System',
    items: [
      { path: 'status', label: 'Status', ico: '◉', match: (p) => p[0] === 'status' },
      { path: 'settings', label: 'Settings', ico: '⚙', match: (p) => p[0] === 'settings' },
    ],
  },
]

type Holiday = { country: string; title: string; reference_period: string }

function useExchangeHolidays() {
  const today = localDate('Asia/Jerusalem')
  return useQuery({
    queryKey: ['holidays-today', today],
    queryFn: async () => {
      const r = await api<{ items: Holiday[] }>(`/api/calendar/events?from=${today}&to=${today}&kinds=holiday&countries=US,IL`)
      return r.items
    },
    staleTime: 3600_000,
    retry: false,
  })
}

function MarketStatus() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15_000)
    return () => clearInterval(t)
  }, [])
  const hol = useExchangeHolidays().data ?? []
  const usDate = localDate('America/New_York', now)
  const ilDate = localDate('Asia/Jerusalem', now)
  const usHoliday = hol.find((h) => h.country === 'US' && h.reference_period === usDate && /closed/i.test(h.title))
  const ilHoliday = hol.find((h) => h.country === 'IL' && h.reference_period === ilDate && /closed/i.test(h.title))
  const us = usMarketOpen(now) && !usHoliday
  const tase = taseMarketOpen(now) && !ilHoliday
  const time = now.toLocaleTimeString('en-GB', { timeZone: 'Asia/Jerusalem', hour: '2-digit', minute: '2-digit' })
  const et = now.toLocaleTimeString('en-GB', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit' })
  return (
    <>
      <span className="mkt" title={usHoliday ? usHoliday.title : 'NYSE / Nasdaq regular session 09:30–16:00 New York time'}>
        <span className={`d ${us ? 'open' : ''}`} />
        US {us ? 'open' : usHoliday ? 'holiday' : 'closed'}
      </span>
      <span className="mkt" title={ilHoliday ? ilHoliday.title : 'Tel Aviv Stock Exchange, Mon–Fri'}>
        <span className={`d ${tase ? 'open' : ''}`} />
        Tel Aviv {tase ? 'open' : ilHoliday ? 'holiday' : 'closed'}
      </span>
      <span className="num" title={`New York ${et}`}>
        {time} <span className="faint">Israel</span>
      </span>
    </>
  )
}

function SystemDot() {
  const q = useQuery({ queryKey: ['providers-health'], queryFn: () => api<{ id: string; usable: boolean; ok?: boolean; reason: string }[]>('/api/health/providers'), refetchInterval: 300_000, retry: false })
  const failing = (q.data ?? []).filter((p) => p.usable && p.ok === false)
  return failing.length ? <span className="dot" title={`${failing.length} data source(s) failing`} /> : null
}

function Recent() {
  const [items, setItems] = useState(recentTickers())
  useEffect(() => {
    const on = () => setItems(recentTickers())
    window.addEventListener('recent-tickers', on)
    return () => window.removeEventListener('recent-tickers', on)
  }, [])
  if (!items.length) return null
  return (
    <div className="nav-section">
      <div className="label">Recently viewed</div>
      {items.map((t) => (
        <a key={t} className="nav-recent" href={href(`company/${t}`)}>
          <span className="ticker">{t}</span>
        </a>
      ))}
    </div>
  )
}

export default function App() {
  const route = useRoute()
  const setSearchOpen = useUI((s) => s.setSearchOpen)
  const connected = useQuotes((s) => s.connected)

  useEffect(() => {
    startQuoteSocket()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      const typing = t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement || t?.isContentEditable
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setSearchOpen(true)
      } else if (e.key === '/' && !typing) {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setSearchOpen])

  return (
    <div className="shell">
      <nav className="sidebar">
        <a className="brand" href="#/" style={{ color: 'var(--text)', textDecoration: 'none' }}>
          <span className="logo">T</span> Terminal
        </a>
        {NAV.map((sec) => (
          <div key={sec.section} className="nav-section">
            <div className="label">{sec.section}</div>
            {sec.items.map((it) => (
              <a key={it.label} className={`nav-item ${it.match(route.path) ? 'on' : ''}`} href={href(it.path)}>
                <span className="ico">{it.ico}</span>
                {it.label}
                {it.path === 'status' && <SystemDot />}
              </a>
            ))}
          </div>
        ))}
        <Recent />
      </nav>
      <header className="header">
        <div className="searchbox" onClick={() => setSearchOpen(true)} role="button">
          <span>⌕</span>
          <span className="grow">Search a company, ticker or data series…</span>
          <kbd>⌘K</kbd>
        </div>
        <div className="status">
          {!connected && <span className="badge warn" title="The live price connection to the backend is down; prices shown may be old.">Live prices disconnected</span>}
          <MarketStatus />
        </div>
      </header>
      <main className="main" id="main">
        <Outlet route={route} />
      </main>
      <Search />
      <Toasts />
    </div>
  )
}
