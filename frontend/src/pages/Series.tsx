import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Observations, type SeriesSpec } from '../lib/api'
import { ago, num, pct } from '../lib/format'
import { href, navigate } from '../lib/router'
import { toast } from '../lib/toast'
import { Card, Empty, ErrorBox, Help, Loading, Page, Segmented, Stat } from '../ui'
import SeriesLineChart, { toLinePoints } from '../components/charts/SeriesLineChart'
import {
  categoryLabel,
  chartPrecision,
  countryName,
  defaultShowAs,
  formatValue,
  formulaText,
  freqLabel,
  freshnessOf,
  periodLabel,
  shortError,
  showAsOptions,
  sourceLabel,
  unitInfo,
  unitLabel,
  useCatalog,
  useProviders,
  type CatalogItem,
  type SeriesMeta,
  type ShowAs,
} from '../components/catalog/labels'

type Obs = Observations & { meta?: SeriesMeta | null; spec?: SeriesSpec }
type Range = '1Y' | '5Y' | '10Y' | 'Max'
const YEARS: Record<Range, number> = { '1Y': 1, '5Y': 5, '10Y': 10, Max: 0 }
const GROWTH_KINDS = ['price', 'level_index', 'stock', 'flow', 'count']

const obsUrl = (id: string, transform?: string) => `/api/series/${encodeURIComponent(id)}/observations${transform && transform !== 'level' ? `?transform=${transform}` : ''}`
const is404 = (e: unknown) => String((e as Error)?.message ?? e).startsWith('404')
const t = (s: string) => new Date(/[zZ]$/.test(s) ? s : `${s}Z`).getTime()

