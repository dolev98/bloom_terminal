import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { ago } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Empty, ErrorBox, Help, Loading, Segmented } from '../../ui'
import StoryCard from './StoryCard'
import { IMPORTANCE_HELP, IMPORTANT, PERIODS, PERIOD_PHRASE, TYPES, sourcesSentence, storyType } from './model'
import type { Period, Story, StoryType } from './model'

export const FEED_LIMIT = 500

export type FeedFilters = { q: string; period: Period; importantOnly: boolean; types: StoryType[] }
export const defaultFilters = (period: Period): FeedFilters => ({ q: '', period, importantOnly: true, types: TYPES.map(([t]) => t) })

/** One request per (tickers, period, search); importance/type/ticker filters are applied in the browser so the
 *  counts next to every control are exact. */
export function useFeed(tickers: string[], period: Period, q: string) {
  const [dq, setDq] = useState(q)
  useEffect(() => {
    const t = setTimeout(() => setDq(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])
  const key = tickers.join(',')
  return useQuery({
    queryKey: ['news-feed', key, period, dq],
    queryFn: () => {
      const p = new URLSearchParams({ tickers: key, since: period, limit: String(FEED_LIMIT) })
      if (dq) p.set('q', dq)
      return api<Story[]>(`/api/news/feed?${p}`)
    },
    enabled: tickers.length > 0,
    refetchInterval: 120_000,
    placeholderData: (prev) => prev,
  })
}

export function applyFilters(stories: Story[], f: FeedFilters, ticker?: string | null, skip?: 'importance' | 'types') {
  return stories.filter(
    (s) =>
      (skip === 'importance' || !f.importantOnly || s.importance >= IMPORTANT) &&
      (skip === 'types' || f.types.includes(storyType(s.kind))) &&
      (!ticker || s.tickers.some((t) => t.ticker === ticker)),
  )
}

export function useRefreshNews(tickers?: string[]) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api<{ sources: Record<string, { status: string; inserted?: number }> }>(`/api/news/poll${tickers?.length ? `?tickers=${encodeURIComponent(tickers.join(','))}` : ''}`, { method: 'POST' }),
    onSuccess: (r) => {
      const n = Object.values(r.sources ?? {}).reduce((a, s) => a + (s.inserted ?? 0), 0)
      toast(n ? `Found ${n} new article${n === 1 ? '' : 's'}` : 'No new articles since the last check', n ? 'ok' : 'info')
      qc.invalidateQueries({ queryKey: ['news-feed'] })
      qc.invalidateQueries({ queryKey: ['news-sources'] })
      qc.invalidateQueries({ queryKey: ['news-stats'] })
      qc.invalidateQueries({ queryKey: ['news-filings'] })
    },
    onError: (e) => toast(`Could not check the news sources: ${String((e as Error).message ?? e)}`, 'err'),
  })
}

export function NewsToolbar({
  value,
  onChange,
  stories,
  ticker,
  onRefresh,
  refreshing,
}: {
  value: FeedFilters
  onChange: (f: FeedFilters) => void
  stories: Story[]
  ticker?: string | null
  onRefresh: () => void
  refreshing: boolean
}) {
  const byImp = applyFilters(stories, value, ticker, 'importance')
  const nImportant = byImp.filter((s) => s.importance >= IMPORTANT).length
  const byType = applyFilters(stories, value, ticker, 'types')
  const toggleType = (t: StoryType) => {
    const on = value.types.includes(t)
    const next = on ? value.types.filter((x) => x !== t) : [...value.types, t]
    onChange({ ...value, types: next.length ? next : TYPES.map(([x]) => x) })
  }
  return (
    <div className="stack" style={{ gap: 10, marginBottom: 14 }}>
      <div className="row" style={{ gap: 10 }}>
        <input
          type="search"
          placeholder="Search headlines (Hebrew works too)"
          value={value.q}
          dir="auto"
          onChange={(e) => onChange({ ...value, q: e.target.value })}
          style={{ width: 260 }}
          aria-label="Search headlines"
        />
        <Segmented value={value.period} options={PERIODS} onChange={(period) => onChange({ ...value, period })} />
        <span className="row" style={{ gap: 6 }}>
          <span className="muted small">Show</span>
          <Segmented
            value={value.importantOnly ? 'imp' : 'all'}
            options={[
              ['imp', `Important only · ${nImportant}`],
              ['all', `All · ${byImp.length}`],
            ]}
            onChange={(v) => onChange({ ...value, importantOnly: v === 'imp' })}
          />
          <Help text={IMPORTANCE_HELP} />
        </span>
        <span className="spacer" />
        <button onClick={onRefresh} disabled={refreshing} title="Ask every news source for new articles now">
          {refreshing ? 'Checking sources…' : 'Refresh'}
        </button>
      </div>
      <div className="row" style={{ gap: 6 }}>
        {TYPES.map(([t, label]) => {
          const n = byType.filter((s) => storyType(s.kind) === t).length
          return (
            <span key={t} className={`chip ${value.types.includes(t) ? 'on' : ''}`} role="checkbox" aria-checked={value.types.includes(t)} tabIndex={0} onClick={() => toggleType(t)} onKeyDown={(e) => e.key === 'Enter' && toggleType(t)}>
              {label} <span className="num faint">{n}</span>
            </span>
          )
        })}
      </div>
    </div>
  )
}

