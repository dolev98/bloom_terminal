import type { ReactNode } from 'react'
import { pct, price as fmtPrice } from '../lib/format'

export function Page({ title, sub, actions, children, wide }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`page ${wide ? 'wide' : ''}`}>
      <div className="page-header">
        <div>
          <h1>{title}</h1>
          {sub && <div className="sub">{sub}</div>}
        </div>
        {actions && <div className="actions">{actions}</div>}
      </div>
      {children}
    </div>
  )
}

export function Card({ title, hint, actions, children, foot, flush, className, style }: { title?: ReactNode; hint?: ReactNode; actions?: ReactNode; children: ReactNode; foot?: ReactNode; flush?: boolean; className?: string; style?: React.CSSProperties }) {
  return (
    <section className={`card ${flush ? 'flush' : ''} ${className ?? ''}`} style={style}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {hint && <span className="hint">{hint}</span>}
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      {children}
      {foot && <div className="card-foot">{foot}</div>}
    </section>
  )
}

export function Stat({ label, value, sub, tone, help }: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: 'up' | 'down' | ''; help?: string }) {
  return (
    <div className="stat">
      <div className="k">
        {label}
        {help && <Help text={help} />}
      </div>
      <div className={`v ${tone ?? ''}`}>{value}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  )
}

export function Help({ text }: { text: string }) {
  return (
    <span className="help" title={text}>
      ?
    </span>
  )
}

export function Change({ value, digits = 2, arrow = false }: { value: number | null | undefined; digits?: number; arrow?: boolean }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return <span className="faint">—</span>
  const cls = value > 0 ? 'up' : value < 0 ? 'down' : 'muted'
  return (
    <span className={`num ${cls}`}>
      {arrow ? (value > 0 ? '▲ ' : value < 0 ? '▼ ' : '') : ''}
      {pct(value, digits)}
    </span>
  )
}

export function Price({ value }: { value: number | null | undefined }) {
  return <span className="num">{fmtPrice(value)}</span>
}

export function Empty({ title, children, actions }: { title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="empty">
      <div className="title">{title}</div>
      {children && <div>{children}</div>}
      {actions && <div className="actions">{actions}</div>}
    </div>
  )
}

export function Loading({ lines = 3 }: { lines?: number }) {
  return (
    <div className="stack" style={{ gap: 8 }}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  )
}

export function ErrorBox({ error, what }: { error: unknown; what?: string }) {
  const msg = String((error as Error)?.message ?? error)
  return (
    <div className="notice err">
      <span>⚠</span>
      <div className="grow">
        {what ? <b>Could not load {what}. </b> : null}
        <span className="muted">{msg}</span>
      </div>
    </div>
  )
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: (T | [T, string])[]; onChange: (v: T) => void }) {
  return (
    <div className="segmented">
      {options.map((o) => {
        const [v, label] = Array.isArray(o) ? o : [o, o]
        return (
          <button key={v} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

export function Tabs<T extends string>({ value, tabs, onChange }: { value: T; tabs: (T | [T, string])[]; onChange: (v: T) => void }) {
  return (
    <div className="tabs">
      {tabs.map((t) => {
        const [v, label] = Array.isArray(t) ? t : [t, t]
        return (
          <button key={v} className={v === value ? 'on' : ''} onClick={() => onChange(v)}>
            {label}
          </button>
        )
      })}
    </div>
  )
}

export function Importance({ level, max = 3 }: { level: number; max?: number }) {
  return (
    <span className="imp" title={`Importance ${level} of ${max}`}>
      {Array.from({ length: max }).map((_, i) => (
        <i key={i} className={i < level ? 'on' : ''} />
      ))}
    </span>
  )
}

const FLAGS: Record<string, string> = { US: '🇺🇸', IL: '🇮🇱', EA: '🇪🇺', EU: '🇪🇺', DE: '🇩🇪', GB: '🇬🇧', JP: '🇯🇵', CN: '🇨🇳', CA: '🇨🇦', CH: '🇨🇭', AU: '🇦🇺', GLOBAL: '🌐' }
export function Flag({ cc }: { cc?: string | null }) {
  if (!cc) return null
  return <span title={cc}>{FLAGS[cc] ?? cc}</span>
}
