import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type SeriesSpec } from '../../lib/api'
import type { PairDef } from './words'

type Hit = { ticker: string; name: string; kind: string }
type WL = { id: number; name: string; items: { ticker: string }[] }

function useDebounced<T>(v: T, ms: number): T {
  const [d, setD] = useState(v)
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms)
    return () => clearTimeout(t)
  }, [v, ms])
  return d
}

const isTicker = (id: string) => !!id && !id.includes(':')

/** Plain name for a catalog id ("US 10Y Treasury yield") or a ticker ("SPDR S&P 500 ETF (SPY)"). */
export function useSeriesName(id: string): string {
  const cat = useQuery({
    queryKey: ['catalog', 'q', id],
    queryFn: () => api<{ items: SeriesSpec[] }>(`/api/catalog?q=${encodeURIComponent(id)}`),
    enabled: !!id && !isTicker(id),
    staleTime: 3_600_000,
  })
  const prof = useQuery({
    queryKey: ['profile', id],
    queryFn: () => api<{ ticker: string; name?: string }>(`/api/market/profile/${encodeURIComponent(id)}`),
    enabled: isTicker(id),
    staleTime: 3_600_000,
    retry: false,
  })
  if (!id) return ''
  if (isTicker(id)) return prof.data?.name && prof.data.name !== id ? `${prof.data.name} (${id})` : id
  return cat.data?.items.find((s) => s.series_id === id)?.name ?? id
}

type Opt = { id: string; name: string; note: string }

