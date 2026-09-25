import { useMemo, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { num } from '../lib/format'
import { href } from '../lib/router'
import { toast } from '../lib/toast'
import { Card, Empty, ErrorBox, Loading, Page } from '../ui'
import Section from '../components/system/Section'
import { STATUS_WORD, lastOkOf, lastRunOf, lastWorkOf, scheduleText, type JobsResponse } from '../components/system/jobs'
import { freshnessOf, shortError, useCatalog, whenLabel, type CatalogItem } from '../components/catalog/labels'

type HealthRow = { id: string; name?: string; usable: boolean; reason: string; requires?: string[]; ok?: boolean; error?: string } & Record<string, unknown>
type Limiter = { provider: string; windows: Record<string, { used: number; limit: number }>; total_calls: number; total_wait_s: number }
type Quotas = { limiters: Limiter[]; cache: { hits: number; misses: number } }
type FetchRow = { ts: string; provider: string; op: string; key: string; status: string; duration_ms: number; rows: number; error: string | null }
type Llm = { month_spent_usd: number; budget_usd: number; by_purpose: { purpose: string; model: string; calls: number; cost_usd: number }[]; configured: boolean }
type Backup = { name: string; size_mb: number; created_at?: string; files?: number }

/** Internal "sources" that are not outside services. */
const INTERNAL = new Set(['manual', 'val', 'derived'])
/** Sources the terminal cannot do its core job without, and what they are for. */
const REQUIRED: Record<string, string> = { fred: 'macro data', edgar: 'company financials', finnhub: 'live US quotes and alerts', sec_filings: 'SEC filing news', finnhub_news: 'company news' }
const SHORT_NAME: Record<string, string> = {
  fred: 'FRED',
  boi: 'Bank of Israel',
  edgar: 'SEC EDGAR',
  hebcal: 'Hebcal holidays',
  finnhub: 'Finnhub quotes',
  yf: 'Yahoo Finance',
  imf: 'IMF',
  bis: 'BIS',
  ecb: 'ECB',
  estat: 'Eurostat',
  oecd: 'OECD',
  cbs: 'Israel CBS',
  ons: 'UK ONS',
  xbrl_org: 'European company filings (xbrl.org)',
  sec_filings: 'SEC filings feed',
  wires: 'Press-release wires',
  finnhub_news: 'Finnhub news',
  google_news: 'Google News',
  massive: 'Massive news',
  alphavantage: 'Alpha Vantage news',
  israel_rss: 'Israeli press feeds',
  gdelt: 'GDELT news',
  val: 'Valuation model',
  manual: 'Manual entry',
}
const OP: Record<string, string> = { get_quotes: 'Quotes', get_series: 'Data series', submissions: 'Filings list', describe: 'Look up', search: 'Search', get_ohlcv: 'Price history', companyfacts: 'Financial statements' }
const WINDOW: Record<string, string> = { per_second: 'per second', per_minute: 'per minute', per_hour: 'per hour', per_day: 'per day' }

const nameOf = (id: string, h?: HealthRow) => SHORT_NAME[id] ?? h?.name ?? id
const usd = (v: number) => `$${num(v, 2)}`

type Line = { tone: 'ok' | 'warn' | 'err' | 'muted'; text: ReactNode }
const DOT: Record<Line['tone'], string> = { ok: 'up', warn: 'warn-text', err: 'down', muted: 'faint' }
function Lines({ lines }: { lines: Line[] }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      {lines.map((l, i) => (
        <div key={i} className="row" style={{ alignItems: 'baseline', gap: 8, flexWrap: 'nowrap' }}>
          <span className={`${DOT[l.tone]} xs`}>●</span>
          <span className={l.tone === 'muted' ? 'muted small' : 'small'}>{l.text}</span>
        </div>
      ))}
    </div>
  )
}
function Headline({ children }: { children: ReactNode }) {
  return <div style={{ fontSize: 19, fontWeight: 600, marginBottom: 10 }}>{children}</div>
}

