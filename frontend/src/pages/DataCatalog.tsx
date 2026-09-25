import { Fragment, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { href, navigate, useRoute } from '../lib/router'
import { toast } from '../lib/toast'
import { Card, Empty, ErrorBox, Flag, Loading, Page } from '../ui'
import AddSeriesDialog from '../components/catalog/AddSeriesDialog'
import { CATEGORIES, categoryLabel, categoryRank, countryName, formatValue, freqLabel, freshnessOf, periodLabel, useCatalog, type CatalogItem } from '../components/catalog/labels'

/** Chips: real categories plus two shortcuts ("Israel" = country IL, "Manual" = values you entered). */
type Chip = { key: string; label: string; match: (i: CatalogItem) => boolean }
const CHIPS: Chip[] = [
  ...CATEGORIES.map(([k, label]) => ({ key: k, label, match: (i: CatalogItem) => i.category === k })),
  { key: 'israel', label: 'Israel', match: (i) => i.country === 'IL' },
  { key: 'manual', label: 'Manual', match: (i) => i.provider === 'manual' || i.category === 'manual' },
]
const PROBLEM_STATES = ['late', 'failed', 'never']

export default function DataCatalogPage() {
  const route = useRoute()
  const qc = useQueryClient()
  const catalog = useCatalog()
  const [q, setQ] = useState(route.query.q ?? '')
  const [chip, setChip] = useState(route.query.category ?? '')
  const [country, setCountry] = useState(route.query.country ?? '')
  const [adding, setAdding] = useState(false)
  const problemsOnly = route.query.show === 'problems'

  const items = catalog.data?.items ?? []
  const chips = useMemo(() => CHIPS.filter((c) => items.some(c.match)), [items])
  const countries = useMemo(() => Array.from(new Set(items.map((i) => i.country).filter(Boolean) as string[])).sort((a, b) => countryName(a).localeCompare(countryName(b))), [items])

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase()
    const c = CHIPS.find((x) => x.key === chip)
    return items
      .filter((i) => !c || c.match(i))
      .filter((i) => !country || i.country === country)
      .filter((i) => !problemsOnly || PROBLEM_STATES.includes(freshnessOf(i).state))
      .filter(
        (i) =>
          !ql ||
          [i.name, i.series_id, i.description ?? '', categoryLabel(i.category), countryName(i.country), ...i.tags].some((s) => s.toLowerCase().includes(ql)),
      )
      .sort((a, b) => categoryRank(a.category) - categoryRank(b.category) || a.category.localeCompare(b.category) || a.name.localeCompare(b.name))
  }, [items, q, chip, country, problemsOnly])

  const checkAll = useMutation({
    mutationFn: () => api('/api/sys/jobs/series.refresh_stale/run', { method: 'POST' }),
    onSuccess: () => {
      toast('Checking every series for new data — this can take a minute', 'info')
      setTimeout(() => qc.invalidateQueries({ queryKey: ['catalog'] }), 20_000)
    },
    onError: (e) => toast(`Could not start the check: ${String((e as Error).message)}`, 'err'),
  })

  const grouped = !chip && !q.trim() && !problemsOnly
  const clearProblems = () => navigate('data', { ...route.query, show: undefined }, true)

  return (
    <Page
      title="Data catalog"
      sub="Every data series the terminal tracks. Charts, correlations, macro pages and alerts all read from here."
      actions={
        <button className="primary" onClick={() => setAdding(true)}>
          Add a data series
        </button>
      }
    >
      <Card
        flush
        title={catalog.data ? `${shown.length === items.length ? `${items.length} series` : `${shown.length} of ${items.length} series`}` : 'Series'}
        actions={
          <button onClick={() => checkAll.mutate()} disabled={checkAll.isPending} title="Ask every source for newer values now (normally done every 30 minutes)">
            Check for new data
          </button>
        }
        foot="Latest value shows the most recent period each series covers. Freshness compares that period with how often the source normally publishes."
      >
        <div className="stack" style={{ gap: 10, padding: '0 18px 12px' }}>
          <div className="row">
            <input style={{ flex: 1, minWidth: 240, maxWidth: 420 }} placeholder="Search by name, code or tag" value={q} onChange={(e) => setQ(e.target.value)} />
            <select value={country} onChange={(e) => setCountry(e.target.value)} aria-label="Country">
              <option value="">All countries</option>
              {countries.map((c) => (
                <option key={c} value={c}>
                  {countryName(c)}
                </option>
              ))}
            </select>
            {problemsOnly && (
              <span className="chip on" onClick={clearProblems} title="Show all series">
                Only series that need attention ✕
              </span>
            )}
          </div>
          <div className="row" style={{ gap: 6 }}>
            <span className={`chip ${!chip ? 'on' : ''}`} onClick={() => setChip('')}>
              All
            </span>
            {chips.map((c) => (
              <span key={c.key} className={`chip ${chip === c.key ? 'on' : ''}`} onClick={() => setChip(chip === c.key ? '' : c.key)}>
                {c.label}
              </span>
            ))}
          </div>
        </div>

        {catalog.isLoading && (
          <div style={{ padding: '0 18px 16px' }}>
            <Loading lines={6} />
          </div>
        )}
        {catalog.error && (
          <div style={{ padding: '0 18px 16px' }}>
            <ErrorBox error={catalog.error} what="the catalog" />
          </div>
        )}
        {catalog.data && shown.length === 0 && (
          <div style={{ padding: '0 18px 16px' }}>
            <Empty
              title={problemsOnly ? 'Nothing needs attention' : 'No series match'}
              actions={
                problemsOnly ? (
                  <button onClick={clearProblems}>Show all series</button>
                ) : (
                  <button
                    onClick={() => {
                      setQ('')
                      setChip('')
                      setCountry('')
                    }}
                  >
                    Clear filters
                  </button>
                )
              }
            >
              {problemsOnly ? 'Every series is up to date.' : 'Try a different search, or add the series you are looking for.'}
            </Empty>
          </div>
        )}
        {shown.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th style={{ paddingLeft: 18 }}>Name</th>
                <th>Country</th>
                <th>Frequency</th>
                <th className="r">Latest value</th>
                <th>Freshness</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((i, idx) => {
                const f = freshnessOf(i)
                const newGroup = grouped && (idx === 0 || shown[idx - 1].category !== i.category)
                return (
                  <Fragment key={i.series_id}>
                    {newGroup && (
                      <tr>
                        <td colSpan={5} className="small muted strong" style={{ paddingLeft: 18, paddingTop: 14, background: 'var(--bg)' }}>
                          {categoryLabel(i.category)}
                        </td>
                      </tr>
                    )}
                    <tr className="click" onClick={() => navigate(`series/${i.series_id}`)}>
                      <td style={{ paddingLeft: 18, maxWidth: 460 }}>
                        <a href={href(`series/${i.series_id}`)} onClick={(e) => e.stopPropagation()} className="strong" style={{ color: 'var(--text)' }} title={i.description || undefined}>
                          {i.name}
                        </a>
                        <div className="mono xs faint" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {i.series_id}
                        </div>
                      </td>
                      <td title={countryName(i.country)}>{i.country ? <Flag cc={i.country} /> : <span className="faint">—</span>}</td>
                      <td className="muted">{freqLabel(i.freq)}</td>
                      <td className="r">
                        <span className="num">{i.meta?.last_value !== undefined && i.meta?.last_value !== null ? formatValue(i.meta.last_value, i.unit) : '—'}</span>
                        {i.meta?.last_ts && <div className="xs faint">{periodLabel(i.meta.last_ts, i.freq)}</div>}
                      </td>
                      <td className={`small ${f.cls}`} title={f.title} style={{ maxWidth: 300 }}>
                        {f.text}
                      </td>
                    </tr>
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </Card>
      {adding && <AddSeriesDialog existing={items} onClose={() => setAdding(false)} />}
    </Page>
  )
}
