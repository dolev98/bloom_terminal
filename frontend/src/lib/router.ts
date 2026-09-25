import { useEffect, useSyncExternalStore } from 'react'

/** Hash router: #/company/AAPL/financials?x=1 → { path: ['company','AAPL','financials'], query: {x:'1'} } */
export type Route = { path: string[]; query: Record<string, string> }

function parse(): Route {
  const raw = decodeURIComponent(window.location.hash.replace(/^#\/?/, ''))
  const [p, q = ''] = raw.split('?')
  const path = p.split('/').filter(Boolean)
  const query: Record<string, string> = {}
  for (const part of q.split('&').filter(Boolean)) {
    const [k, v = ''] = part.split('=')
    query[decodeURIComponent(k)] = decodeURIComponent(v)
  }
  return { path, query }
}

let current = parse()
const listeners = new Set<() => void>()
window.addEventListener('hashchange', () => {
  current = parse()
  listeners.forEach((l) => l())
})

export function useRoute(): Route {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb)
      return () => listeners.delete(cb)
    },
    () => current,
  )
}

export function href(path: string, query?: Record<string, string | number | undefined | null>): string {
  const segs = path.split('/').filter(Boolean).map(encodeURIComponent).join('/')
  const qs = query
    ? Object.entries(query)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join('&')
    : ''
  return `#/${segs}${qs ? `?${qs}` : ''}`
}

export function navigate(path: string, query?: Record<string, string | number | undefined | null>, replace = false) {
  const h = href(path, query)
  if (replace) history.replaceState(null, '', h)
  else if (window.location.hash !== h) window.location.hash = h
  if (replace) {
    current = parse()
    listeners.forEach((l) => l())
  }
}

export function useTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · Terminal` : 'Terminal'
  }, [title])
}
