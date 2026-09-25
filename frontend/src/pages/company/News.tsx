import { useMemo, useState } from 'react'
import { useProfiles, useWatchlists } from '../../lib/hooks'
import { FEED_LIMIT, NewsFootnote, NewsToolbar, StoryList, applyFilters, defaultFilters, useFeed, useRefreshNews } from '../../components/news/NewsFeed'
import CoverageChart from '../../components/news/CoverageChart'
import RecentFilings from '../../components/news/RecentFilings'

/** Company tab: the news feed for one ticker, a 30-day coverage chart and the latest SEC filings. */
export default function News({ ticker, otherListing }: { ticker: string; otherListing?: string }) {
  const lists = useWatchlists()
  const watch = useMemo(() => [...new Set((lists.data ?? []).flatMap((w) => w.items.map((i) => i.ticker.toUpperCase())))], [lists.data])
  const watchSet = useMemo(() => new Set(watch), [watch])
  const profiles = useProfiles(useMemo(() => [...new Set([ticker, ...watch])], [ticker, watch]))
  const names = useMemo(() => Object.fromEntries(Object.entries(profiles.data ?? {}).map(([t, p]) => [t, p.name])), [profiles.data])
  // one ticker has far fewer stories than a watchlist, so the default window is a week
  const [filters, setFilters] = useState(defaultFilters('7d'))
  // a dual-listed company's stories are tagged with either listing (usually the US one)
  const tickers = useMemo(() => (otherListing ? [ticker, otherListing] : [ticker]), [ticker, otherListing])
  const feed = useFeed(tickers, filters.period, filters.q)
  const refresh = useRefreshNews(tickers)
  const all = feed.data ?? []
  const stories = applyFilters(all, filters)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 900px) minmax(280px, 360px)', gap: 20, alignItems: 'start' }}>
      <div style={{ minWidth: 0 }}>
        <NewsToolbar value={filters} onChange={setFilters} stories={all} onRefresh={() => refresh.mutate()} refreshing={refresh.isPending} />
        <StoryList
          stories={stories}
          all={all}
          filters={filters}
          onChange={setFilters}
          names={names}
          watch={watchSet}
          hideTicker={ticker}
          loading={feed.isLoading}
          error={feed.error}
          emptyAction={
            <button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              {refresh.isPending ? 'Checking sources…' : `Check for ${ticker} news now`}
            </button>
          }
        />
        <NewsFootnote stories={all} truncated={all.length >= FEED_LIMIT} />
      </div>
      <div className="stack">
        <CoverageChart ticker={ticker} />
        <RecentFilings ticker={ticker.endsWith('.TA') && otherListing ? otherListing : ticker} />
      </div>
    </div>
  )
}