/** Search the data catalog by name, or type a ticker. Shows the plain name; the id/ticker is secondary. */
export function SeriesPicker({ value, onChange, placeholder, width = 300, ariaLabel }: { value: string; onChange: (id: string) => void; placeholder?: string; width?: number; ariaLabel?: string }) {
  const name = useSeriesName(value)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const ref = useRef<HTMLInputElement>(null)
  const dq = useDebounced(q.trim(), 200)
  const cat = useQuery({ queryKey: ['catalog', 'q', dq], queryFn: () => api<{ items: SeriesSpec[] }>(`/api/catalog?q=${encodeURIComponent(dq)}`), enabled: open && dq.length >= 2 })
  const tick = useQuery({ queryKey: ['search', dq], queryFn: () => api<Hit[]>(`/api/market/search?q=${encodeURIComponent(dq)}&limit=6`), enabled: open && dq.length >= 1 })
  const wl = useQuery({ queryKey: ['watchlists'], queryFn: () => api<WL[]>('/api/watchlists'), enabled: open, staleTime: 60_000 })

  const opts = useMemo<Opt[]>(() => {
    if (!dq) {
      const tickers = Array.from(new Set((wl.data ?? []).flatMap((w) => w.items.map((i) => i.ticker)))).slice(0, 10)
      return tickers.map((t) => ({ id: t, name: t, note: 'From your watchlist' }))
    }
    const series = (cat.data?.items ?? []).slice(0, 10).map((s) => ({ id: s.series_id, name: s.name, note: s.series_id }))
    const tickers = (tick.data ?? []).slice(0, 6).map((h) => ({ id: h.ticker, name: h.name, note: h.ticker }))
    const seen = new Set<string>()
    return [...tickers, ...series].filter((o) => (seen.has(o.id) ? false : (seen.add(o.id), true)))
  }, [dq, cat.data, tick.data, wl.data])

  const commit = (id: string) => {
    const v = id.trim()
    if (!v) return
    onChange(isTicker(v) ? v.toUpperCase() : v)
    setQ('')
    setOpen(false)
    ref.current?.blur()
  }

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <input
        ref={ref}
        aria-label={ariaLabel}
        value={open ? q : name}
        placeholder={placeholder}
        title={value}
        style={{ width }}
        onFocus={() => {
          setOpen(true)
          setQ('')
          setHi(0)
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => {
          setQ(e.target.value)
          setHi(0)
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') setHi((h) => Math.min(h + 1, opts.length - 1))
          else if (e.key === 'ArrowUp') setHi((h) => Math.max(h - 1, 0))
          else if (e.key === 'Enter') commit(opts[hi]?.id ?? q)
          else if (e.key === 'Escape') {
            setOpen(false)
            ref.current?.blur()
          }
        }}
      />
      {open && (
        <div
          style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 50, width: Math.max(width, 380), maxHeight: 320, overflow: 'auto', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 8, boxShadow: '0 12px 32px rgba(0,0,0,.5)', padding: 4 }}
        >
          {opts.length === 0 && <div className="faint small" style={{ padding: '8px 10px' }}>{dq ? 'No matches. Press Enter to use it as a ticker.' : 'Type a name (e.g. "10-year", "shekel") or a ticker.'}</div>}
          {opts.map((o, i) => (
            <div
              key={o.id}
              onMouseDown={() => commit(o.id)}
              onMouseEnter={() => setHi(i)}
              style={{ padding: '6px 10px', borderRadius: 6, cursor: 'pointer', background: i === hi ? 'var(--surface-3)' : undefined }}
            >
              <div className="small" style={{ color: 'var(--text)' }}>
                {o.name}
              </div>
              <div className="xs faint mono">{o.note}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const REGION: Record<string, string> = { US: 'United States', IL: 'Israel', EA: 'Euro area', GB: 'United Kingdom', JP: 'Japan', CN: 'China' }
const REGION_ORDER = ['US', 'IL', 'EA', 'GB', 'JP', 'CN']

/** Presets grouped by region, each with its plain-English rationale. */
export function PresetMenu({ pairs, current, onPick, onDelete }: { pairs: PairDef[]; current: string; onPick: (p: PairDef) => void; onDelete: (p: PairDef) => void }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false)
    const k = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('mousedown', h)
    window.addEventListener('keydown', k)
    return () => {
      window.removeEventListener('mousedown', h)
      window.removeEventListener('keydown', k)
    }
  }, [open])
  const groups = useMemo(() => {
    const g = new Map<string, PairDef[]>()
    for (const p of pairs) g.set(p.country ?? 'other', [...(g.get(p.country ?? 'other') ?? []), p])
    return [...g.entries()].sort(([a], [b]) => (REGION_ORDER.indexOf(a) + 1 || 99) - (REGION_ORDER.indexOf(b) + 1 || 99))
  }, [pairs])
  return (
    <div ref={box} style={{ position: 'relative', display: 'inline-block' }}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        Presets ▾
      </button>
      {open && (
        <div style={{ position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 60, width: 520, maxWidth: '86vw', maxHeight: 460, overflow: 'auto', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 8, boxShadow: '0 16px 40px rgba(0,0,0,.55)', padding: 6 }}>
          {groups.map(([cc, list]) => (
            <div key={cc}>
              <div className="xs faint strong" style={{ padding: '8px 10px 4px' }}>
                {REGION[cc] ?? 'Other'}
              </div>
              {list.map((p) => (
                <div
                  key={p.id}
                  onClick={() => {
                    onPick(p)
                    setOpen(false)
                  }}
                  style={{ padding: '7px 10px', borderRadius: 6, cursor: 'pointer', background: p.id === current ? 'var(--accent-soft)' : undefined, display: 'flex', gap: 8 }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = p.id === current ? 'var(--accent-soft)' : 'var(--surface-3)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = p.id === current ? 'var(--accent-soft)' : '')}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="small" style={{ color: 'var(--text)' }}>
                      {p.name}
                      {!p.is_seed && <span className="faint"> · yours</span>}
                    </div>
                    {p.rationale && (
                      <div className="xs muted" style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {p.rationale}
                      </div>
                    )}
                  </div>
                  {!p.is_seed && (
                    <button
                      className="ghost icon"
                      title="Delete this preset"
                      onClick={(e) => {
                        e.stopPropagation()
                        if (confirm(`Delete the preset "${p.name}"?`)) onDelete(p)
                      }}
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
