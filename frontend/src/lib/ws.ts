import { useEffect } from 'react'
import { create } from 'zustand'

export type QuoteMsg = { ticker: string; ts: string; last: number; prev_close?: number | null; change_pct?: number | null; open?: number | null; high?: number | null; low?: number | null; volume?: number | null; currency?: string; source: string; age_s?: number; stale?: boolean }

type QuoteState = { quotes: Record<string, QuoteMsg>; connected: boolean; apply: (qs: QuoteMsg[]) => void; setConnected: (v: boolean) => void }

export const useQuotes = create<QuoteState>((set, get) => ({
  quotes: {},
  connected: false,
  apply: (qs) => {
    const next = { ...get().quotes }
    for (const q of qs) next[q.ticker.toUpperCase()] = { ...next[q.ticker.toUpperCase()], ...q }
    set({ quotes: next })
  },
  setConnected: (v) => set({ connected: v }),
}))

let socket: WebSocket | null = null
let started = false

export function startQuoteSocket() {
  if (started) return
  started = true
  const connect = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${proto}://${location.host}/ws`)
    socket.onopen = () => useQuotes.getState().setConnected(true)
    socket.onclose = () => {
      useQuotes.getState().setConnected(false)
      setTimeout(connect, 3000)
    }
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.type === 'quotes') useQuotes.getState().apply(msg.data)
        else if (msg.type === 'hello') useQuotes.getState().apply(msg.data.quotes ?? [])
        else if (msg.type === 'ping') socket?.send('ping')
      } catch {}
    }
  }
  connect()
}

export function useQuote(ticker: string | undefined) {
  useEffect(() => {
    startQuoteSocket()
  }, [])
  return useQuotes((s) => (ticker ? s.quotes[ticker.toUpperCase()] : undefined))
}

export function useQuoteMap() {
  useEffect(() => {
    startQuoteSocket()
  }, [])
  return useQuotes((s) => s.quotes)
}
