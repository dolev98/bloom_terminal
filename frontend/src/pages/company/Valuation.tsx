import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { dateLabel, dateTimeIL, pct } from '../../lib/format'
import { href, navigate } from '../../lib/router'
import { toast } from '../../lib/toast'
import { useQuote } from '../../lib/ws'
import { quoteFreshness } from '../../lib/hooks'
import { Card, Empty, ErrorBox, Loading, Stat } from '../../ui'
import { Disclosure, currencySymbol } from '../../components/financials/shared'
import { NON_COMPANY, type CoverageRow, type Profile } from '../../components/financials/model'
import { AssumptionsCard, KEY_ASSUMPTIONS, toModel } from '../../components/valuation/AssumptionsCard'
import { FootballField, SensitivityHeatmap, sensitivitySentence } from '../../components/valuation/ValuationCharts'
import { MethodsTable } from '../../components/valuation/MethodsTable'
import { AdvancedValuation } from '../../components/valuation/AdvancedValuation'
import {
  DEFAULT_MODELS,
  HELP,
  deletePath,
  latestRuns,
  methodName,
  plainWarnings,
  prune,
  referenceName,
  schemaFields,
  setPath,
  type AlertRule,
  type MacroResp,
  type ModelsResp,
  type Overview,
  type PeersResp,
} from '../../components/valuation/model'

type RunResp = { results?: Record<string, { value_per_share: number | null; warnings?: string[] }>; errors?: Record<string, string> }

const isValQuery = (q: { queryKey: readonly unknown[] }) => String(q.queryKey[0]).startsWith('val')
const errText = (e: unknown) => String((e as Error)?.message ?? e)
/** Alert rules reference a named value, not a model id. */
const alertReference = (name: string | null | undefined) => (!name || name === 'fcff' ? 'base_dcf' : name === 'external' ? 'imported_fv' : ['base_dcf', 'imported_fv', 'multiples', 'blended', 'analyst'].includes(name) ? name : `model:${name}`)

/** Short status under the price: Live / Pre-market / After-hours / Delayed (Yahoo) / Last trade <time>. */
function priceStatus(q: Parameters<typeof quoteFreshness>[0]): string {
  const f = quoteFreshness(q)
  if (f.tone === 'delayed') return 'Delayed (Yahoo)'
  if (f.tone === 'live') return f.text.split(/ ·| trade/)[0]
  return f.text
}

