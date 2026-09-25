import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/** Chart palette (dark). Categorical slots were stepped into the dark lightness band and validated for
 * colour-vision deficiency against the card surface (#151920): blue → amber → violet, in that fixed order. */
export const VIZ = {
  blue: '#3987e5',
  amber: '#c98500',
  violet: '#9085e9',
  text: '#e7eaf0',
  muted: '#8a93a3',
  faint: '#626b7a',
  grid: '#262d38',
  accent: '#f5a524',
  up: '#2fbf71',
  down: '#f0565a',
  neutral: '#383835',
}

const FONT = 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif'

/** Top-level option keys that replace the mono defaults of components/charts/EChart. */
export const vizBase = {
  textStyle: { color: VIZ.muted, fontFamily: FONT, fontSize: 11 },
  tooltip: {
    trigger: 'axis' as const,
    backgroundColor: '#1b2029',
    borderColor: '#343c4a',
    textStyle: { color: VIZ.text, fontSize: 12, fontFamily: FONT },
    extraCssText: 'box-shadow: 0 6px 20px rgba(0,0,0,.45); border-radius: 6px;',
  },
  legend: { top: 0, left: 0, icon: 'roundRect', itemWidth: 10, itemHeight: 10, itemGap: 14, textStyle: { color: VIZ.muted, fontSize: 12, fontFamily: FONT } },
}

export const axisCat = {
  axisLine: { lineStyle: { color: VIZ.grid } },
  axisTick: { show: false },
  axisLabel: { color: VIZ.muted, fontSize: 11 },
}

export const axisVal = {
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: VIZ.muted, fontSize: 11 },
  splitLine: { lineStyle: { color: VIZ.grid, type: 'dashed' as const } },
}

/** Axis money: 390B, 1.2T, 850M (the currency is named in the card hint). */
export function compactMoney(v: number): string {
  if (!Number.isFinite(v)) return ''
  const a = Math.abs(v)
  const s = v < 0 ? '-' : ''
  const fmt = (x: number) => (x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, ''))
  if (a >= 1e12) return `${s}${fmt(a / 1e12)}T`
  if (a >= 1e9) return `${s}${fmt(a / 1e9)}B`
  if (a >= 1e6) return `${s}${fmt(a / 1e6)}M`
  if (a >= 1e3) return `${s}${fmt(a / 1e3)}K`
  return `${s}${fmt(a)}`
}

/** Bars get rounded data-ends; negative bars are rounded at the bottom instead of the top. */
export function barData(values: (number | null | undefined)[]) {
  return values.map((v) => (v === null || v === undefined ? null : v < 0 ? { value: v, itemStyle: { borderRadius: [0, 0, 3, 3] } } : v))
}

export function currencySymbol(cur: string | null | undefined): string {
  if (!cur) return ''
  return cur === 'USD' ? '$' : cur === 'ILS' ? '₪' : cur === 'EUR' ? '€' : cur === 'GBP' ? '£' : `${cur} `
}

/** Collapsible card section (details/summary). Children mount only when open, so closed sections cost no requests.
 * Bump `openSignal` to open it programmatically and scroll it into view. */
export function Disclosure({ title, hint, children, defaultOpen = false, openSignal = 0 }: { title: ReactNode; hint?: ReactNode; children: ReactNode; defaultOpen?: boolean; openSignal?: number }) {
  const [open, setOpen] = useState(defaultOpen)
  const ref = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    if (!openSignal) return
    setOpen(true)
    requestAnimationFrame(() => ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }, [openSignal])
  return (
    <details ref={ref} className="card" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)} style={{ padding: 0 }}>
      <summary style={{ cursor: 'pointer', padding: '14px 18px', listStyle: 'none', display: 'flex', alignItems: 'baseline', gap: 10, userSelect: 'none' }}>
        <span aria-hidden style={{ color: 'var(--faint)', width: 10, display: 'inline-block', transition: 'transform .12s', transform: open ? 'rotate(90deg)' : 'none' }}>
          ▸
        </span>
        <span style={{ fontSize: 14.5, fontWeight: 600 }}>{title}</span>
        {hint && <span style={{ color: 'var(--faint)', fontSize: 12 }}>{hint}</span>}
      </summary>
      {open && <div style={{ padding: '0 18px 16px' }}>{children}</div>}
    </details>
  )
}

/** Small uppercase-free sub-heading inside a card. */
export function SubHead({ children }: { children: ReactNode }) {
  return <div className="section-title">{children}</div>
}
