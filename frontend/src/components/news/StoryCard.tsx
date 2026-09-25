import { useState } from 'react'
import type { KeyboardEvent, MouseEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { href } from '../../lib/router'
import { ago, dateLabel, dateTimeIL } from '../../lib/format'
import { ErrorBox, Importance, Loading } from '../../ui'
import { filingLabel, importanceLevel, isHebrew, sentimentLabel } from './model'
import type { Story, StoryDetail } from './model'

type Props = {
  story: Story
  names: Record<string, string | null | undefined>
  watch: Set<string>
  /** On a company tab the company's own ticker chip is redundant. */
  hideTicker?: string
}

/** Headline to show: SEC filings get a plain label + the company name instead of "Form 4 insider transaction — NVIDIA CORP". */
export function storyTitle(s: Story, names: Props['names']): string {
  if (s.filing) {
    const t = s.filing.ticker
    return `${filingLabel(s.filing.form, s.filing.items)} — ${names[t] ?? t}`
  }
  return s.title
}

function dirOf(text: string, lang?: string) {
  return lang === 'he' || isHebrew(text) ? 'rtl' : 'ltr'
}

export default function StoryCard({ story: s, names, watch, hideTicker }: Props) {
  const [open, setOpen] = useState(false)
  const title = storyTitle(s, names)
  const tone = sentimentLabel(s.sentiment)
  const tickers = s.tickers
    .map((t) => t.ticker)
    .filter((t) => t !== hideTicker)
    .sort((a, b) => Number(watch.has(b)) - Number(watch.has(a)))
  const shown = tickers.slice(0, 3)
  const publisher = s.filing ? 'SEC EDGAR' : (s.publisher ?? s.publishers[0] ?? '')
  const toggle = (e: MouseEvent | KeyboardEvent) => {
    if ((e.target as HTMLElement).closest('a')) return
    if ('key' in e && e.key !== 'Enter' && e.key !== ' ') return
    e.preventDefault()
    setOpen((v) => !v)
  }
  const when = s.first_seen === s.last_seen ? dateTimeIL(s.last_seen) : `First reported ${dateTimeIL(s.first_seen)} · latest ${dateTimeIL(s.last_seen)}`

  return (
    <div className="list-item click" role="button" tabIndex={0} aria-expanded={open} onClick={toggle} onKeyDown={toggle} style={{ outline: 'none' }}>
      <div className="li-body">
        <div className="li-title" dir={dirOf(title, s.lang)} style={{ fontSize: 14.5, fontWeight: 500 }}>
          <a href={s.filing?.url || s.url} target="_blank" rel="noreferrer" style={{ color: 'inherit' }} title="Open the original in a new tab">
            {title}
          </a>
        </div>
        <div className="li-meta" style={{ marginTop: 4 }}>
          <Importance level={importanceLevel(s.importance)} />
          {publisher && <span className="muted">{publisher}</span>}
          <span className="num" title={when}>
            {ago(s.last_seen)}
          </span>
          {s.n_publishers > 1 && <span>{s.n_publishers} outlets</span>}
          {tone && tone.text !== 'Neutral' && (
            <span className={tone.cls} title="Tone of the wording, estimated automatically">
              {tone.text}
            </span>
          )}
          {shown.map((t) => (
            <a key={t} className="badge ticker" href={href(`company/${t}`)} title={names[t] ?? t} style={{ textDecoration: 'none' }}>
              {t}
            </a>
          ))}
          {tickers.length > shown.length && <span title={tickers.slice(3).join(', ')}>+{tickers.length - shown.length}</span>}
        </div>
        {s.summary && (
          <div dir={dirOf(s.summary)} style={{ marginTop: 6, color: 'var(--text-2)', fontSize: 13.5 }}>
            {s.summary}
          </div>
        )}
        {open && <StoryDetails id={s.id} story={s} names={names} />}
      </div>
    </div>
  )
}

function StoryDetails({ id, story, names }: { id: number; story: Story; names: Props['names'] }) {
  const q = useQuery({ queryKey: ['news-cluster', id], queryFn: () => api<StoryDetail>(`/api/news/clusters/${id}`), staleTime: 60_000 })
  const d = q.data
  const tone = sentimentLabel(story.sentiment)
  return (
    <div style={{ marginTop: 10, paddingLeft: 12, borderLeft: '2px solid var(--border-strong)', cursor: 'default' }} onClick={(e) => e.stopPropagation()}>
      {q.isLoading && <Loading lines={2} />}
      {q.error && <ErrorBox error={q.error} what="the story details" />}
      {d && (
        <div className="stack" style={{ gap: 10 }}>
          {d.why_it_matters && (
            <div dir={dirOf(d.why_it_matters)}>
              <span className="muted">Why it matters: </span>
              {d.why_it_matters}
            </div>
          )}
          {d.facts?.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {d.facts.map((f, i) => (
                <li key={i} dir={dirOf(f)}>
                  {f}
                </li>
              ))}
            </ul>
          )}
          {d.filing && (
            <div className="small">
              {filingLabel(d.filing.form, d.filing.items)} filed by {names[d.filing.ticker] ?? d.filing.ticker}
              {d.filing.filed_at ? ` on ${dateLabel(d.filing.filed_at)}` : ''}.{' '}
              <a href={d.filing.url} target="_blank" rel="noreferrer">
                Open the filing on sec.gov ↗
              </a>
            </div>
          )}
          {!d.filing && (
            <div>
              <div className="small muted" style={{ marginBottom: 4 }}>
                {d.items.length === 1 ? 'One article so far' : `${d.items.length} articles from ${d.n_publishers} outlet${d.n_publishers === 1 ? '' : 's'}`}
                {tone ? ` · tone: ${tone.text.toLowerCase()}` : ''}
              </div>
              <div className="stack" style={{ gap: 6 }}>
                {[...d.items].reverse().map((it) => (
                  <div key={it.id} className="small">
                    <a href={it.url} target="_blank" rel="noreferrer" dir={dirOf(it.title, it.lang)} style={{ display: 'block' }}>
                      {it.title}
                    </a>
                    <span className="faint">
                      {it.publisher ?? '—'} · {dateTimeIL(it.published_at)}
                    </span>
                    {it.snippet && it.snippet.length > it.title.length + 30 && (
                      <div className="muted" dir={dirOf(it.snippet, it.lang)} style={{ marginTop: 2 }}>
                        {it.snippet.length > 320 ? `${it.snippet.slice(0, 320)}…` : it.snippet}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
