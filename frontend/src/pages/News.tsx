import { useMemo, useState } from 'react'
import { navigate, useRoute } from '../lib/router'
import { useProfiles, useWatchlists } from '../lib/hooks'
import { Empty, Loading, Page } from '../ui'
import { FEED_LIMIT, NewsFootnote, NewsToolbar, StoryList, applyFilters, defaultFilters, useFeed, useRefreshNews } from '../components/news/NewsFeed'

/** Watchlist-wide news: one clean column of stories, a short ticker filter on the left. */
export default function NewsPage() {
  const route = useRoute()
  const selected = route.query.ticker?.toUpperCase() || null
  const lists = useWatchlists()
  const watch = useMemo(() => [...new Set((lists.data ?? []).flatMap((w) => w.items.map((i) => i.ticker.toUpperCase())))], [lists.data])
  const watchSet = useMemo(() => new Set(watch), [watch])
  const profiles = useProfiles(watch)
  const names = useMemo(() => Object.fromEntries(Object.entries(profiles.data ?? {}).map(([t, p]) => [t, p.name])), [profiles.data])
  const [filters, setFilters] = useState(defaultFilters('24h'))
  const feed = useFeed(watch, filters.period, filters.q)
  const refresh = useRefreshNews()
  const all = feed.data ?? []
  const stories = applyFilters(all, filters, selected)
  const pick = (t: string | null) => navigate('news', { ticker: t ?? undefined }, true)

  if (lists.isLoading)
    return (
      <Page title="News">
        <Loading lines={6} />
      </Page>
    )
  if (!watch.length)
    return (
      <Page title="News">
        <Empty title="Your watchlist is empty" actions={<a href="#/watchlist">Add companies to your watchlist</a>}>
          News is collected for the companies on your watchlist.
        </Empty>
      </Page>
    )

  const countFor = (t: string | null) => applyFilters(all, filters, t).length
  return (
    <Page title="News" sub="Stories about the companies on your watchlist, newest first. Articles about the same event are grouped into one story — click a story to see every source.">
      <div style={{ display: 'grid', gridTemplateColumns: '210px minmax(0, 900px)', gap: 24, alignItems: 'start' }}>
        <nav aria-label="Filter by company" style={{ position: 'sticky', top: 0 }}>
          <div className="section-title" style={{ marginTop: 4 }}>
            Company
          </div>
          <a className={`nav-item ${selected ? '' : 'on'}`} onClick={() => pick(null)}>
            <span>All watchlist</span>
            <span className="count num">{feed.data ? countFor(null) : ''}</span>
          </a>
          {watch.map((t) => {
            const n = feed.data ? countFor(t) : null
            return (
              <a key={t} className={`nav-item ${selected === t ? 'on' : ''}`} onClick={() => pick(selected === t ? null : t)} title={names[t] ?? t} style={{ opacity: n === 0 && selected !== t ? 0.55 : 1 }}>
                <span className="ticker" style={{ fontSize: 12.5, minWidth: 44 }}>
                  {t}
                </span>
                <span className="muted small" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
                  {names[t] ?? ''}
                </span>
                <span className="count num">{n ?? ''}</span>
              </a>
            )
          })}
          <div className="faint xs" style={{ padding: '8px 10px' }}>
            Counts follow the filters on the right.
          </div>
        </nav>
        <div style={{ minWidth: 0 }}>
          <NewsToolbar value={filters} onChange={setFilters} stories={all} ticker={selected} onRefresh={() => refresh.mutate()} refreshing={refresh.isPending} />
          <StoryList
            stories={stories}
            all={all}
            ticker={selected}
            filters={filters}
            onChange={setFilters}
            names={names}
            watch={watchSet}
            loading={feed.isLoading}
            error={feed.error}
            emptyAction={
              filters.period !== '7d' ? (
                <button onClick={() => setFilters({ ...filters, period: '7d' })}>Show the last week</button>
              ) : (
                <button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                  {refresh.isPending ? 'Checking sources…' : 'Check for news now'}
                </button>
              )
            }
          />
          <NewsFootnote stories={all} truncated={all.length >= FEED_LIMIT} />
        </div>
      </div>
    </Page>
  )
}