export default function Valuation({ ticker }: { ticker: string }) {
  const t = ticker.toUpperCase()
  const enc = encodeURIComponent(t)
  const qc = useQueryClient()
  const quote = useQuote(t)

  const profile = useQuery({ queryKey: ['fin-profile', t], queryFn: () => api<Profile>(`/api/market/profile/${enc}`), staleTime: 3_600_000, retry: false })
  const kind = profile.data?.kind
  const nonCompany = kind && kind !== 'stock' ? (NON_COMPANY[kind] ?? `a ${kind}`) : null
  const enabled = !profile.isLoading && !nonCompany

  const ov = useQuery({ queryKey: ['val', t], queryFn: () => api<Overview>(`/api/valuation/${enc}`), enabled })
  const models = useQuery({ queryKey: ['val-models'], queryFn: () => api<ModelsResp>('/api/valuation/models'), enabled, staleTime: 300_000 })
  const coverage = useQuery({ queryKey: ['fin-coverage', t], queryFn: () => api<CoverageRow[]>(`/api/statements/${enc}/coverage`), enabled, retry: false })
  const macro = useQuery({ queryKey: ['val-macro'], queryFn: () => api<MacroResp>('/api/valuation/macro'), enabled, staleTime: 600_000 })
  const peers = useQuery({ queryKey: ['val-peers', t], queryFn: () => api<PeersResp>(`/api/valuation/${enc}/peers`), enabled })
  const rules = useQuery({ queryKey: ['val-alert-rules'], queryFn: () => api<AlertRule[]>('/api/alerts/rules'), enabled })

  const [keyEdits, setKeyEdits] = useState<Record<string, string>>({})
  const [advEdits, setAdvEdits] = useState<Record<string, string>>({})
  const [picked, setPicked] = useState<string[] | null>(null)
  const [alertOpen, setAlertOpen] = useState(false)
  const [threshold, setThreshold] = useState('25')
  const [alertMade, setAlertMade] = useState(false)

  const data = ov.data
  const list = models.data?.models ?? []
  const latest = useMemo(() => latestRuns(data?.runs ?? []), [data])
  const selected = useMemo(() => {
    if (picked) return picked
    const ids = [...new Set([...DEFAULT_MODELS, ...latest.keys()])]
    return list.length ? ids.filter((id) => list.some((m) => m.id === id)) : ids
  }, [picked, latest, list])
  const fcffRun = latest.get('fcff')
  const setId = fcffRun?.assumption_set_id ?? [...latest.values()][0]?.assumption_set_id ?? null
  const currentSet = data?.assumption_sets.find((s) => s.id === setId)
  const currentOverrides = useMemo(() => prune(currentSet?.payload ?? {}), [currentSet])

  const buildPayload = (): Record<string, any> | null => {
    const base: Record<string, any> = structuredClone(currentOverrides)
    for (const [path, s] of Object.entries(keyEdits)) {
      const k = KEY_ASSUMPTIONS.find((x) => x.path === path)
      if (!k) continue
      if (s.trim() === '') deletePath(base, path)
      else setPath(base, path, toModel(s, k.unit))
    }
    const fields = list.flatMap((m) => schemaFields(m.assumptions_schema, m.assumptions_schema?.title && m.assumptions_schema.title !== 'Assumptions' ? 'custom.' : ''))
    for (const [path, s] of Object.entries(advEdits)) {
      if (s === '') continue
      const f = fields.find((x) => x.path === path)
      const v = f?.kind === 'number' ? Number(s) : f?.kind === 'boolean' ? s === 'true' : f?.kind === 'list' ? s.split(/[,\s]+/).filter(Boolean).map(Number) : s
      if (typeof v === 'number' && !Number.isFinite(v)) continue
      setPath(base, path, v)
    }
    const p = prune(base)
    return Object.keys(p).length ? p : null
  }

  const run = useMutation({
    mutationFn: (opts: { setId?: number; edits?: boolean }) => {
      const body: Record<string, unknown> = { model_ids: selected.length ? selected : null, scenario: 'base' }
      if (opts.setId) body.assumption_set_id = opts.setId
      else if (opts.edits) body.assumptions = buildPayload()
      return api<RunResp>(`/api/valuation/${enc}/run`, { method: 'POST', json: body })
    },
    onSuccess: (r) => {
      const dcf = r.results?.fcff
      const s = currencySymbol(data?.fair_values[0]?.currency ?? (t.endsWith('.TA') ? 'ILS' : 'USD'))
      if (dcf?.value_per_share != null) toast(`Valuation updated — DCF value ${s}${dcf.value_per_share.toFixed(2)} per share.`, 'ok')
      else if (dcf) toast(`The DCF could not be calculated: ${plainWarnings(dcf.warnings).join('; ') || 'no reason given'}.`, 'err')
      else toast('Valuation updated.', 'ok')
      const errs = Object.keys(r.errors ?? {})
      if (errs.length) toast(`Some methods failed to run: ${errs.map((k) => methodName(k, list)).join(', ')}.`, 'err')
      setKeyEdits({})
      setAdvEdits({})
      qc.invalidateQueries({ predicate: isValQuery })
    },
    onError: (e) => toast(`The valuation could not run: ${errText(e)}`, 'err'),
  })

  const createAlert = useMutation({
    mutationFn: (thr: number) =>
      api('/api/alerts/rules', { method: 'POST', json: { name: `${t} upside > ${thr}%`, ticker: t, rule_type: 'upside_gt', reference: alertReference(data?.reference.name), threshold: thr } }),
    onSuccess: (_r, thr) => {
      toast(`Alert created: you’ll be notified when ${t}’s upside exceeds ${thr}%. Manage it on the Alerts page.`, 'ok')
      setAlertOpen(false)
      setAlertMade(true)
      qc.invalidateQueries({ queryKey: ['val-alert-rules'] })
    },
    onError: (e) => toast(`Could not create the alert: ${errText(e)}`, 'err'),
  })

  // ---- early states -------------------------------------------------------------------------------------------
  if (profile.isLoading) return <Loading lines={4} />
  if (nonCompany)
    return (
      <Empty title="No valuation">
        Valuation models apply to companies; this is {nonCompany}.
      </Empty>
    )
  if (ov.isLoading) return <Loading lines={6} />
  if (ov.isError || !data) return <ErrorBox error={ov.error} what="the valuation" />

  const sym = currencySymbol(data.fair_values[0]?.currency ?? data.latest.fcff?.currency ?? (t.endsWith('.TA') ? 'ILS' : 'USD'))
  const money2 = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : `${sym}${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
  const price = quote?.last ?? data.price ?? null
  const priceTs = quote?.ts ?? data.price_ts
  const priceStale = quote ? !!quote.stale : !!data.quote_stale
  const priceSrc = quote?.source ?? data.price_source ?? ''
  const priceNote = price === null ? 'No current price available' : `Price ${dateTimeIL(priceTs)} Israel time${priceStale ? ' (latest available, not live)' : ''}${priceSrc.startsWith('yf') ? ', delayed quote (Yahoo)' : ''}`
  const running = run.isPending
  const noStatements = coverage.isError || (coverage.data !== undefined && coverage.data.length === 0)
  const advanced = (
    <Disclosure title="Advanced" hint="Models, all assumptions, imports, history, peers and settings">
      <AdvancedValuation
        ticker={t}
        ov={data}
        models={models.data}
        selected={selected}
        setSelected={setPicked}
        advEdits={advEdits}
        setAdvEdits={setAdvEdits}
        currentOverrides={currentOverrides}
        currentSetId={setId}
        onRunEdits={() => run.mutate({ edits: true })}
        onRunSet={(id) => run.mutate({ setId: id })}
        running={running}
      />
    </Disclosure>
  )

  const ref = data.reference
  if (ref.value === null || ref.value === undefined) {
    if (!data.runs.length && noStatements)
      return (
        <Empty title="Valuation needs financial statements" actions={<button className="primary" onClick={() => navigate(`company/${t}/financials`)}>Open Financials</button>}>
          The models value a company from its revenue, profits, cash flow and balance sheet, and none are loaded for {t} yet. Load or upload them on the Financials tab first.
        </Empty>
      )
    const failed = plainWarnings(fcffRun?.warnings)
    return (
      <div className="stack">
        {data.runs.length === 0 ? (
          <Empty
            title="No valuation yet"
            actions={
              <button className="primary" onClick={() => run.mutate({ edits: true })} disabled={running}>
                {running ? 'Running…' : 'Run valuation'}
              </button>
            }
          >
            Estimate a fair value from the company’s approved financial statements and current market data: a discounted cash flow, price multiples and the growth the current price
            implies. Takes a few seconds.
          </Empty>
        ) : (
          <Card title="Estimated fair value">
            <div className="notice warn" style={{ marginBottom: 12 }}>
              <span>!</span>
              <div className="grow">
                The last valuation ({dateTimeIL(fcffRun?.created_at ?? data.runs[0].created_at)}) couldn’t estimate a fair value{failed.length ? `: ${failed.join('; ')}` : '.'}
              </div>
            </div>
            <button className="primary" onClick={() => run.mutate({ edits: true })} disabled={running}>
              {running ? 'Running…' : 'Run valuation again'}
            </button>
          </Card>
        )}
        {data.runs.length > 0 && list.length > 0 && (
          <Card title="Methods">
            <MethodsTable ov={data} models={list} latest={latest} price={price} sym={sym} peersUsed={!!peers.data?.effective.length} />
          </Card>
        )}
        {advanced}
      </div>
    )
  }

  // ---- summary ----------------------------------------------------------------------------------------------------
  const rn = referenceName(ref.name, list)
  const upside = price ? ref.value / price - 1 : null
  const upPct = upside === null ? null : Math.round(Math.abs(upside) * 100)
  const sentence =
    upside === null || price === null
      ? `The ${rn.short} values the company at ${sym}${Math.round(ref.value)} per share.`
      : upPct === 0
        ? `The ${rn.short} values the company at ${sym}${Math.round(ref.value)} per share, in line with the current price of ${sym}${Math.round(price)}.`
        : `The ${rn.short} values the company at ${sym}${Math.round(ref.value)} per share, ${upPct}% ${upside < 0 ? 'below' : 'above'} the current price of ${sym}${Math.round(price)}.`
  const runAt = fcffRun?.created_at ?? data.runs[0]?.created_at
  const myAlerts = (rules.data ?? []).filter((r) => r.ticker === t && r.rule_type === 'upside_gt' && r.enabled)
  const resolved = data.latest.fcff?.diagnostics?.resolved as Record<string, any> | undefined
  const derivation = (data.latest.fcff?.diagnostics?.derivation ?? resolved?.derivation ?? {}) as Record<string, string>
  const grid = data.sensitivity?.wacc_g
  const rfAsOf = macro.data?.macro?.rf_us_10y?.as_of ?? null

  return (
    <div className="stack">
      <Card
        title="Estimated fair value"
        hint={rn.long}
        actions={
          !alertOpen && (
            <button onClick={() => setAlertOpen(true)} title="Get a notification when the upside to this fair value passes a threshold">
              Create an alert
            </button>
          )
        }
        foot={
          <>
            Valued {dateTimeIL(runAt)} Israel time, using approved financial statements and market data from that day. {priceNote}.
            {myAlerts.length > 0 && (
              <>
                {' '}
                Alert set: upside above {myAlerts.map((r) => `${r.threshold}%`).join(', ')} (<a href={href('alerts')}>manage</a>).
              </>
            )}
          </>
        }
      >
        <div className="stats" style={{ marginBottom: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
          <Stat label="Estimated fair value" value={money2(ref.value)} sub={`${rn.long}, per share`} help={ref.name === 'base_dcf' ? HELP.dcf : undefined} />
          <Stat label="Current price" value={money2(price)} sub={priceStale ? `Latest available, ${dateLabel(priceTs)}` : quote ? priceStatus(quote) : `As of ${dateLabel(priceTs)}`} />
          <Stat label="Upside / downside" value={upside === null ? '—' : pct(upside * 100, 1)} tone={upside === null ? '' : upside < 0 ? 'down' : 'up'} sub={`Fair value vs price`} help={HELP.upside} />
        </div>
        <p style={{ fontSize: 14, color: 'var(--text-2)' }}>{sentence}</p>
        {alertOpen && (
          <div className="row" style={{ marginTop: 12 }}>
            <span className="small">Alert me when upside exceeds</span>
            <input className="num" type="number" step={1} value={threshold} onChange={(e) => setThreshold(e.target.value)} style={{ width: 70, textAlign: 'right' }} aria-label="Upside threshold in percent" />
            <span className="small">%</span>
            <button className="primary" disabled={createAlert.isPending || !Number.isFinite(Number(threshold)) || threshold.trim() === ''} onClick={() => createAlert.mutate(Number(threshold))}>
              {createAlert.isPending ? 'Creating…' : 'Create alert'}
            </button>
            <button className="ghost" onClick={() => setAlertOpen(false)}>
              Cancel
            </button>
            <span className="faint small">Compared with the {rn.long.toLowerCase()} value; sent to Telegram and the in-app inbox.</span>
          </div>
        )}
        {alertMade && !alertOpen && (
          <div className="small" style={{ marginTop: 10 }}>
            <span className="up">✓</span> Alert created. <a href={href('alerts')}>Open alerts</a>
          </div>
        )}
      </Card>

      {resolved && (
        <AssumptionsCard
          resolved={resolved}
          derivation={derivation}
          overrides={currentOverrides}
          edits={keyEdits}
          setEdit={(path, v) => setKeyEdits({ ...keyEdits, [path]: v })}
          onRun={() => run.mutate({ edits: true })}
          onReset={() => setKeyEdits(Object.fromEntries(KEY_ASSUMPTIONS.map((k) => [k.path, ''])))}
          running={running}
          rfAsOf={rfAsOf}
          foot={`Automatic values are recalculated from the latest approved statements and market data on every run. Hover “Automatic” for the exact rule.`}
        />
      )}

      <div className="grid-2">
        <Card title="Range of values" hint={`per share, ${sym.trim() || 'local currency'}`} foot="Bars span each method’s low-to-high estimate; dots mark the base value. Price multiples span the 25th–75th percentile of comparable multiples.">
          {data.football_field.length ? <FootballField rows={data.football_field} price={price} fairValue={ref.value} sym={sym} models={list} /> : <div className="muted">No method produced a range yet.</div>}
        </Card>
        <Card title="Sensitivity" hint="DCF value per share by discount rate and terminal growth" foot="Green = above the current price, red = below, grey = close to it. The outlined cell is the base case.">
          {grid && grid.values.length ? (
            <>
              <SensitivityHeatmap grid={grid} price={price} sym={sym} />
              <p className="small" style={{ color: 'var(--text-2)', marginTop: 6 }}>
                {sensitivitySentence(grid, price, sym)}
              </p>
            </>
          ) : (
            <div className="muted">The sensitivity grid is calculated when the DCF runs successfully.</div>
          )}
        </Card>
      </div>

      <Card title="Methods" hint={price !== null ? `Upside against the current price of ${money2(price)}` : undefined}>
        <MethodsTable ov={data} models={list} latest={latest} price={price} sym={sym} peersUsed={!!peers.data?.effective.length} />
      </Card>

      {advanced}
    </div>
  )
}