export function StoryList({
  stories,
  all,
  filters,
  onChange,
  ticker,
  names,
  watch,
  hideTicker,
  loading,
  error,
  emptyAction,
}: {
  stories: Story[]
  all: Story[]
  ticker?: string | null
  filters: FeedFilters
  onChange: (f: FeedFilters) => void
  names: Record<string, string | null | undefined>
  watch: Set<string>
  hideTicker?: string
  loading: boolean
  error: unknown
  emptyAction?: ReactNode
}) {
  if (loading) return <Loading lines={6} />
  if (error) return <ErrorBox error={error} what="the news feed" />
  if (!stories.length) {
    const periodLabel = PERIOD_PHRASE[filters.period]
    const relaxed = { ...filters, importantOnly: false, types: TYPES.map(([t]) => t) }
    const unfiltered = applyFilters(all, relaxed, ticker)
    if (filters.q.trim() && !unfiltered.length)
      return (
        <Empty title={`No headlines match “${filters.q.trim()}”`} actions={<button onClick={() => onChange({ ...filters, q: '' })}>Clear search</button>}>
          Search looks at headlines and article summaries from {periodLabel}.
        </Empty>
      )
    if (!unfiltered.length)
      return (
        <Empty title={`No stories from ${periodLabel}`} actions={emptyAction}>
          None of the news sources has published anything about these companies in this period yet.
        </Empty>
      )
    return (
      <Empty
        title={filters.importantOnly ? `No important stories from ${periodLabel}` : 'No stories of the selected types'}
        actions={
          unfiltered.length ? (
            <button onClick={() => onChange(relaxed)}>
              Show all {unfiltered.length} stor{unfiltered.length === 1 ? 'y' : 'ies'}
            </button>
          ) : undefined
        }
      >
        {filters.importantOnly ? 'Only lightly covered stories came in during this period.' : 'Turn the other story types back on to see them.'}
      </Empty>
    )
  }
  return (
    <div className="card" style={{ padding: '4px 18px' }}>
      <div className="list">
        {stories.map((s) => (
          <StoryCard key={s.id} story={s} names={names} watch={watch} hideTicker={hideTicker} />
        ))}
      </div>
    </div>
  )
}

type SourceRow = { id: string; enabled: boolean; usable: boolean; last_fetch_at: string | null }

/** Footer: where stories come from, when we last looked, and (quietly) whether AI summaries are on. */
export function NewsFootnote({ stories, truncated }: { stories: Story[]; truncated: boolean }) {
  const llm = useQuery({ queryKey: ['sys-llm'], queryFn: () => api<{ configured: boolean }>('/api/sys/llm'), staleTime: 300_000 })
  const sources = useQuery({ queryKey: ['news-sources'], queryFn: () => api<SourceRow[]>('/api/news/sources'), staleTime: 60_000 })
  const last = useMemo(() => {
    const ts = (sources.data ?? []).filter((s) => s.enabled && s.usable && s.last_fetch_at).map((s) => s.last_fetch_at as string)
    return ts.length ? ts.sort().at(-1)! : null
  }, [sources.data])
  const from = sourcesSentence(stories.flatMap((s) => s.sources))
  return (
    <div className="card-foot stack" style={{ gap: 4, marginTop: 14 }}>
      {truncated && <div>Showing the newest {FEED_LIMIT} stories for this period.</div>}
      <div>
        {from ? `Collected from ${from}. ` : ''}
        {last ? `Last checked for new articles ${ago(last)}. ` : ''}Times are Israel time.
      </div>
      {llm.data && !llm.data.configured && (
        <div>
          AI summaries are off — add a Claude API key in <a href="#/settings">Settings</a>.
        </div>
      )}
    </div>
  )
}
