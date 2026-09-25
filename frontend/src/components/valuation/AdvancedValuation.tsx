import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Observations } from '../../lib/api'
import { dateLabel, dateTimeIL, money } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Loading } from '../../ui'
import EChart, { type EChartsOption } from '../charts/EChart'
import { SubHead, VIZ, axisCat, axisVal, vizBase } from '../financials/shared'
import { flatten, methodName, prune, schemaFields, type MacroResp, type ModelsResp, type Overview, type PeersResp, type Policies, type SchemaField } from './model'

const errText = (e: unknown) => String((e as Error)?.message ?? e)
const SOURCE_WORDS: Record<string, string> = { auto: 'Automatic', manual: 'Your overrides' }
const IMPORT_KINDS: [string, string][] = [
  ['csv', 'CSV file (key, value, unit, scenario, note)'],
  ['xlsx', 'Excel file (same columns)'],
  ['sheets', 'Google Sheets link (shared as “anyone with the link”)'],
  ['ginzu', 'Damodaran valuation spreadsheet (fcffsimpleginzu.xlsx)'],
]

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ borderTop: '1px solid var(--border)', paddingTop: 4 }}>
      <SubHead>{title}</SubHead>
      {children}
    </section>
  )
}

/** Everything that is useful but not needed for the default view. */
export function AdvancedValuation({
  ticker,
  ov,
  models,
  selected,
  setSelected,
  advEdits,
  setAdvEdits,
  currentOverrides,
  currentSetId,
  onRunEdits,
  onRunSet,
  running,
}: {
  ticker: string
  ov: Overview
  models: ModelsResp | undefined
  selected: string[]
  setSelected: (ids: string[]) => void
  advEdits: Record<string, string>
  setAdvEdits: (e: Record<string, string>) => void
  currentOverrides: Record<string, any>
  currentSetId: number | null
  onRunEdits: () => void
  onRunSet: (id: number) => void
  running: boolean
}) {
  const t = ticker
  const enc = encodeURIComponent(t)
  const qc = useQueryClient()
  const list = models?.models ?? []

  // ---- models ----
  const reload = useMutation({
    mutationFn: () => api<{ loaded: string[]; errors: Record<string, string> }>('/api/valuation/models/reload', { method: 'POST' }),
    onSuccess: (r) => {
      const errs = Object.keys(r.errors ?? {})
      toast(`Custom models loaded: ${r.loaded?.length ? r.loaded.join(', ') : 'none'}${errs.length ? ` · failed: ${errs.join(', ')}` : ''}`, errs.length ? 'err' : 'ok')
      qc.invalidateQueries({ queryKey: ['val-models'] })
    },
    onError: (e) => toast(`Could not reload custom models: ${errText(e)}`, 'err'),
  })

  // ---- full assumptions form ----
  const [formModel, setFormModel] = useState('fcff')
  const model = list.find((m) => m.id === formModel)
  const fields: SchemaField[] = useMemo(() => {
    if (!model) return []
    const custom = model.assumptions_schema?.title && model.assumptions_schema.title !== 'Assumptions'
    return schemaFields(model.assumptions_schema, custom ? 'custom.' : '')
  }, [model])
  const flatOverrides = useMemo(() => Object.fromEntries(flatten(currentOverrides)), [currentOverrides])

  // ---- import ----
  const [kind, setKind] = useState('csv')
  const [file, setFile] = useState<File | null>(null)
  const [url, setUrl] = useState('')
  const [imported, setImported] = useState<number | null>(null)
  const doImport = useMutation({
    mutationFn: () => {
      const fd = new FormData()
      fd.append('kind', kind)
      if (file) fd.append('file', file)
      if (url) fd.append('url', url)
      return api<{ sets?: { id: number }[]; errors?: string[] }>(`/api/valuation/${enc}/import`, { method: 'POST', body: fd })
    },
    onSuccess: (r) => {
      const n = r.sets?.length ?? 0
      toast(`Imported ${n} assumption set${n === 1 ? '' : 's'}${r.errors?.length ? ` · ${r.errors.join('; ')}` : ''}`, r.errors?.length ? 'err' : 'ok')
      setImported(r.sets?.[0]?.id ?? null)
      qc.invalidateQueries({ queryKey: ['val', t] })
    },
    onError: (e) => toast(`Import failed: ${errText(e)}`, 'err'),
  })

  // ---- peers ----
  const peers = useQuery({ queryKey: ['val-peers', t], queryFn: () => api<PeersResp>(`/api/valuation/${enc}/peers`) })
  const [pins, setPins] = useState<string | null>(null)
  const [excl, setExcl] = useState<string | null>(null)
  const findPeers = useMutation({
    mutationFn: () => api(`/api/valuation/${enc}/peers?rebuild=true`),
    onSuccess: () => {
      toast('Peer list updated — re-run the valuation to use it.', 'ok')
      qc.invalidateQueries({ queryKey: ['val-peers', t] })
    },
    onError: (e) => toast(`Could not find peers: ${errText(e)}`, 'err'),
  })
  const savePeers = useMutation({
    mutationFn: () => {
      const split = (s: string) => s.split(/[,\s]+/).map((x) => x.trim().toUpperCase()).filter(Boolean)
      return api(`/api/valuation/${enc}/peers`, { method: 'PUT', json: { pins: split(pins ?? peers.data?.pins.join(', ') ?? ''), excludes: split(excl ?? peers.data?.excludes.join(', ') ?? '') } })
    },
    onSuccess: () => {
      toast('Peers saved — re-run the valuation to use them.', 'ok')
      setPins(null)
      setExcl(null)
      qc.invalidateQueries({ queryKey: ['val-peers', t] })
    },
    onError: (e) => toast(`Could not save peers: ${errText(e)}`, 'err'),
  })

  // ---- policies ----
  const pol = useQuery({ queryKey: ['val-policies'], queryFn: () => api<Policies>('/api/valuation/policies') })
  const [polEdit, setPolEdit] = useState<Partial<Policies>>({})
  const polVal = { ...(pol.data ?? {}), ...polEdit } as Policies
  const savePol = useMutation({
    mutationFn: () => api<Policies>('/api/valuation/policies', { method: 'PUT', json: polVal }),
    onSuccess: () => {
      toast('Valuation settings saved — re-run to apply them.', 'ok')
      setPolEdit({})
      qc.invalidateQueries({ queryKey: ['val-policies'] })
    },
    onError: (e) => toast(`Could not save settings: ${errText(e)}`, 'err'),
  })

  // ---- market inputs ----
  const macro = useQuery({ queryKey: ['val-macro'], queryFn: () => api<MacroResp>('/api/valuation/macro'), staleTime: 600_000 })
  const refreshMacro = useMutation({
    mutationFn: () => api<MacroResp>('/api/valuation/macro?refresh=true'),
    onSuccess: () => {
      toast('Market inputs refreshed — re-run the valuation to use them.', 'ok')
      qc.invalidateQueries({ queryKey: ['val-macro'] })
    },
    onError: (e) => toast(`Could not refresh market inputs: ${errText(e)}`, 'err'),
  })

  // ---- fair value history ----
  const fv = useQuery({ queryKey: ['val-fv-series', t], queryFn: () => api<Observations>(`/api/series/${encodeURIComponent(`val:${t}:fair_value_base`)}/observations`), retry: false })
  const hasHistory = (fv.data?.n ?? 0) >= 2
  const px = useQuery({ queryKey: ['val-px', t], queryFn: () => api<{ ts: string[]; close: number[] }>(`/api/market/ohlcv/${enc}?limit=500`), enabled: hasHistory, retry: false })
  const histOption = useMemo<EChartsOption | null>(() => {
    if (!hasHistory || !fv.data) return null
    const first = fv.data.ts[0].slice(0, 10)
    const pxPts = px.data ? px.data.ts.map((d, i) => [d, px.data!.close[i]]).filter((p) => String(p[0]).slice(0, 10) >= first) : []
    return {
      ...vizBase,
      legend: { ...vizBase.legend, data: ['Fair value (base-case DCF)', 'Share price'] },
      grid: { left: 52, right: 12, top: 30, bottom: 26 },
      xAxis: { type: 'time', ...axisCat },
      yAxis: { type: 'value', scale: true, ...axisVal },
      series: [
        { name: 'Fair value (base-case DCF)', type: 'line', step: 'end', showSymbol: false, data: fv.data.ts.map((d, i) => [d, fv.data!.value[i]]), lineStyle: { color: VIZ.amber, width: 2 }, itemStyle: { color: VIZ.amber } },
        { name: 'Share price', type: 'line', showSymbol: false, data: pxPts, lineStyle: { color: VIZ.blue, width: 2 }, itemStyle: { color: VIZ.blue } },
      ],
    } as EChartsOption
  }, [hasHistory, fv.data, px.data])

  const dcf = ov.latest.fcff
  const proj = dcf?.components?.projection
  const years: any[] = proj?.years ?? []
  const bn = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : money(v))
  const pc = (v: number | null | undefined) => (v === null || v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`)
  const rf = macro.data?.macro?.rf_us_10y
  const crp = Object.entries(macro.data?.macro ?? {}).find(([k]) => k.startsWith('crp_'))

  return (
    <div className="stack" style={{ gap: 14 }}>
      {dcf?.components?.bridge && (
        <Section title="DCF details (base case)">
          <div className="grid-2" style={{ alignItems: 'start' }}>
            <table className="table compact">
              <tbody>
                {(
                  [
                    ['Enterprise value (present value of cash flows)', dcf.components.bridge.ev, false],
                    ['− Debt', dcf.components.bridge.debt, false],
                    ['− Lease liabilities', dcf.components.bridge.leases, false],
                    ['− Minority interest', dcf.components.bridge.minority, false],
                    ['+ Cash and short-term investments', dcf.components.bridge.cash, false],
                    ['+ Non-operating assets', dcf.components.bridge.non_operating_assets, false],
                    ['= Equity value', dcf.components.bridge.equity_value, true],
                  ] as [string, number | null, boolean][]
                ).map(([k, v, total]) => (
                  <tr key={k} className={total ? 'total' : ''}>
                    <td>{k}</td>
                    <td className="r num">{bn(v)}</td>
                  </tr>
                ))}
                <tr>
                  <td>÷ Diluted shares</td>
                  <td className="r num">{dcf.components.bridge.shares ? `${(dcf.components.bridge.shares / 1e6).toLocaleString('en-US', { maximumFractionDigits: 1 })}M` : '—'}</td>
                </tr>
                <tr className="total">
                  <td>= Value per share</td>
                  <td className="r num">{dcf.components.bridge.value_per_share != null ? dcf.components.bridge.value_per_share.toFixed(2) : '—'}</td>
                </tr>
              </tbody>
            </table>
            <div className="small muted">
              {proj?.pv_terminal_share != null && (
                <p>
                  {(proj.pv_terminal_share * 100).toFixed(0)}% of the enterprise value comes from the years after the forecast (the terminal value), so the result is most sensitive to
                  the discount rate and terminal growth.
                </p>
              )}
            </div>
          </div>
          {years.length > 0 && (
            <div className="table-wrap" style={{ marginTop: 10 }}>
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Year</th>
                    <th className="r">Revenue</th>
                    <th className="r">Growth</th>
                    <th className="r">Operating income</th>
                    <th className="r">Margin</th>
                    <th className="r">Tax rate</th>
                    <th className="r">Reinvestment</th>
                    <th className="r">Free cash flow</th>
                    <th className="r">Present value</th>
                  </tr>
                </thead>
                <tbody>
                  {years.map((y: any) => (
                    <tr key={y.year}>
                      <td>{y.year}</td>
                      <td className="r num">{bn(y.revenue)}</td>
                      <td className="r num">{pc(y.growth)}</td>
                      <td className="r num">{bn(y.ebit)}</td>
                      <td className="r num">{pc(y.margin)}</td>
                      <td className="r num">{pc(y.tax_rate)}</td>
                      <td className="r num">{bn(y.reinvestment)}</td>
                      <td className="r num">{bn(y.fcff)}</td>
                      <td className="r num">{bn(y.pv)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="card-foot">Money in {dcf.currency || 'the reporting currency'}, compact (B = billions). Reinvestment = capital expenditure − depreciation + change in working capital.</div>
        </Section>
      )}

      <Section title="Models to run">
        <div className="stack" style={{ gap: 6 }}>
          {list.map((m) => (
            <label key={m.id} className="check" title={`Needs: ${m.inputs_required.join(', ') || 'nothing'} · version ${m.version}`}>
              <input type="checkbox" checked={selected.includes(m.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, m.id] : selected.filter((x) => x !== m.id))} />
              {methodName(m.id, list)}
              {m.user && <span className="badge">custom</span>}
              <span className="faint xs">{m.name}</span>
            </label>
          ))}
        </div>
        {models && Object.keys(models.errors).length > 0 && (
          <div className="notice err" style={{ marginTop: 8 }}>
            <span>⚠</span>
            <div className="grow">
              Some of your custom models failed to load:{' '}
              {Object.entries(models.errors)
                .map(([f, e]) => `${f}: ${e}`)
                .join('; ')}
            </div>
          </div>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button onClick={() => reload.mutate()} disabled={reload.isPending}>
            {reload.isPending ? 'Reloading…' : 'Reload custom models'}
          </button>
          {models?.user_dir && (
            <span className="faint small">
              Custom models are Python files in <code>{models.user_dir}</code>
            </span>
          )}
        </div>
      </Section>

      <Section title="All assumptions">
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="muted small">Form for</span>
          <select value={formModel} onChange={(e) => setFormModel(e.target.value)}>
            {list.map((m) => (
              <option key={m.id} value={m.id}>
                {methodName(m.id, list)}
              </option>
            ))}
          </select>
          <span className="faint small">Rates as decimals (0.09 = 9%). Empty keeps the current value. Changes apply together with the key assumptions.</span>
        </div>
        <div className="form" style={{ gridTemplateColumns: '280px minmax(0, 260px)', maxWidth: 620 }}>
          {fields.map((f) => {
            const cur = flatOverrides[f.path]
            const ph = cur !== undefined ? String(cur) : 'automatic'
            const v = advEdits[f.path] ?? ''
            const set = (x: string) => setAdvEdits({ ...advEdits, [f.path]: x })
            return (
              <div key={f.path} style={{ display: 'contents' }}>
                <label title={f.description}>
                  {f.label} <span className="mono faint xs">{f.path}</span>
                </label>
                {f.kind === 'boolean' ? (
                  <select value={v} onChange={(e) => set(e.target.value)}>
                    <option value="">{cur !== undefined ? `current (${String(cur)})` : 'automatic'}</option>
                    <option value="true">yes</option>
                    <option value="false">no</option>
                  </select>
                ) : f.kind === 'enum' ? (
                  <select value={v} onChange={(e) => set(e.target.value)}>
                    <option value="">{cur !== undefined ? `current (${String(cur)})` : 'automatic'}</option>
                    {f.options!.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input className="num" placeholder={f.kind === 'list' ? (cur !== undefined ? String(cur) : '0.10, 0.08, …') : ph} value={v} onChange={(e) => set(e.target.value)} />
                )}
              </div>
            )
          })}
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button onClick={onRunEdits} disabled={running}>
            {running ? 'Running…' : 'Re-run with these assumptions'}
          </button>
          {Object.values(advEdits).some(Boolean) && (
            <button className="ghost" onClick={() => setAdvEdits({})}>
              Clear this form
            </button>
          )}
        </div>
      </Section>

      <Section title="Import assumptions or a fair value">
        <div className="form" style={{ gridTemplateColumns: '160px minmax(0, 1fr)', maxWidth: 720 }}>
          <label>Format</label>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {IMPORT_KINDS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
          <label>File</label>
          <input type="file" accept=".csv,.xlsx,.xls" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <label>Or a link</label>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…" />
          <span />
          <div className="row">
            <button onClick={() => doImport.mutate()} disabled={doImport.isPending || (!file && !url)}>
              {doImport.isPending ? 'Importing…' : 'Import'}
            </button>
            {imported !== null && (
              <button className="primary" onClick={() => onRunSet(imported)} disabled={running}>
                Run with the imported set
              </button>
            )}
            <span className="faint small">
              Keys such as <code>fair_value</code>, <code>wacc.beta</code>, <code>terminal.g</code>, <code>revenue_growth</code> (see user_models/README.md).
            </span>
          </div>
        </div>
      </Section>

      <Section title="Assumption history">
        {ov.assumption_sets.length === 0 ? (
          <div className="faint small">No assumption sets yet.</div>
        ) : (
          <table className="table compact">
            <thead>
              <tr>
                <th>Created</th>
                <th>Type</th>
                <th>What it changes</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {ov.assumption_sets.map((s) => {
                const changes = flatten(prune(s.payload))
                return (
                  <tr key={s.id}>
                    <td className="num nowrap">{dateTimeIL(s.created_at)}</td>
                    <td className="nowrap">
                      {SOURCE_WORDS[s.source] ?? 'Imported'} {s.id === currentSetId && <span className="badge ok">in use</span>}
                    </td>
                    <td className="small">
                      {changes.length === 0 ? (
                        <span className="faint">Nothing — all values automatic</span>
                      ) : (
                        <span className="mono xs">
                          {changes
                            .slice(0, 5)
                            .map(([k, v]) => `${k} = ${Array.isArray(v) ? v.join(', ') : String(v)}`)
                            .join(' · ')}
                          {changes.length > 5 ? ` · +${changes.length - 5} more` : ''}
                        </span>
                      )}
                    </td>
                    <td className="r">
                      <button className="ghost" onClick={() => onRunSet(s.id)} disabled={running}>
                        Run with this set
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Peers for the multiples method">
        {peers.isLoading ? (
          <Loading lines={1} />
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            <div className="small">
              {peers.data?.effective.length ? (
                <span className="row" style={{ gap: 6 }}>
                  {peers.data.effective.map((p) => (
                    <span key={p} className="chip ticker" style={{ cursor: 'default' }}>
                      {p}
                    </span>
                  ))}
                </span>
              ) : (
                <span className="muted">No peers yet — the multiples method compares the company with its own history.</span>
              )}
              {peers.data?.industry && <div className="faint xs">Industry: {peers.data.industry}</div>}
            </div>
            <div className="form" style={{ gridTemplateColumns: '160px minmax(0, 360px)', maxWidth: 560 }}>
              <label>Always include</label>
              <input value={pins ?? peers.data?.pins.join(', ') ?? ''} onChange={(e) => setPins(e.target.value)} placeholder="e.g. MSFT, GOOGL" />
              <label>Exclude</label>
              <input value={excl ?? peers.data?.excludes.join(', ') ?? ''} onChange={(e) => setExcl(e.target.value)} placeholder="tickers to leave out" />
            </div>
            <div className="row">
              <button onClick={() => savePeers.mutate()} disabled={savePeers.isPending || (pins === null && excl === null)}>
                Save peers
              </button>
              <button className="ghost" onClick={() => findPeers.mutate()} disabled={findPeers.isPending}>
                {findPeers.isPending ? 'Finding peers…' : 'Find peers automatically'}
              </button>
            </div>
          </div>
        )}
      </Section>

      <Section title="Valuation settings (all companies)">
        {pol.isLoading || !pol.data ? (
          <Loading lines={2} />
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            {(
              [
                ['sbc_cash_expense', 'Treat stock-based compensation as a cash cost'],
                ['leases_as_debt', 'Count lease liabilities as debt'],
                ['rd_capitalize', 'Capitalize research and development (5-year straight line)'],
                ['mid_year', 'Mid-year discounting (cash flows arrive mid-year)'],
              ] as [keyof Policies, string][]
            ).map(([k, l]) => (
              <label key={k} className="check">
                <input type="checkbox" checked={!!polVal[k]} onChange={(e) => setPolEdit({ ...polEdit, [k]: e.target.checked })} /> {l}
              </label>
            ))}
            <div className="row">
              <label className="check">
                Cap on terminal growth
                <input className="num" type="number" step={0.1} style={{ width: 72 }} value={Number((polVal.g_cap * 100).toFixed(2))} onChange={(e) => setPolEdit({ ...polEdit, g_cap: Number(e.target.value) / 100 })} />%
              </label>
              <label className="check">
                Default forecast years
                <input className="num" type="number" min={1} max={20} style={{ width: 64 }} value={polVal.forecast_years} onChange={(e) => setPolEdit({ ...polEdit, forecast_years: Math.round(Number(e.target.value)) })} />
              </label>
            </div>
            <div className="row">
              <button onClick={() => savePol.mutate()} disabled={savePol.isPending || Object.keys(polEdit).length === 0}>
                Save settings
              </button>
            </div>
          </div>
        )}
      </Section>

      <Section title="Market inputs">
        <div className="row small" style={{ gap: 16 }}>
          <span>
            Risk-free rate (US 10-year Treasury): <b className="num">{rf?.value != null ? `${(rf.value * 100).toFixed(2)}%` : '—'}</b>{' '}
            <span className="faint">{rf?.as_of ? `as of ${dateLabel(rf.as_of)}` : ''}</span>
          </span>
          {crp && (
            <span>
              Country risk premium: <b className="num">{crp[1].value != null ? `${(crp[1].value * 100).toFixed(2)}%` : '—'}</b> <span className="faint">{crp[1].as_of ? `as of ${dateLabel(crp[1].as_of)}` : ''}</span>
            </span>
          )}
          <button className="ghost" onClick={() => refreshMacro.mutate()} disabled={refreshMacro.isPending}>
            {refreshMacro.isPending ? 'Refreshing…' : 'Refresh market inputs'}
          </button>
        </div>
      </Section>

      {histOption && (
        <Section title="Fair value over time">
          <EChart option={histOption} height={220} />
        </Section>
      )}
    </div>
  )
}
