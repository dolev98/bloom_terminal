import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { QuoteMsg } from './ws'
import { dateTimeIL } from './format'
import { usMarketOpen } from './market'

export type Profile = { ticker: string; name: string | null; kind: string; currency: string; cik?: number; other_listing?: string }
export type Watchlist = { id: number; name: string; items: { id: number; ticker: string; note?: string | null }[] }

export function useProfile(ticker: string) {
  return useQuery({ queryKey: ['profile', ticker], queryFn: () => api<Profile>(`/api/market/profile/${encodeURIComponent(ticker)}`), staleTime: 3600_000 })
}

export function useProfiles(tickers: string[]) {
  return useQuery({
    queryKey: ['profiles', tickers.join(',')],
    queryFn: async () => {
      const out: Record<string, Profile> = {}
      await Promise.all(tickers.map(async (t) => (out[t] = await api<Profile>(`/api/market/profile/${encodeURIComponent(t)}`).catch(() => ({ ticker: t, name: null, kind: 'stock', currency: 'USD' })))))
      return out
    },
    enabled: tickers.length > 0,
    staleTime: 3600_000,
  })
}

export function useWatchlists() {
  return useQuery({ queryKey: ['watchlists'], queryFn: () => api<Watchlist[]>('/api/watchlists') })
}

export function useSparklines(tickers: string[], n = 30) {
  const key = tickers.join(',')
  return useQuery({ queryKey: ['sparklines', key, n], queryFn: () => api<Record<string, number[]>>(`/api/market/sparklines?tickers=${encodeURIComponent(key)}&n=${n}`), enabled: tickers.length > 0, staleTime: 600_000 })
}

/** Initial quotes over REST (the websocket then keeps them current). */
export function useQuotesREST(tickers: string[]) {
  const key = tickers.join(',')
  return useQuery({ queryKey: ['quotes-rest', key], queryFn: () => api<QuoteMsg[]>(`/api/market/quotes?tickers=${encodeURIComponent(key)}`), enabled: tickers.length > 0, staleTime: 30_000, refetchInterval: 120_000 })
}

/** Honest freshness text for a quote. Yahoo quotes carry the time WE fetched them, not the trade time,
 *  so they are labelled "delayed" instead of showing a misleading timestamp. */
export function quoteFreshness(q?: QuoteMsg | null): { text: string; tone: 'live' | 'delayed' | 'old' | 'none' } {
  if (!q) return { text: 'No price yet', tone: 'none' }
  if (q.source?.startsWith('yf')) return { text: 'Delayed quote (Yahoo Finance)', tone: 'delayed' }
  const ageMin = (Date.now() - new Date(q.ts.endsWith('Z') || q.ts.includes('+') ? q.ts : `${q.ts}Z`).getTime()) / 60000
  const t = new Date(q.ts.endsWith('Z') || q.ts.includes('+') ? q.ts : `${q.ts}Z`)
  if (ageMin <= 20 && !q.ticker.endsWith('.TA') && !usMarketOpen(t)) {
    const etHour = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', hour12: false }).format(t)) % 24
    return { text: `${etHour < 12 ? 'Pre-market' : 'After-hours'} trade ${dateTimeIL(q.ts)}`, tone: 'live' }
  }
  if (ageMin <= 20) return { text: `Live · last trade ${dateTimeIL(q.ts)}`, tone: 'live' }
  return { text: `Last trade ${dateTimeIL(q.ts)}`, tone: 'old' }
}

export function isIndexLike(kind?: string) {
  return kind === 'index' || kind === 'etf' || kind === 'fx' || kind === 'commodity' || kind === 'crypto'
}
