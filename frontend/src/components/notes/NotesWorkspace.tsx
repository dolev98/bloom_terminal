import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { href } from '../../lib/router'
import { useProfiles } from '../../lib/hooks'
import { ago, dateLabel, dateTimeIL, price } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Card, Empty, ErrorBox, Loading } from '../../ui'

export type Note = { id: number; title: string; body: string; tickers: string[]; tags: string[]; price_at_note: Record<string, number>; created_at: string; updated_at: string }
type Draft = { title: string; tickers: string; tags: string; body: string }

const split = (s: string, upper = false) => [...new Set(s.split(/[,\s]+/).map((t) => (upper ? t.trim().toUpperCase() : t.trim())).filter(Boolean))]
const toDraft = (n: Note): Draft => ({ title: n.title, tickers: n.tickers.join(', '), tags: n.tags.join(', '), body: n.body })
const blank = (ticker?: string): Draft => ({ title: '', tickers: ticker ?? '', tags: '', body: '' })
const same = (a: Draft, b: Draft) => a.title === b.title && a.body === b.body && split(a.tickers, true).join() === split(b.tickers, true).join() && split(a.tags).join() === split(b.tags).join()

/** Right-to-left when the text is mostly Hebrew. */
export function dirFor(text: string): 'rtl' | 'ltr' {
  const he = (text.match(/[֐-׿]/g) ?? []).length
  const lat = (text.match(/[A-Za-z]/g) ?? []).length
  return he > 0 && he >= lat ? 'rtl' : 'ltr'
}

const firstLine = (body: string) => body.split('\n').map((l) => l.trim()).find(Boolean) ?? ''

/** Two-pane notes: searchable list on the left, editor on the right. With `ticker`, only that company's
 *  notes are listed and new notes are tagged with it. */
