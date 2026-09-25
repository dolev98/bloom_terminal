import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type SeriesSpec } from '../../lib/api'
import { navigate } from '../../lib/router'
import { toast } from '../../lib/toast'
import { Segmented } from '../../ui'
import SeriesLineChart, { toLinePoints } from '../charts/SeriesLineChart'
import { CATEGORIES, COUNTRY_NAME, FREQ_LABEL, chartPrecision, formatValue, periodLabel, sourceLabel, unitLabel, type CatalogItem } from './labels'

type Mode = 'link' | 'manual'
type Preview = { n: number; first_ts: string | null; last_ts: string | null; ts: string[]; value: number[]; partial: boolean; skipped?: number }

const UNITS: [string, string][] = [
  ['pct', 'Percent (%)'],
  ['bp', 'Basis points'],
  ['index', 'Index'],
  ['usd', 'US dollars'],
  ['ils', 'Shekels'],
  ['usd_bn', 'US dollars, billions'],
  ['usd_mn', 'US dollars, millions'],
  ['ils_mn', 'Shekels, millions'],
  ['thousands', 'Thousands'],
  ['count', 'Count'],
  ['', 'No unit'],
]
const KINDS: [string, string][] = [
  ['price', 'Price or exchange rate'],
  ['yield', 'Interest rate or yield'],
  ['spread', 'Spread between two rates'],
  ['level_index', 'Index level'],
  ['flow', 'Amount per period (e.g. sales)'],
  ['stock', 'Amount outstanding (e.g. debt)'],
  ['ratio', 'Ratio'],
  ['survey', 'Survey result or rate (%)'],
  ['count', 'Count'],
  ['other', 'Other'],
]
const TRANSFORMS: [string, string][] = [
  ['level', 'Level'],
  ['diff', 'Change'],
  ['diff_bp', 'Change in basis points'],
  ['pct', '% change'],
  ['log_ret', 'Log return (for prices)'],
  ['yoy', 'Year-over-year % change'],
]

function slug(name: string): string {
  return name
    .normalize('NFKD')
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toUpperCase()
    .slice(0, 40)
}

/** Mirrors the backend parser: "date,value" per line (comma, semicolon or tab), header optional, YYYY-MM[-DD]. */
function parseCsv(text: string): Preview {
  const rows: [string, number][] = []
  let skipped = 0
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line || /^(date|ts)/i.test(line)) continue
    const [d0, v0] = line.replace(/[;\t]/g, ',').split(',').map((x) => x.trim())
    const d = d0?.length === 7 ? `${d0}-01` : d0
    const v = Number(v0)
    if (!d || !/^\d{4}-\d{2}-\d{2}$/.test(d) || v0 === undefined || v0 === '' || !Number.isFinite(v)) {
      skipped++
      continue
    }
    rows.push([`${d}T00:00:00`, v])
  }
  rows.sort((a, b) => a[0].localeCompare(b[0]))
  return { n: rows.length, first_ts: rows[0]?.[0] ?? null, last_ts: rows.at(-1)?.[0] ?? null, ts: rows.map((r) => r[0]), value: rows.map((r) => r[1]), partial: false, skipped }
}

/** Resolve errors → what to do about them. */
function explainResolveError(e: unknown): string {
  const msg = String((e as Error)?.message ?? e)
  if (/no provider recognises/i.test(msg)) return 'This link isn’t from a source the terminal can read. Supported: FRED, Bank of Israel and Yahoo Finance links — or type an id such as fred:UNRATE.'
  if (/cannot parse id|bad series id/i.test(msg)) return 'That doesn’t look like a link or a series id. Ids look like fred:UNRATE or boi:EXR/RER_EUR_ILS.'
  if (/missing settings/i.test(msg)) return 'This source needs an API key first. Add it on the Settings page, then try again.'
  if (/grey sources disabled/i.test(msg)) return 'Unofficial sources (like Yahoo Finance) are switched off. Turn them on in Settings → Preferences.'
  return msg.replace(/^\d{3}: /, '')
}