export default function SeriesPage({ seriesId }: { seriesId: string }) {
  const qc = useQueryClient()
  const catalog = useCatalog()
  const providers = useProviders()
  const level = useQuery({ queryKey: ['obs', seriesId, 'level'], queryFn: () => api<Obs>(obsUrl(seriesId)), retry: (n, e) => !is404(e) && n < 2 })

  const item: CatalogItem | undefined = useMemo(() => {
    const fromCat = catalog.data?.items.find((i) => i.series_id === seriesId)
    const spec = level.data?.spec
    if (!fromCat && !spec) return undefined
    return { ...(spec ?? fromCat!), ...(fromCat ?? {}), meta: level.data?.meta ?? fromCat?.meta } as CatalogItem
  }, [catalog.data, level.data, seriesId])

  const options = useMemo(() => (item ? showAsOptions(item) : []), [item])
  const hasYoy = options.some((o) => o.key === 'yoy')
  const yoy = useYoy(level.data, hasYoy)
  const [showAsPick, setShowAs] = useState<ShowAs | null>(null)
  const showAs: ShowAs = showAsPick ?? (item ? defaultShowAs(item) : 'level')
  const opt = options.find((o) => o.key === showAs) ?? options[0]
  const [range, setRange] = useState<Range>('5Y')

  const transformed = useQuery({
    queryKey: ['obs', seriesId, opt?.transform],
    queryFn: () => api<Obs>(obsUrl(seriesId, opt!.transform)),
    enabled: !!opt && opt.transform !== 'level',
  })
  const shown = opt && opt.transform !== 'level' ? transformed : level

  const points = useMemo(() => {
    const d = shown.data
    if (!d || !d.n) return []
    const all = toLinePoints(d.ts, d.value)
    const yrs = YEARS[range]
    if (!yrs || !all.length) return all
    const from = all[all.length - 1].time - yrs * 365.25 * 86400
    return all.filter((p) => p.time >= from)
  }, [shown.data, range])

  const refresh = useMutation({
    mutationFn: (full: boolean) => api<{ status: string; error?: string | null }>(`/api/series/${encodeURIComponent(seriesId)}/refresh?full=${full}`, { method: 'POST' }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['obs', seriesId] })
      qc.invalidateQueries({ queryKey: ['catalog'] })
      if (r.status === 'ok') toast('Refreshed from the source', 'ok')
      else if (r.status === 'skipped') toast('This series is switched off, so it was not refreshed', 'info')
      else toast(`Refresh failed: ${shortError(r.error)}`, 'err')
    },
    onError: (e) => toast(`Refresh failed: ${String((e as Error).message)}`, 'err'),
  })

  const remove = useMutation({
    mutationFn: () => api(`/api/catalog/${encodeURIComponent(seriesId)}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['catalog'] })
      toast(`Removed “${item?.name ?? seriesId}” from the catalog`, 'ok')
      navigate('data')
    },
    onError: (e) => toast(`Could not remove: ${String((e as Error).message)}`, 'err'),
  })

  const switchOn = useMutation({
    mutationFn: async () => {
      if (!item) return
      const { meta: _m, ...spec } = item
      void _m
      await api(`/api/catalog/${encodeURIComponent(seriesId)}?refresh=true`, { method: 'PUT', json: { ...spec, enabled: true } })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['obs', seriesId] })
      qc.invalidateQueries({ queryKey: ['catalog'] })
      toast('Switched on and loaded', 'ok')
    },
    onError: (e) => toast(`Could not switch on: ${String((e as Error).message)}`, 'err'),
  })

  // ---- not found / loading
  if (level.error && is404(level.error)) {
    return (
      <Page title="Series not found">
        <Empty title="This series is not in the catalog" actions={<a href={href('data')}>Open the data catalog</a>}>
          There is no series with the code <span className="mono">{seriesId}</span>. It may have been removed.
        </Empty>
      </Page>
    )
  }
  if (!item) {
    return (
      <Page title="Data series" sub={<span className="mono">{seriesId}</span>}>
        {level.error ? <ErrorBox error={level.error} what="this series" /> : <Loading lines={4} />}
      </Page>
    )
  }

  const fresh = freshnessOf(item)
  const source = sourceLabel(item.provider, providers.data)
  const data = level.data
  const n = data?.n ?? 0
  const last = n ? data!.value[n - 1] : null
  const lastTs = n ? data!.ts[n - 1] : null
  const prev = n > 1 ? data!.value[n - 2] : null
  const prevTs = n > 1 ? data!.ts[n - 2] : null
  const u = unitInfo(item.unit)
  const isManual = item.provider === 'manual'

  return (
    <Page
      title={item.name}
      sub={
        <div className="stack" style={{ gap: 2 }}>
          <span className="mono small faint">{item.series_id}</span>
          <span>
            {freqLabel(item.freq)}
            {unitLabel(item.unit) ? ` · ${unitLabel(item.unit)}` : ''}
            {item.country ? ` · ${countryName(item.country)}` : ''} · Source: {source}
          </span>
        </div>
      }
    >
      <div className="stack">
        {n > 0 && (
          <div className="stats">
            <Stat label="Latest value" value={<span className="num">{formatValue(last, item.unit)}</span>} sub={periodLabel(lastTs, item.freq)} />
            {u.kind !== 'flag' && prev !== null && (
              <Stat label={['1d', 'irregular'].includes(item.freq) ? 'Change since previous value' : `Change vs previous ${freqNoun(item.freq)}`} value={<ChangeValue last={last!} prev={prev} spec={item} />} sub={`from ${formatValue(prev, item.unit)} in ${periodLabel(prevTs, item.freq)}`} />
            )}
            {hasYoy && yoy && <Stat label="Year-over-year" value={<span className={`num ${yoy.v > 0 ? 'up' : yoy.v < 0 ? 'down' : ''}`}>{pct(yoy.v, 1)}</span>} sub={`vs ${periodLabel(yoy.ts, item.freq)}`} help="Percent change compared with the same period one year earlier." />}
            <Stat label="Freshness" value={<span className={fresh.cls === 'muted' ? '' : fresh.cls} style={{ fontSize: 15 }}>{fresh.text}</span>} sub={item.meta?.fetched_at ? `Checked with the source ${ago(item.meta.fetched_at)}` : 'Not checked yet'} />
          </div>
        )}

        {level.isLoading && <Loading lines={5} />}
        {level.error && <ErrorBox error={level.error} what="this series" />}

        {data && n === 0 && (
          <EmptySeries
            item={item}
            source={source}
            busy={refresh.isPending || switchOn.isPending}
            onLoad={() => refresh.mutate(true)}
            onSwitchOn={() => switchOn.mutate()}
          />
        )}

        {n > 0 && opt && (
          <Card
            title={opt.label}
            hint={opt.explain}
            actions={
              <>
                {options.length > 1 && <Segmented<ShowAs> value={opt.key} onChange={setShowAs} options={options.map((o) => [o.key, o.label] as [ShowAs, string])} />}
                <Segmented<Range> value={range} onChange={setRange} options={['1Y', '5Y', '10Y', 'Max']} />
              </>
            }
            foot={
              <div className="row between" style={{ alignItems: 'flex-start' }}>
                <div className="grow" style={{ flex: 1, minWidth: 240 }}>
                  {data?.attribution ?? `Source: ${source}`}
                </div>
                <div className="row">
                  {!isManual && (
                    <button onClick={() => refresh.mutate(item.meta?.last_status === 'error')} disabled={refresh.isPending} title="Ask the source for newer values now">
                      {refresh.isPending ? 'Refreshing…' : 'Refresh now'}
                    </button>
                  )}
                  <button onClick={() => (window.location.href = `/api/series/${encodeURIComponent(seriesId)}/export.csv${opt.transform !== 'level' ? `?transform=${opt.transform}` : ''}`)}>Download CSV</button>
                </div>
              </div>
            }
          >
            {shown.isLoading ? (
              <Loading lines={5} />
            ) : shown.error ? (
              <ErrorBox error={shown.error} what="this view" />
            ) : points.length < 2 ? (
              <Empty title="Not enough data for this view">Try a longer range or a different “show as” option.</Empty>
            ) : (
              <SeriesLineChart key={`${opt.key}-${range}`} data={points} height={340} precision={viewPrecision(opt.transform, item.unit, points.at(-1)?.value)} />
            )}
            {fresh.state === 'late' && (
              <div className="notice warn" style={{ marginTop: 10 }}>
                <span>⚠</span>
                <div className="grow">
                  {isManual
                    ? `The latest value you entered is for ${periodLabel(lastTs, item.freq)}. Add newer values below.`
                    : `The latest value is for ${periodLabel(lastTs, item.freq)}. A newer one would normally be out by now — the source may have stopped publishing this series.`}
                </div>
              </div>
            )}
            {fresh.state === 'failed' && (
              <div className="notice err" style={{ marginTop: 10 }} title={item.meta?.last_error ?? undefined}>
                <span>⚠</span>
                <div className="grow">The last update failed ({shortError(item.meta?.last_error)}). The chart shows the values stored before that.</div>
              </div>
            )}
          </Card>
        )}

        <AboutCard item={item} items={catalog.data?.items ?? []} source={source} onRemove={() => confirm(`Remove “${item.name}” from the catalog? Charts and alerts that use it will stop working.`) && remove.mutate()} />
        {isManual && <ManualValues seriesId={seriesId} freq={item.freq} />}
      </div>
    </Page>
  )
}

function freqNoun(f: string) {
  return { '1d': 'day', '1w': 'week', '1mo': 'month', '1q': 'quarter', '1y': 'year' }[f] ?? 'value'
}

function viewPrecision(transform: string, unit: string, sample: number | undefined): number {
  if (transform === 'diff_bp') return 1
  if (transform === 'pct' || transform === 'yoy') return 2
  return chartPrecision(unit, sample)
}

/** Change since the previous observation, in the unit that reads naturally for the series. */
function ChangeValue({ last, prev, spec }: { last: number; prev: number; spec: CatalogItem }) {
  const u = unitInfo(spec.unit)
  const d = last - prev
  const cls = d > 0 ? 'up' : d < 0 ? 'down' : 'muted'
  const sign = d > 0 ? '+' : ''
  if (u.kind === 'pct' && ['yield', 'spread'].includes(spec.value_kind))
    return (
      <span className={`num ${cls}`}>
        {sign}
        {num(d * 100, 0)} bp <Help text="Basis points: 1 bp = 0.01 percentage point." />
      </span>
    )
  if (u.kind === 'pct' || u.kind === 'fraction')
    return (
      <span className={`num ${cls}`}>
        {sign}
        {num(u.kind === 'fraction' ? d * 100 : d, 2)} pp <Help text="Percentage points: the difference between two percentages." />
      </span>
    )
  if (u.kind === 'bp')
    return (
      <span className={`num ${cls}`}>
        {sign}
        {num(d, 0)} bp
      </span>
    )
  if (GROWTH_KINDS.includes(spec.value_kind) && prev !== 0) return <span className={`num ${cls}`}>{pct((last / prev - 1) * 100, 2)}</span>
  return (
    <span className={`num ${cls}`}>
      {sign}
      {num(d, 2)}
    </span>
  )
}

function useYoy(data: Obs | undefined, enabled: boolean): { v: number; ts: string } | null {
  return useMemo(() => {
    if (!enabled || !data || data.n < 2) return null
    const lastT = t(data.ts[data.n - 1])
    const target = lastT - 365 * 86400000
    for (let i = data.n - 2; i >= 0; i--) {
      const ti = t(data.ts[i])
      if (ti <= target + 7 * 86400000) {
        if (target - ti > 40 * 86400000 || !data.value[i]) return null // no value close to a year ago
        return { v: (data.value[data.n - 1] / data.value[i] - 1) * 100, ts: data.ts[i] }
      }
    }
    return null
  }, [data, enabled])
}

function EmptySeries({ item, source, busy, onLoad, onSwitchOn }: { item: CatalogItem; source: string; busy: boolean; onLoad: () => void; onSwitchOn: () => void }) {
  const f = freshnessOf(item)
  if (item.provider === 'manual')
    return (
      <Empty title="No values entered yet">
        This series holds numbers you type in yourself. Add them below under “Enter values”.
      </Empty>
    )
  if (f.state === 'paused')
    return (
      <Empty title="This series is switched off" actions={<button className="primary" disabled={busy} onClick={onSwitchOn}>{busy ? 'Loading…' : 'Switch on and load data'}</button>}>
        It is not refreshed automatically, so there is nothing to show yet.
      </Empty>
    )
  if (f.state === 'failed')
    return (
      <Empty title="This series could not be loaded" actions={<button className="primary" disabled={busy} onClick={onLoad}>{busy ? 'Trying…' : 'Try again'}</button>}>
        <div>
          The last attempt to load it from {source} failed: <b>{shortError(item.meta?.last_error)}</b>.
        </div>
        {item.meta?.last_error && (
          <div className="xs faint mono" style={{ marginTop: 6, wordBreak: 'break-all' }}>
            {item.meta.last_error.split('\n')[0]}
          </div>
        )}
      </Empty>
    )
  return (
    <Empty title="No data loaded yet" actions={<button className="primary" disabled={busy} onClick={onLoad}>{busy ? 'Loading…' : 'Load data'}</button>}>
      The terminal has not fetched this series from {source} yet.
    </Empty>
  )
}

function AboutCard({ item, items, source, onRemove }: { item: CatalogItem; items: CatalogItem[]; source: string; onRemove: () => void }) {
  const m = item.meta ?? {}
  return (
    <Card
      title="About this series"
      actions={
        <button className="ghost danger" onClick={onRemove}>
          Remove from catalog
        </button>
      }
    >
      <dl className="kv">
        {item.description ? (
          <>
            <dt>Description</dt>
            <dd style={{ whiteSpace: 'pre-line', maxWidth: 820 }}>{item.description}</dd>
          </>
        ) : null}
        {item.formula && (
          <>
            <dt>How it’s calculated</dt>
            <dd>
              {formulaText(item.formula, items)}
              <div className="mono xs faint">{item.formula}</div>
            </dd>
          </>
        )}
        <dt>Category</dt>
        <dd>{categoryLabel(item.category)}</dd>
        <dt>Data available</dt>
        <dd>{m.n_obs ? `${m.n_obs.toLocaleString('en-US')} values, ${periodLabel(m.first_ts, item.freq)} to ${periodLabel(m.last_ts, item.freq)}` : 'None yet'}</dd>
        <dt>Seasonally adjusted</dt>
        <dd>{item.sa ? 'Yes' : 'No'}</dd>
        <dt>Source</dt>
        <dd>
          {source}
          {item.license_note ? <div className="small faint">{item.license_note}</div> : null}
        </dd>
      </dl>
    </Card>
  )
}

function ManualValues({ seriesId, freq }: { seriesId: string; freq: string }) {
  const qc = useQueryClient()
  const [csv, setCsv] = useState('')
  const [replace, setReplace] = useState(false)
  const save = useMutation({
    mutationFn: () => api<{ rows_added: number; n: number }>(`/api/catalog/${encodeURIComponent(seriesId)}/manual`, { method: 'POST', json: { csv, replace } }),
    onSuccess: (r) => {
      setCsv('')
      qc.invalidateQueries({ queryKey: ['obs', seriesId] })
      qc.invalidateQueries({ queryKey: ['catalog'] })
      toast(`Saved ${r.rows_added} value${r.rows_added === 1 ? '' : 's'} — the series now has ${r.n}`, 'ok')
    },
    onError: (e) => toast(String((e as Error).message).includes('no valid') ? 'No lines could be read. Use one “date, value” pair per line, e.g. 2026-08, 48.7' : `Could not save: ${String((e as Error).message)}`, 'err'),
  })
  return (
    <Card title="Enter values" hint={`One per line: date, value (e.g. ${freq === '1mo' ? '2026-08, 48.7' : '2026-08-15, 48.7'}). Pasting from a spreadsheet works too.`}>
      <div className="stack" style={{ gap: 10 }}>
        <textarea rows={5} className="mono" value={csv} onChange={(e) => setCsv(e.target.value)} placeholder={'2026-07,49.1\n2026-08,48.7'} />
        <div className="row">
          <label className="check">
            <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> Replace all existing values (otherwise new dates are added and matching dates are updated)
          </label>
          <span className="spacer" />
          <button className="primary" disabled={!csv.trim() || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save values'}
          </button>
        </div>
      </div>
    </Card>
  )
}