export default function NotesWorkspace({ ticker }: { ticker?: string }) {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [dq, setDq] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setDq(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])
  const notes = useQuery({
    queryKey: ['notes', dq, ticker ?? ''],
    queryFn: () => {
      const p = new URLSearchParams()
      if (dq) p.set('q', dq)
      if (ticker) p.set('ticker', ticker)
      return api<Note[]>(`/api/notes?${p}`)
    },
    placeholderData: (prev) => prev,
  })
  const list = notes.data ?? []
  // the open note is kept as an object so a search that hides it from the list does not close it
  const [sel, setSel] = useState<Note | 'new' | null>(null)
  const current = sel && sel !== 'new' ? sel : null
  const [draft, setDraft] = useState<Draft>(blank(ticker))
  const base = current ? toDraft(current) : blank(ticker)
  const dirty = !same(draft, base)
  const titleRef = useRef<HTMLInputElement>(null)

  // show the most useful thing right away: the latest note, or a blank one when there are none
  useEffect(() => {
    if (sel !== null || notes.isLoading || dq) return
    if (list.length) {
      setSel(list[0])
      setDraft(toDraft(list[0]))
    } else setSel('new')
  }, [sel, notes.isLoading, list, dq])

  const open = (n: Note | 'new') => {
    if (dirty && !window.confirm('Discard the unsaved changes to this note?')) return
    if (n === 'new') {
      setSel('new')
      setDraft(blank(ticker))
      setTimeout(() => titleRef.current?.focus(), 0)
    } else {
      setSel(n)
      setDraft(toDraft(n))
    }
  }

  const save = useMutation({
    mutationFn: () => {
      const body = { title: draft.title.trim(), body: draft.body, tickers: split(draft.tickers, true), tags: split(draft.tags) }
      return current ? api<Note>(`/api/notes/${current.id}`, { method: 'PUT', json: body }) : api<Note>('/api/notes', { method: 'POST', json: body })
    },
    onSuccess: (n) => {
      qc.setQueryData<Note[]>(['notes', dq, ticker ?? ''], (old) => [n, ...(old ?? []).filter((x) => x.id !== n.id)])
      qc.invalidateQueries({ queryKey: ['notes'] })
      setSel(n)
      setDraft(toDraft(n))
      toast('Note saved', 'ok')
    },
    onError: (e) => toast(`Could not save the note: ${String((e as Error).message ?? e)}`, 'err'),
  })
  const del = useMutation({
    mutationFn: (id: number) => api(`/api/notes/${id}`, { method: 'DELETE' }),
    onSuccess: (_r, id) => {
      const rest = list.filter((n) => n.id !== id)
      qc.setQueryData<Note[]>(['notes', dq, ticker ?? ''], rest)
      qc.invalidateQueries({ queryKey: ['notes'] })
      if (rest.length) {
        setSel(rest[0])
        setDraft(toDraft(rest[0]))
      } else {
        setSel('new')
        setDraft(blank(ticker))
      }
      toast('Note deleted', 'info')
    },
    onError: (e) => toast(`Could not delete the note: ${String((e as Error).message ?? e)}`, 'err'),
  })
  const canSave = dirty && (draft.title.trim() || draft.body.trim()) && !save.isPending

  const priceTickers = useMemo(() => Object.keys(current?.price_at_note ?? {}), [current])
  const profiles = useProfiles(priceTickers)
  const cur = (t: string) => {
    const c = profiles.data?.[t]?.currency
    return c === 'ILS' ? '₪' : c === 'USD' || !c ? '$' : `${c} `
  }

  const onKey = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
      e.preventDefault()
      if (canSave) save.mutate()
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 330px) minmax(0, 1fr)', gap: 16, alignItems: 'start' }}>
      <Card flush title={ticker ? `Notes about ${ticker}` : 'Your notes'} actions={<button onClick={() => open('new')}>New note</button>}>
        <div style={{ padding: '0 18px 10px' }}>
          <input type="search" placeholder="Search notes (Hebrew works too)" value={q} dir="auto" onChange={(e) => setQ(e.target.value)} style={{ width: '100%' }} aria-label="Search notes" />
        </div>
        <div style={{ maxHeight: 'calc(100vh - 260px)', overflow: 'auto', padding: '0 8px 8px' }}>
          {notes.isLoading && (
            <div style={{ padding: 10 }}>
              <Loading lines={4} />
            </div>
          )}
          {notes.error && <ErrorBox error={notes.error} what="your notes" />}
          {notes.data && !list.length && (
            <div style={{ padding: 10 }}>
              {dq ? (
                <Empty title={`No notes match “${dq}”`} actions={<button onClick={() => setQ('')}>Clear search</button>}>
                  Search looks at titles, text, tickers and tags.
                </Empty>
              ) : (
                <Empty title={ticker ? `No notes about ${ticker} yet` : 'No notes yet'}>
                  {ticker ? `Write down your thesis or questions about ${ticker}. The note is tagged with ${ticker} and also appears on the Notes page.` : 'Write down a thesis, a question or something you noticed. Tag it with tickers and it also shows up on those company pages.'}
                </Empty>
              )}
            </div>
          )}
          <div className="list">
            {list.map((n) => {
              const on = n.id === current?.id
              const preview = firstLine(n.body)
              return (
                <div
                  key={n.id}
                  className="list-item click"
                  role="button"
                  tabIndex={0}
                  onClick={() => !on && open(n)}
                  onKeyDown={(e) => e.key === 'Enter' && !on && open(n)}
                  style={{ padding: '9px 10px', borderRadius: 6, background: on ? 'var(--accent-soft)' : undefined, flexDirection: 'column', gap: 2 }}
                >
                  <div className="li-title" dir={dirFor(n.title || preview)} style={{ fontWeight: 500, width: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {n.title || <span className="faint">Untitled note</span>}
                  </div>
                  {preview && preview !== n.title && (
                    <div className="small muted" dir={dirFor(preview)} style={{ width: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {preview}
                    </div>
                  )}
                  <div className="li-meta">
                    {n.tickers.map((t) => (
                      <span key={t} className="badge ticker">
                        {t}
                      </span>
                    ))}
                    <span title={`Last edited ${dateTimeIL(n.updated_at)}`}>{dateLabel(n.updated_at)}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </Card>

      <Card
        title={current ? 'Edit note' : 'New note'}
        hint={current ? `Written ${dateLabel(current.created_at)}${current.updated_at.slice(0, 16) !== current.created_at.slice(0, 16) ? ` · last edited ${ago(current.updated_at)}` : ''}` : undefined}
      >
        <div className="stack" style={{ gap: 12 }} onKeyDown={onKey}>
          <input
            ref={titleRef}
            placeholder="Title"
            value={draft.title}
            dir={dirFor(draft.title)}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            style={{ fontSize: 16, fontWeight: 600, padding: '8px 10px' }}
            aria-label="Title"
          />
          <div className="grid-2" style={{ gap: 12 }}>
            <label className="stack" style={{ gap: 4 }}>
              <span className="small muted">Tickers</span>
              <input className="ticker" placeholder="AAPL, TEVA" value={draft.tickers} onChange={(e) => setDraft({ ...draft, tickers: e.target.value })} />
            </label>
            <label className="stack" style={{ gap: 4 }}>
              <span className="small muted">Tags</span>
              <input placeholder="thesis, earnings" value={draft.tags} dir="auto" onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
            </label>
          </div>
          <textarea
            rows={16}
            placeholder="Write here — Hebrew switches to right-to-left automatically."
            value={draft.body}
            dir={dirFor(draft.body)}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })}
            style={{ fontFamily: 'var(--font)', fontSize: 14, resize: 'vertical' }}
            aria-label="Note text"
          />
          {current && priceTickers.length > 0 && (
            <div className="small muted">
              {priceTickers.map((t, i) => (
                <span key={t}>
                  {i > 0 ? '; ' : ''}
                  <a className="ticker" href={href(`company/${t}`)}>
                    {t}
                  </a>{' '}
                  was <span className="num">{cur(t)}{price(current.price_at_note[t])}</span>
                </span>
              ))}{' '}
              when you wrote this.
            </div>
          )}
          <div className="row">
            <button className="primary" onClick={() => save.mutate()} disabled={!canSave}>
              {save.isPending ? 'Saving…' : current ? 'Save changes' : 'Save note'}
            </button>
            {current && (
              <button className="ghost danger" onClick={() => window.confirm('Delete this note? This cannot be undone.') && del.mutate(current.id)} disabled={del.isPending}>
                Delete
              </button>
            )}
            <span className="small faint">{dirty ? 'Unsaved changes · ⌘S to save' : current ? 'All changes saved' : ''}</span>
          </div>
        </div>
      </Card>
    </div>
  )
}