export default function AddSeriesDialog({ onClose, existing }: { onClose: () => void; existing: CatalogItem[] }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<Mode>('link')
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [text, setText] = useState('')
  const [manual, setManual] = useState({ name: '', freq: '1mo', unit: 'index', csv: '' })
  const [spec, setSpec] = useState<SeriesSpec | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const already = spec ? existing.find((i) => i.series_id === spec.series_id) : undefined

  const resolve = useMutation({
    mutationFn: async () => {
      if (mode === 'manual') {
        const id = slug(manual.name)
        if (!id) throw new Error('Give the series a name first.')
        const s = await api<SeriesSpec>('/api/catalog/resolve', { method: 'POST', json: { text: `manual:${id}` } })
        return { ...s, name: manual.name.trim(), freq: manual.freq, unit: manual.unit }
      }
      return api<SeriesSpec>('/api/catalog/resolve', { method: 'POST', json: { text: text.trim() } })
    },
    onSuccess: (s) => {
      setSpec(s)
      setPreview(null)
      setErr('')
      setStep(2)
    },
    onError: (e) => setErr(mode === 'manual' ? String((e as Error).message) : explainResolveError(e)),
  })

  const test = useMutation({
    mutationFn: async (): Promise<Preview> => {
      if (!spec) throw new Error('nothing to preview')
      if (spec.provider === 'manual') return parseCsv(manual.csv)
      const r = await api<Omit<Preview, 'partial'>>('/api/catalog/test-fetch', { method: 'POST', json: { spec } })
      return { ...r, partial: r.n > r.ts.length }
    },
    onSuccess: (p) => {
      setPreview(p)
      setErr('')
      setStep(3)
    },
    onError: (e) => setErr(`Could not load a preview: ${String((e as Error).message).replace(/^\d{3}: /, '')}`),
  })

  const save = useMutation({
    mutationFn: async () => {
      if (!spec) return
      const isManual = spec.provider === 'manual'
      await api(`/api/catalog/${encodeURIComponent(spec.series_id)}?refresh=${!isManual}`, { method: 'PUT', json: spec })
      if (isManual && manual.csv.trim()) await api(`/api/catalog/${encodeURIComponent(spec.series_id)}/manual`, { method: 'POST', json: { csv: manual.csv, replace: false } })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['catalog'] })
      toast(`Added “${spec?.name}” to the catalog`, 'ok')
      onClose()
      if (spec) navigate(`series/${spec.series_id}`)
    },
    onError: (e) => setErr(`Could not save: ${String((e as Error).message).replace(/^\d{3}: /, '')}`),
  })

  const set = <K extends keyof SeriesSpec>(k: K, v: SeriesSpec[K]) => spec && setSpec({ ...spec, [k]: v })
  const points = useMemo(() => (preview ? toLinePoints(preview.ts, preview.value) : []), [preview])
  const busy = resolve.isPending || test.isPending || save.isPending

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Add a data series">
      <div className="card" style={{ width: 720, maxWidth: '94vw', maxHeight: '82vh', overflow: 'auto', padding: '18px 22px' }}>
        <div className="card-head">
          <h2>Add a data series</h2>
          <span className="hint">
            Step {step} of 3 — {step === 1 ? 'where the data comes from' : step === 2 ? 'check the details' : 'preview and save'}
          </span>
          <div className="actions">
            <button className="ghost" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>

        {step === 1 && (
          <div className="stack" style={{ gap: 12 }}>
            <Segmented<Mode>
              value={mode}
              onChange={(m) => {
                setMode(m)
                setErr('')
              }}
              options={[
                ['link', 'Paste a link or id'],
                ['manual', 'Enter values manually (CSV)'],
              ]}
            />
            {mode === 'link' ? (
              <>
                <input autoFocus value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && text.trim() && resolve.mutate()} placeholder="Paste a link from FRED, Bank of Israel or Yahoo Finance — or an id" />
                <div className="small faint">
                  Examples: <span className="mono">https://fred.stlouisfed.org/series/UNRATE</span> · <span className="mono">boi:EXR/RER_EUR_ILS</span> · <span className="mono">https://finance.yahoo.com/quote/TEVA.TA</span>
                </div>
              </>
            ) : (
              <div className="form" style={{ gridTemplateColumns: '140px minmax(0,1fr)' }}>
                <label>Name</label>
                <input autoFocus value={manual.name} onChange={(e) => setManual({ ...manual, name: e.target.value })} placeholder="e.g. ISM services PMI" />
                <label>Frequency</label>
                <select value={manual.freq} onChange={(e) => setManual({ ...manual, freq: e.target.value })}>
                  {['1d', '1w', '1mo', '1q', '1y'].map((f) => (
                    <option key={f} value={f}>
                      {FREQ_LABEL[f]}
                    </option>
                  ))}
                </select>
                <label>Unit</label>
                <select value={manual.unit} onChange={(e) => setManual({ ...manual, unit: e.target.value })}>
                  {UNITS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
                <label style={{ alignSelf: 'start', paddingTop: 6 }}>
                  Values
                  <div className="hint">One per line: date, value</div>
                </label>
                <textarea rows={6} value={manual.csv} onChange={(e) => setManual({ ...manual, csv: e.target.value })} placeholder={'2026-07,49.1\n2026-08,48.7'} className="mono" />
              </div>
            )}
            {err && <div className="notice err">{err}</div>}
            <div className="row">
              <span className="spacer" />
              <button onClick={onClose}>Cancel</button>
              <button className="primary" disabled={busy || (mode === 'link' ? !text.trim() : !manual.name.trim())} onClick={() => resolve.mutate()}>
                {resolve.isPending ? 'Looking it up…' : 'Next'}
              </button>
            </div>
          </div>
        )}

        {step === 2 && spec && (
          <div className="stack" style={{ gap: 12 }}>
            <div className="small muted">
              Source: {sourceLabel(spec.provider)}
              {spec.license_note ? <span className="faint"> · {spec.license_note}</span> : null}
            </div>
            {already && (
              <div className="notice info">
                <div className="grow">
                  This series is already in your catalog as “{already.name}”. Saving will update its settings. <a href={`#/series/${already.series_id}`}>Open it instead</a>
                </div>
              </div>
            )}
            <div className="form" style={{ gridTemplateColumns: '140px minmax(0,1fr)' }}>
              <label>Name</label>
              <input value={spec.name} onChange={(e) => set('name', e.target.value)} />
              <label>Frequency</label>
              <select value={spec.freq} onChange={(e) => set('freq', e.target.value)}>
                {Object.entries(FREQ_LABEL).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
              <label>Unit</label>
              <select value={spec.unit} onChange={(e) => set('unit', e.target.value)}>
                {!UNITS.some(([v]) => v === spec.unit) && <option value={spec.unit}>{unitLabel(spec.unit) || spec.unit}</option>}
                {UNITS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
              <label>Country</label>
              <select value={spec.country ?? ''} onChange={(e) => set('country', e.target.value || null)}>
                <option value="">None / global</option>
                {Object.entries(COUNTRY_NAME).map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
              <label>Category</label>
              <select value={spec.category} onChange={(e) => set('category', e.target.value)}>
                {!CATEGORIES.some(([v]) => v === spec.category) && <option value={spec.category}>{spec.category === 'manual' ? 'Manual' : spec.category}</option>}
                {CATEGORIES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
            <details>
              <summary className="small muted" style={{ cursor: 'pointer' }}>
                More settings
              </summary>
              <div className="form" style={{ gridTemplateColumns: '180px minmax(0,1fr)', marginTop: 10 }}>
                <label>Series id</label>
                <span className="mono small">{spec.series_id}</span>
                <label>Kind of value</label>
                <select value={spec.value_kind} onChange={(e) => set('value_kind', e.target.value)}>
                  {KINDS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
                <label>
                  Default for correlations
                  <div className="hint">How the series is compared with others</div>
                </label>
                <select value={spec.default_transform} onChange={(e) => set('default_transform', e.target.value)}>
                  {TRANSFORMS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
                <label>Seasonally adjusted</label>
                <label className="check">
                  <input type="checkbox" checked={!!spec.sa} onChange={(e) => set('sa', e.target.checked)} /> Yes
                </label>
                <label>
                  Publication delay (days)
                  <div className="hint">Extra days before a period’s value normally appears</div>
                </label>
                <input type="number" min={0} style={{ width: 100 }} value={spec.publication_lag_days ?? 0} onChange={(e) => set('publication_lag_days', Number(e.target.value) || 0)} />
                <label>
                  Plausible range
                  <div className="hint">Values outside it are dropped as errors</div>
                </label>
                <span className="row">
                  <input style={{ width: 100 }} placeholder="min" value={spec.plausible_min ?? ''} onChange={(e) => set('plausible_min', e.target.value === '' ? null : Number(e.target.value))} />
                  <span className="faint">to</span>
                  <input style={{ width: 100 }} placeholder="max" value={spec.plausible_max ?? ''} onChange={(e) => set('plausible_max', e.target.value === '' ? null : Number(e.target.value))} />
                </span>
                <label>Tags</label>
                <input value={spec.tags.join(', ')} placeholder="comma separated" onChange={(e) => set('tags', e.target.value.split(',').map((t) => t.trim()).filter(Boolean))} />
              </div>
            </details>
            {err && <div className="notice err">{err}</div>}
            <div className="row">
              <button onClick={() => setStep(1)}>Back</button>
              <span className="spacer" />
              <button className="primary" disabled={busy || !spec.name.trim()} onClick={() => test.mutate()}>
                {test.isPending ? 'Loading a preview…' : 'Preview'}
              </button>
            </div>
          </div>
        )}

        {step === 3 && spec && preview && (
          <div className="stack" style={{ gap: 12 }}>
            <div>
              <b>{spec.name}</b>
              <div className="small muted">
                {preview.n > 0 ? (
                  <>
                    {preview.n.toLocaleString('en-US')} values from {periodLabel(preview.first_ts, spec.freq)} to {periodLabel(preview.last_ts, spec.freq)} · latest <span className="num">{formatValue(preview.value.at(-1), spec.unit)}</span>
                    {preview.partial ? ' · chart shows the most recent 200' : ''}
                  </>
                ) : spec.provider === 'manual' ? (
                  'No values yet — you can save now and add values later from the series page.'
                ) : (
                  'The source returned no values for this series.'
                )}
                {preview.skipped ? <span className="warn-text"> · {preview.skipped} line(s) could not be read and will be skipped</span> : null}
              </div>
            </div>
            {points.length > 1 && <SeriesLineChart data={points} height={220} precision={chartPrecision(spec.unit, preview.value.at(-1))} />}
            {err && <div className="notice err">{err}</div>}
            <div className="row">
              <button onClick={() => setStep(2)}>Back</button>
              <span className="spacer" />
              <button className="primary" disabled={busy || (preview.n === 0 && spec.provider !== 'manual')} onClick={() => save.mutate()}>
                {save.isPending ? 'Saving and loading history…' : already ? 'Save changes' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