export default function StatusPage() {
  const catalog = useCatalog()
  const health = useQuery({ queryKey: ['sys', 'health'], queryFn: () => api<HealthRow[]>('/api/health/providers'), staleTime: 60_000 })
  const jobs = useQuery({ queryKey: ['sys', 'jobs'], queryFn: () => api<JobsResponse>('/api/sys/jobs'), refetchInterval: 30_000 })
  const backups = useQuery({ queryKey: ['sys', 'backups'], queryFn: () => api<Backup[]>('/api/sys/backups') })
  const llm = useQuery({ queryKey: ['sys', 'llm'], queryFn: () => api<Llm>('/api/sys/llm') })

  return (
    <Page title="Status" sub="Is everything working? The three cards answer that; the sections below have the details.">
      <div className="stack">
        <div className="grid-3">
          <SourcesCard health={health} items={catalog.data?.items} />
          <FreshnessCard catalog={catalog} />
          <TasksCard jobs={jobs} backups={backups.data} />
        </div>

        <div className="section-title" style={{ margin: '8px 0 0' }}>
          Details
        </div>
        <Section title="Scheduled tasks" hint="What runs in the background and when" summary={jobs.data ? `${jobs.data.scheduled.length} tasks` : undefined}>
          {() => <JobsDetail jobs={jobs} />}
        </Section>
        <Section title="Data sources" hint="Every source, its setup and last health check">
          {() => <SourcesDetail health={health} />}
        </Section>
        <Section title="API usage and rate limits" hint="Calls made since the terminal started">
          {() => <QuotasDetail />}
        </Section>
        <Section title="Recent fetch log" hint="The last 100 requests to outside sources">
          {() => <FetchLogDetail />}
        </Section>
        <Section title="AI (Claude) spend" summary={llm.data ? (llm.data.configured ? `${usd(llm.data.month_spent_usd)} of ${usd(llm.data.budget_usd)} this month` : 'Not set up (optional)') : undefined}>
          {() => <LlmDetail llm={llm} />}
        </Section>
        <Section title="Backups" summary={backups.data?.[0] ? `Latest: ${backups.data[0].created_at ? whenLabel(backups.data[0].created_at) : backups.data[0].name}` : undefined}>
          {() => <BackupsDetail backups={backups} />}
        </Section>
      </div>
    </Page>
  )
}

// ------------------------------------------------------------------ top cards
function SourcesCard({ health, items }: { health: ReturnType<typeof useQuery<HealthRow[]>>; items?: CatalogItem[] }) {
  const body = useMemo(() => {
    if (!health.data) return null
    const rows = health.data.filter((h) => !INTERNAL.has(h.id))
    const failingSeries: Record<string, { n: number; err: string | null }> = {}
    for (const i of items ?? []) {
      if (freshnessOf(i).state !== 'failed' || INTERNAL.has(i.provider)) continue
      const f = (failingSeries[i.provider] ??= { n: 0, err: i.meta?.last_error ?? null })
      f.n++
    }
    const lines: Line[] = []
    const optionalMissing: string[] = []
    const switchedOff: string[] = []
    for (const h of rows) {
      const name = nameOf(h.id, h)
      if (!h.usable) {
        if (/grey/i.test(h.reason)) switchedOff.push(name)
        else if (REQUIRED[h.id]) lines.push({ tone: 'warn', text: <>{name} — not set up, needed for {REQUIRED[h.id]}. <a href={href('settings')}>Add the key</a></> })
        else optionalMissing.push(name)
      } else if (h.ok === false) {
        lines.push({ tone: 'err', text: <span title={String(h.error ?? '')}>{name} — failing: {shortError(String(h.error ?? h.reason))}</span> })
      }
    }
    for (const [pid, f] of Object.entries(failingSeries)) {
      const h = rows.find((r) => r.id === pid)
      if (h?.ok === false) continue
      lines.push({ tone: 'err', text: <span title={f.err ?? ''}>{nameOf(pid, h)} — {f.n} series won’t load: {shortError(f.err)}</span> })
    }
    if (optionalMissing.length) lines.push({ tone: 'muted', text: <>Not set up (optional): {optionalMissing.join(', ')}</> })
    if (switchedOff.length) lines.push({ tone: 'muted', text: <>Switched off (unofficial sources): {switchedOff.join(', ')}</> })
    const setUp = rows.filter((h) => h.usable)
    const working = setUp.filter((h) => h.ok !== false && !failingSeries[h.id]).length
    return { lines, working, setUp: setUp.length }
  }, [health.data, items])

  return (
    <Card title="Data sources" foot="Checked when this page opened.">
      {health.isLoading && <Loading lines={3} />}
      {health.error && <ErrorBox error={health.error} what="source health" />}
      {body && (
        <>
          <Headline>
            {body.working === body.setUp ? `All ${body.setUp} sources working` : `${body.working} of ${body.setUp} sources working`}
          </Headline>
          {body.lines.length ? <Lines lines={body.lines} /> : <div className="small muted">No problems.</div>}
        </>
      )}
    </Card>
  )
}

