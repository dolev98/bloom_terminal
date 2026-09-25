import { useState, type ReactNode } from 'react'

/** Collapsible card for plumbing details. Children mount only when open, so their queries load lazily. */
export default function Section({ title, hint, summary, children, defaultOpen = false }: { title: string; hint?: ReactNode; summary?: ReactNode; children: () => ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="card" style={{ padding: 0 }}>
      <button
        className="ghost"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        style={{ width: '100%', display: 'flex', alignItems: 'baseline', gap: 10, padding: '13px 18px', borderRadius: 'var(--radius)', textAlign: 'left', whiteSpace: 'normal' }}
      >
        <span className="faint" style={{ width: 12, flexShrink: 0 }}>
          {open ? '▾' : '▸'}
        </span>
        <span className="strong" style={{ fontSize: 14.5, color: 'var(--text)' }}>
          {title}
        </span>
        {hint && <span className="small faint">{hint}</span>}
        {summary && (
          <span className="small muted" style={{ marginLeft: 'auto' }}>
            {summary}
          </span>
        )}
      </button>
      {open && <div style={{ padding: '0 18px 16px' }}>{children()}</div>}
    </section>
  )
}
