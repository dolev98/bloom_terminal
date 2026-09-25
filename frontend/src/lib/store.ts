import { create } from 'zustand'
import { navigate } from './router'

export type PanelRequest = { component: string; id?: string; title?: string; params?: Record<string, unknown> }

/** Maps the old "open a panel" requests (used inside page bodies) onto routes. */
export function routeFor(req: PanelRequest): [string, Record<string, string>?] {
  const p = (req.params ?? {}) as Record<string, any>
  const t = (p.ticker ?? (Array.isArray(p.tickers) && p.tickers.length === 1 ? p.tickers[0] : undefined)) as string | undefined
  switch (req.component) {
    case 'home':
      return ['']
    case 'chart':
      return [`company/${t}/chart`]
    case 'security':
    case 'statements':
      return [`company/${t}/financials`]
    case 'valuation':
      return t ? [`company/${t}/valuation`] : ['']
    case 'news':
      return t ? [`company/${t}/news`] : ['news']
    case 'notes':
      return t ? [`company/${t}/notes`] : ['notes']
    case 'seriesChart':
      return [`series/${p.seriesId}`]
    case 'correlation':
      return ['correlation', { a: p.a, b: p.b, pair: p.pairId }]
    case 'macro':
      return [`macro/${p.country ?? 'US'}`]
    case 'calendar':
      return ['calendar', p.country ? { country: p.country } : undefined]
    case 'catalog':
      return ['data']
    case 'sys':
      return ['status']
    default:
      return [req.component]
  }
}

type UIState = {
  searchOpen: boolean
  setSearchOpen: (v: boolean) => void
  /** legacy name kept for older components */
  commandOpen: boolean
  setCommandOpen: (v: boolean) => void
  openPanel: (req: PanelRequest) => void
}

export const useUI = create<UIState>((set) => ({
  searchOpen: false,
  setSearchOpen: (v) => set({ searchOpen: v, commandOpen: v }),
  commandOpen: false,
  setCommandOpen: (v) => set({ searchOpen: v, commandOpen: v }),
  openPanel: (req) => {
    const [path, query] = routeFor(req)
    navigate(path, query)
  },
}))

const RECENT_KEY = 'terminal.recent-tickers.v2'
export function recentTickers(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]')
  } catch {
    return []
  }
}
export function pushRecentTicker(t: string) {
  try {
    const next = [t, ...recentTickers().filter((x) => x !== t)].slice(0, 6)
    localStorage.setItem(RECENT_KEY, JSON.stringify(next))
    window.dispatchEvent(new Event('recent-tickers'))
  } catch {}
}