function FreshnessCard({ catalog }: { catalog: ReturnType<typeof useCatalog> }) {
  const c = useMemo(() => {
    const out = { ok: 0, late: 0, failed: 0, never: 0, paused: 0 }
    for (const i of catalog.data?.items ?? []) out[freshnessOf(i).state]++
    return out
  }, [catalog.data])
  const total = catalog.data?.items.length ?? 0
  const problems = c.late + c.failed + c.never
  const lines: Line[] = []
  if (c.late) lines.push({ tone: 'warn', text: `${c.late} late — a newer value was expected by now` })
  if (c.failed) lines.push({ tone: 'err', text: `${c.failed} failed to load on the last try` })
  if (c.never) lines.push({ tone: 'muted', text: `${c.never} never loaded` })
  if (c.paused) lines.push({ tone: 'muted', text: `${c.paused} switched off` })
  return (
    <Card title="Data freshness" foot={problems ? <a href={href('data', { show: 'problems' })}>Show the {problems} series that need attention</a> : 'Compared with how often each source normally publishes.'}>
      {catalog.isLoading && <Loading lines={3} />}
      {catalog.error && <ErrorBox error={catalog.error} what="the catalog" />}
      {catalog.data && (
        <>
          <Headline>
            {c.ok} of {total} series up to date
          </Headline>
          {lines.length ? <Lines lines={lines} /> : <div className="small muted">Everything is current.</div>}
        </>
      )}
    </Card>
  )
}

function TasksCard({ jobs, backups }: { jobs: ReturnType<typeof useQuery<JobsResponse>>; backups?: Backup[] }) {
  const lines = useMemo((): Line[] | null => {
    const j = jobs.data
    if (!j) return null
    const has = (id: string) => j.scheduled.some((s) => s.id === id)
    const out: Line[] = []
    if (has('market.quotes_poll')) {
      const work = lastWorkOf(j, 'market.quotes_poll')
      const run = lastRunOf(j, 'market.quotes_poll')
      const idle = run?.status === 'ok' && !run.message
      out.push({ tone: work ? 'ok' : 'muted', text: <>{work ? `Prices updated ${whenLabel(work)}` : 'Prices not updated yet'}{idle ? <span className="faint"> · markets closed, checking every 15 min</span> : null}</> })
    }
    const macro = ['series.refresh_stale', 'series.refresh_daily_full'].filter(has)
    if (macro.length) {
      const oks = macro.map((id) => lastOkOf(j, id))
      const ok = oks.filter(Boolean).sort().at(-1)
      if (ok) out.push({ tone: 'ok', text: `Macro data checked for new values ${whenLabel(ok)}` })
      else if (oks.every((o) => o === null)) out.push({ tone: 'muted', text: 'Macro data not checked yet' })
    }
    if (has('maintenance.backup') || backups?.length) {
      const ok = lastOkOf(j, 'maintenance.backup') ?? backups?.[0]?.created_at
      const next = j.scheduled.find((s) => s.id === 'maintenance.backup')?.next_run
      if (ok) out.push({ tone: 'ok', text: `Backup made ${whenLabel(ok)}` })
      else if (backups?.length) out.push({ tone: 'ok', text: `Latest backup: ${backups[0].name}` })
      else out.push({ tone: 'muted', text: `No backup yet${next ? ` — next one ${whenLabel(next)}` : ''}` })
    }
    if (has('valuation.daily')) {
      const ok = lastOkOf(j, 'valuation.daily')
      if (ok) out.push({ tone: 'ok', text: `Valuations recalculated ${whenLabel(ok)}` })
    }
    for (const s of j.scheduled) {
      const r = lastRunOf(j, s.id)
      if (r?.status === 'error') out.push({ tone: 'err', text: <span title={r.message ?? ''}>{s.name} failed {whenLabel(r.started_at)}: {shortError(r.message)}</span> })
    }
    return out
  }, [jobs.data, backups])

  return (
    <Card title="Background tasks" foot="Times are Israel time. When the laptop wakes from sleep, out-of-date data is refreshed right away.">
      {jobs.isLoading && <Loading lines={3} />}
      {jobs.error && <ErrorBox error={jobs.error} what="background tasks" />}
      {jobs.data && jobs.data.scheduled.length === 0 && (
        <div className="notice warn">
          <span>⚠</span>
          <div className="grow">Background tasks are switched off, so data only updates when you press “Refresh now” on a page.</div>
        </div>
      )}
      {jobs.data && jobs.data.scheduled.length > 0 && lines && (
        <>
          <Headline>{lines.some((l) => l.tone === 'err') ? 'Some tasks failed' : 'Running normally'}</Headline>
          <Lines lines={lines} />
        </>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------ detail sections
function JobsDetail({ jobs }: { jobs: ReturnType<typeof useQuery<JobsResponse>> }) {
  const qc = useQueryClient()
  const run = useMutation({
    mutationFn: (id: string) => api(`/api/sys/jobs/${encodeURIComponent(id)}/run`, { method: 'POST' }),
    onSuccess: (_d, id) => {
      toast(`Started “${jobs.data?.scheduled.find((s) => s.id === id)?.name ?? id}”`, 'ok')
      setTimeout(() => qc.invalidateQueries({ queryKey: ['sys', 'jobs'] }), 2500)
    },
    onError: (e) => toast(`Could not start: ${String((e as Error).message)}`, 'err'),
  })
  if (!jobs.data) return jobs.error ? <ErrorBox error={jobs.error} what="tasks" /> : <Loading />
  const names = Object.fromEntries(jobs.data.scheduled.map((s) => [s.id, s.name]))
  return (
    <div className="stack" style={{ gap: 14 }}>
      {jobs.data.scheduled.length === 0 ? (
        <Empty title="No scheduled tasks">The scheduler is switched off in the configuration.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Task</th>
                <th>How often</th>
                <th>Last run</th>
                <th>Next run</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.data.scheduled.map((s) => {
                const r = lastRunOf(jobs.data!, s.id)
                const [word, cls] = STATUS_WORD[s.running ? 'running' : (r?.status ?? '')] ?? [r?.status ?? '', '']
                return (
                  <tr key={s.id}>
                    <td title={s.id}>{s.name}</td>
                    <td className="muted">{scheduleText(s.trigger)}</td>
                    <td>
                      {r ? (
                        <>
                          <span className={`badge ${cls}`}>{word}</span> <span className="small">{whenLabel(r.started_at)}</span>
                          {r.message && (
                            <div className="xs faint" title={r.message} style={{ maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {r.message}
                            </div>
                          )}
                        </>
                      ) : (
                        <span className="faint small">Not run yet</span>
                      )}
                    </td>
                    <td className="small muted">{s.next_run ? whenLabel(s.next_run) : '—'}</td>
                    <td className="r">
                      <button disabled={s.running || run.isPending} onClick={() => run.mutate(s.id)}>
                        Run now
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {jobs.data.runs.length > 0 && (
        <>
          <div className="section-title" style={{ margin: 0 }}>
            Recent runs
          </div>
          <div className="table-wrap" style={{ maxHeight: 320 }}>
            <table className="table compact">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Started</th>
                  <th>Result</th>
                  <th className="r">Took</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {jobs.data.runs.map((r) => {
                  const [word, cls] = STATUS_WORD[r.status] ?? [r.status, '']
                  return (
                    <tr key={r.id}>
                      <td title={r.job_id}>{names[r.job_id] ?? r.job_id}</td>
                      <td className="small muted">{whenLabel(r.started_at)}</td>
                      <td>
                        <span className={`badge ${cls}`}>{word}</span>
                      </td>
                      <td className="r num small">{r.duration_s !== null ? `${num(r.duration_s, 1)} s` : '—'}</td>
                      <td className="xs faint" title={r.message ?? ''} style={{ maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.message}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function SourcesDetail({ health }: { health: ReturnType<typeof useQuery<HealthRow[]>> }) {
  if (!health.data) return health.error ? <ErrorBox error={health.error} what="sources" /> : <Loading />
  const extra = (h: HealthRow) =>
    Object.entries(h)
      .filter(([k]) => !['id', 'name', 'usable', 'reason', 'ok', 'error', 'requires'].includes(k))
      .map(([k, v]) => `${k}: ${String(v)}`)
      .join(' · ')
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Source</th>
            <th>Status</th>
            <th>Needs</th>
            <th>Health check</th>
          </tr>
        </thead>
        <tbody>
          {health.data.map((h) => {
            const status = !h.usable ? (/grey/i.test(h.reason) ? ['Switched off', ''] : [REQUIRED[h.id] ? 'Not set up — required' : 'Not set up (optional)', REQUIRED[h.id] ? 'warn' : '']) : h.ok === false ? ['Failing', 'err'] : ['Working', 'ok']
            return (
              <tr key={h.id}>
                <td>
                  {nameOf(h.id, h)}
                  <div className="mono xs faint">{h.id}</div>
                </td>
                <td>
                  <span className={`badge ${status[1]}`}>{status[0]}</span>
                </td>
                <td className="small muted">{h.requires?.length ? h.requires.join(', ') : 'Nothing'}</td>
                <td className="xs faint" style={{ whiteSpace: 'normal', maxWidth: 420 }}>
                  {h.ok === false ? <span className="down">{String(h.error ?? h.reason)}</span> : extra(h)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function QuotasDetail() {
  const q = useQuery({ queryKey: ['sys', 'quotas'], queryFn: () => api<Quotas>('/api/sys/quotas'), refetchInterval: 10_000 })
  if (!q.data) return q.error ? <ErrorBox error={q.error} what="usage" /> : <Loading />
  return (
    <div className="stack" style={{ gap: 8 }}>
      {q.data.limiters.length === 0 ? (
        <Empty title="No calls yet">Rate-limited sources appear here after their first request.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Current use vs limit</th>
                <th className="r">Calls since start</th>
                <th className="r">Time spent waiting</th>
              </tr>
            </thead>
            <tbody>
              {q.data.limiters.map((l) => (
                <tr key={l.provider}>
                  <td>{nameOf(l.provider)}</td>
                  <td className="small">
                    {Object.entries(l.windows)
                      .map(([k, w]) => `${num(w.used, 0)} of ${num(w.limit, w.limit < 1 ? 1 : 0)} ${WINDOW[k] ?? k}`)
                      .join(' · ')}
                  </td>
                  <td className="r num">{num(l.total_calls, 0)}</td>
                  <td className="r num">{num(l.total_wait_s, 1)} s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="small faint">
        Response cache since start: {num(q.data.cache.hits, 0)} hits, {num(q.data.cache.misses, 0)} misses.
      </div>
    </div>
  )
}

function FetchLogDetail() {
  const q = useQuery({ queryKey: ['sys', 'fetch'], queryFn: () => api<FetchRow[]>('/api/sys/fetch-log'), refetchInterval: 15_000 })
  if (!q.data) return q.error ? <ErrorBox error={q.error} what="the fetch log" /> : <Loading />
  if (!q.data.length) return <Empty title="No requests yet" />
  return (
    <div className="table-wrap" style={{ maxHeight: 420 }}>
      <table className="table compact">
        <thead>
          <tr>
            <th>When</th>
            <th>Source</th>
            <th>Request</th>
            <th>Result</th>
            <th className="r">Took</th>
            <th className="r">Rows</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((r, i) => (
            <tr key={i}>
              <td className="small muted nowrap">{whenLabel(r.ts)}</td>
              <td>{nameOf(r.provider)}</td>
              <td>
                {OP[r.op] ?? r.op}
                <div className="mono xs faint" title={r.key} style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.key}
                </div>
              </td>
              <td title={r.error ?? ''}>
                {r.status === 'ok' ? <span className="badge ok">OK</span> : <span className="badge err">Failed</span>}
                {r.error && <div className="xs down">{shortError(r.error)}</div>}
              </td>
              <td className="r num small">{num(r.duration_ms / 1000, 1)} s</td>
              <td className="r num small">{num(r.rows, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LlmDetail({ llm }: { llm: ReturnType<typeof useQuery<Llm>> }) {
  if (!llm.data) return llm.error ? <ErrorBox error={llm.error} what="AI usage" /> : <Loading />
  const d = llm.data
  const share = d.budget_usd > 0 ? Math.min(1, d.month_spent_usd / d.budget_usd) : 0
  return (
    <div className="stack" style={{ gap: 12 }}>
      {!d.configured && (
        <div className="small muted">
          Claude is not set up. It is optional — with a key, the terminal can read Israeli PDF financial statements, summarise important news and write the morning brief. <a href={href('settings')}>Add a key in Settings</a>
        </div>
      )}
      <div>
        <div className="row between small">
          <span>
            Spent this month: <b className="num">{usd(d.month_spent_usd)}</b> of <span className="num">{usd(d.budget_usd)}</span> budget
          </span>
          <span className="muted">{Math.round(share * 100)}%</span>
        </div>
        <div style={{ height: 6, background: 'var(--surface-3)', borderRadius: 3, marginTop: 6, overflow: 'hidden' }}>
          <div style={{ width: `${share * 100}%`, height: '100%', background: share >= 0.9 ? 'var(--down)' : 'var(--accent)' }} />
        </div>
        <div className="xs faint" style={{ marginTop: 4 }}>
          AI features pause automatically when the monthly budget is used up.
        </div>
      </div>
      {d.by_purpose.length > 0 && (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Used for</th>
                <th>Model</th>
                <th className="r">Calls</th>
                <th className="r">Cost</th>
              </tr>
            </thead>
            <tbody>
              {d.by_purpose.map((r, i) => (
                <tr key={i}>
                  <td>{r.purpose}</td>
                  <td className="mono small muted">{r.model}</td>
                  <td className="r num">{num(r.calls, 0)}</td>
                  <td className="r num">{usd(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function BackupsDetail({ backups }: { backups: ReturnType<typeof useQuery<Backup[]>> }) {
  const qc = useQueryClient()
  const run = useMutation({
    mutationFn: () => api('/api/sys/backups/run', { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sys', 'backups'] })
      toast('Backup made', 'ok')
    },
    onError: (e) => toast(`Backup failed: ${String((e as Error).message)}`, 'err'),
  })
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row between">
        <span className="small muted">A copy of the database and stored data is made every night; the last 7 are kept in the data folder.</span>
        <button className="primary" disabled={run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? 'Backing up…' : 'Back up now'}
        </button>
      </div>
      {!backups.data ? (
        backups.error ? <ErrorBox error={backups.error} what="backups" /> : <Loading />
      ) : backups.data.length === 0 ? (
        <Empty title="No backups yet">The first one is made tonight, or press “Back up now”.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table compact">
            <thead>
              <tr>
                <th>Backup</th>
                <th>Made</th>
                <th className="r">Size</th>
              </tr>
            </thead>
            <tbody>
              {backups.data.map((b) => (
                <tr key={b.name}>
                  <td className="mono small">{b.name}</td>
                  <td className="small muted">{b.created_at ? whenLabel(b.created_at) : '—'}</td>
                  <td className="r num">{num(b.size_mb, 1)} MB</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
