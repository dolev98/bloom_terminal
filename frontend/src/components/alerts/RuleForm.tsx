import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { useQuotesREST } from '../../lib/hooks'
import { dateLabel, price as fmtPrice } from '../../lib/format'
import { toast } from '../../lib/toast'
import { Card, Help } from '../../ui'
import { CONDITIONS, REFERENCES, SERIES_UNITS, condition, fmtValue, ruleSentence } from './model'
import type { Ctx, Rule, RuleTypes } from './model'

export type CatalogItem = { series_id: string; name: string; unit: string; meta?: { n_obs?: number; last_value?: number | null; last_ts?: string | null } | null }

type Form = {
  subject: string // 'ALL' | ticker | '__other'
  other: string
  rule_type: string
  reference: string
  series_id: string
  direction: 'above' | 'below'
  threshold: string
  telegram: boolean
  ops: boolean
  name: string
  hysteresis: string
  cooldown: string
  cap: string
  quietOn: boolean
  quietStart: string
  quietEnd: string
  allowGrey: boolean
}

const OTHER = '__other'

function fromRule(r: Rule, watch: string[]): Form {
  const quietOff = !!r.quiet_hours && !r.quiet_hours.start
  return {
    subject: r.ticker === 'ALL' || !r.ticker ? 'ALL' : watch.includes(r.ticker) ? r.ticker : OTHER,
    other: r.ticker === 'ALL' || watch.includes(r.ticker) ? '' : r.ticker,
    rule_type: r.rule_type,
    reference: r.reference || 'base_dcf',
    series_id: r.series_id ?? '',
    direction: r.params?.direction === 'below' ? 'below' : 'above',
    threshold: String(r.threshold),
    telegram: r.channels.includes('telegram'),
    ops: r.channels.includes('ops'),
    name: r.name ?? '',
    hysteresis: String(r.hysteresis_pp),
    cooldown: String(r.cooldown_hours),
    cap: String(r.daily_cap),
    quietOn: !quietOff,
    quietStart: r.quiet_hours?.start || '22:00',
    quietEnd: r.quiet_hours?.end || '07:00',
    allowGrey: !!r.params?.allow_grey,
  }
}

function blank(types: RuleTypes | undefined, watch: string[]): Form {
  const d = types?.defaults
  return {
    subject: watch.includes('AAPL') ? 'AAPL' : (watch[0] ?? 'ALL'),
    other: '',
    rule_type: 'upside_gt',
    reference: 'base_dcf',
    series_id: '',
    direction: 'above',
    threshold: String(types?.rule_types.upside_gt?.default_threshold ?? 25),
    telegram: true,
    ops: false,
    name: '',
    hysteresis: String(d?.hysteresis_pp ?? 5),
    cooldown: String(d?.cooldown_hours ?? 24),
    cap: String(d?.daily_cap ?? 3),
    quietOn: true,
    quietStart: d?.quiet_hours.start ?? '22:00',
    quietEnd: d?.quiet_hours.end ?? '07:00',
    allowGrey: false,
  }
}

/** "Notify me when [AAPL] [upside to fair value is above] [25] %" — the rule reads as the sentence it builds. */
export default function RuleForm({
  types,
  editing,
  watch,
  ctx,
  telegramReady,
  onSaved,
  onCancel,
}: {
  types: RuleTypes | undefined
  editing: Rule | null
  watch: string[]
  ctx: Ctx
  telegramReady: boolean | undefined
  onSaved: () => void
  onCancel: () => void
}) {
  const [f, setF] = useState<Form>(() => (editing ? fromRule(editing, watch) : blank(types, watch)))
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    setF(editing ? fromRule(editing, watch) : blank(types, watch))
    setErr(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing?.id, types ? 1 : 0, watch.join()])

  const cond = condition(f.rule_type) ?? CONDITIONS[0]
  const isSeries = f.rule_type === 'series_threshold'
  const ticker = f.subject === OTHER ? f.other.trim().toUpperCase() : f.subject
  const catalog = useQuery({ queryKey: ['catalog-all'], queryFn: () => api<{ items: CatalogItem[] }>('/api/catalog'), enabled: isSeries, staleTime: 600_000 })
  const seriesList = useMemo(() => (catalog.data?.items ?? []).filter((s) => (s.meta?.n_obs ?? 0) > 0).sort((a, b) => a.name.localeCompare(b.name)), [catalog.data])
  const series = seriesList.find((s) => s.series_id === f.series_id)
  const seriesUnit = series ? (SERIES_UNITS[series.unit] ?? '') : ''
  const quote = useQuotesREST(cond.unit === 'price' && ticker && ticker !== 'ALL' ? [ticker] : [])
  const now = quote.data?.[0]

  const set = (patch: Partial<Form>) => setF((x) => ({ ...x, ...patch }))
  const pickCondition = (t: string) => {
    const c = condition(t)!
    const patch: Partial<Form> = { rule_type: t, threshold: c.unit === 'price' ? '' : String(types?.rule_types[t]?.default_threshold ?? 0) }
    if (!c.allowAll && f.subject === 'ALL') patch.subject = watch[0] ?? OTHER
    set(patch)
  }

  const body = () => {
    const thr = cond.noThreshold ? 0 : Number(f.threshold)
    if (!isSeries && (!ticker || (ticker === 'ALL' && !cond.allowAll))) throw new Error('Choose a company.')
    if (isSeries && !f.series_id) throw new Error('Choose a data series.')
    if (!cond.noThreshold && (f.threshold.trim() === '' || !Number.isFinite(thr))) throw new Error('Enter a number for the threshold.')
    const n = (s: string, fallback: number) => (s.trim() === '' || !Number.isFinite(Number(s)) ? fallback : Number(s))
    return {
      name: f.name.trim(),
      ticker: isSeries ? 'ALL' : ticker,
      rule_type: f.rule_type,
      reference: cond.needsRef ? f.reference : 'base_dcf',
      series_id: isSeries ? f.series_id : null,
      threshold: thr,
      hysteresis_pp: n(f.hysteresis, 5),
      cooldown_hours: n(f.cooldown, 24),
      daily_cap: Math.max(1, Math.round(n(f.cap, 3))),
      channels: ['inapp', ...(f.telegram ? ['telegram'] : []), ...(f.ops ? ['ops'] : [])],
      quiet_hours: f.quietOn ? { start: f.quietStart, end: f.quietEnd } : { start: '', end: '' },
      params: { ...(editing?.params ?? {}), allow_grey: f.allowGrey, ...(isSeries ? { direction: f.direction } : {}) },
      enabled: editing?.enabled ?? true,
    }
  }
  const save = useMutation({
    mutationFn: () => {
      const b = body()
      return editing ? api<Rule>(`/api/alerts/rules/${editing.id}`, { method: 'PUT', json: b }) : api<Rule>('/api/alerts/rules', { method: 'POST', json: b })
    },
    onSuccess: () => {
      toast(editing ? 'Alert updated' : 'Alert created', 'ok')
      setF(blank(types, watch))
      setErr(null)
      onSaved()
    },
    onError: (e) => setErr(String((e as Error).message ?? e)),
  })

  let preview = ''
  try {
    const b = body()
    preview = ruleSentence({ ticker: b.ticker, rule_type: b.rule_type, reference: b.reference, series_id: b.series_id, threshold: b.threshold, params: b.params }, ctx, seriesUnit)
  } catch {
    preview = ''
  }
  const unitBefore = cond.unit === 'price' ? ctx.currency(ticker) : ''
  const unitAfter = cond.unit === '%' ? '%' : cond.unit === 'level' ? seriesUnit : ''

  return (
    <Card title={editing ? 'Edit alert' : 'New alert'} hint={editing ? 'Changes apply from the next check' : undefined}>
      <div className="stack" style={{ gap: 14 }}>
        <div className="row" style={{ gap: 8, fontSize: 14.5 }}>
          <span>Notify me when</span>
          {isSeries ? (
            <select value={f.series_id} onChange={(e) => set({ series_id: e.target.value })} style={{ maxWidth: 320 }} aria-label="Data series">
              <option value="">{catalog.isLoading ? 'Loading series…' : 'Choose a data series…'}</option>
              {seriesList.map((s) => (
                <option key={s.series_id} value={s.series_id}>
                  {s.name}
                </option>
              ))}
            </select>
          ) : (
            <select value={f.subject} onChange={(e) => set({ subject: e.target.value })} aria-label="Company">
              {cond.allowAll && <option value="ALL">any watchlist company</option>}
              {watch.map((t) => (
                <option key={t} value={t}>
                  {t}
                  {ctx.names[t] ? ` — ${ctx.names[t]}` : ''}
                </option>
              ))}
              <option value={OTHER}>another ticker…</option>
            </select>
          )}
          {!isSeries && f.subject === OTHER && <input className="ticker" placeholder="Ticker, e.g. TEVA.TA" value={f.other} onChange={(e) => set({ other: e.target.value.toUpperCase() })} style={{ width: 150 }} aria-label="Ticker" />}
          <select value={f.rule_type} onChange={(e) => pickCondition(e.target.value)} aria-label="Condition">
            {CONDITIONS.filter((c) => !c.more).map((c) => (
              <option key={c.type} value={c.type}>
                {c.type === 'series_threshold' ? 'a data series crosses a level' : c.label}
              </option>
            ))}
            <optgroup label="More conditions">
              {CONDITIONS.filter((c) => c.more).map((c) => (
                <option key={c.type} value={c.type}>
                  {c.label}
                </option>
              ))}
            </optgroup>
          </select>
          {isSeries && (
            <select value={f.direction} onChange={(e) => set({ direction: e.target.value as 'above' | 'below' })} aria-label="Direction">
              <option value="above">rises above</option>
              <option value="below">falls below</option>
            </select>
          )}
          {!cond.noThreshold && (
            <span className="row nowrap" style={{ gap: 4 }}>
              {unitBefore && <span className="muted">{unitBefore}</span>}
              <input className="num" inputMode="decimal" value={f.threshold} placeholder={cond.unit === 'price' && now ? fmtPrice(now.last) : ''} onChange={(e) => set({ threshold: e.target.value })} style={{ width: 90, textAlign: 'right' }} aria-label="Threshold" />
              {unitAfter && <span className="muted">{unitAfter}</span>}
            </span>
          )}
          {cond.help && <Help text={cond.help} />}
        </div>

        {cond.unit === 'price' && now && (
          <div className="small muted" style={{ marginTop: -6 }}>
            {ticker} last traded at {fmtValue('price', now.last, ticker, ctx)}.
          </div>
        )}
        {isSeries && series && (
          <div className="small muted" style={{ marginTop: -6 }}>
            Latest value {fmtValue('level', series.meta?.last_value, '', ctx, seriesUnit)}
            {series.meta?.last_ts ? ` (${dateLabel(series.meta.last_ts)})` : ''} · <span className="mono faint">{series.series_id}</span>
          </div>
        )}
        {cond.needsRef && (
          <div className="row" style={{ gap: 8 }}>
            <span className="muted small">Fair value to compare with</span>
            <select value={REFERENCES.some(([k]) => k === f.reference) ? f.reference : 'custom'} onChange={(e) => set({ reference: e.target.value === 'custom' ? 'model:' : e.target.value })} aria-label="Fair value">
              {REFERENCES.map(([k, l]) => (
                <option key={k} value={k}>
                  {l[0].toUpperCase() + l.slice(1)}
                </option>
              ))}
              <option value="custom">A specific model…</option>
            </select>
            {f.reference.startsWith('model:') && <input className="mono" placeholder="model id" value={f.reference.slice(6)} onChange={(e) => set({ reference: `model:${e.target.value.trim()}` })} style={{ width: 160 }} aria-label="Model id" />}
            <Help text="Which fair value the alert uses. “DCF, base case” is your discounted-cash-flow model; “blended” mixes all your models; “imported” is a fair value you uploaded." />
          </div>
        )}

        <div className="row" style={{ gap: 14 }}>
          <span className="muted small">Send to</span>
          <label className="check">
            <input type="checkbox" checked={f.telegram} onChange={(e) => set({ telegram: e.target.checked })} /> Telegram
          </label>
          <label className="check" title="Every alert is always listed under Recent alerts">
            <input type="checkbox" checked disabled /> Recent alerts on this page (always)
          </label>
          {f.telegram && telegramReady === false && <span className="small warn-text">Telegram is not set up yet — add the bot in Settings.</span>}
        </div>

        <details>
          <summary className="muted small" style={{ cursor: 'pointer' }}>
            Advanced
          </summary>
          <div className="form" style={{ marginTop: 12, gridTemplateColumns: '200px minmax(0, 1fr)' }}>
            <label>Name (optional)</label>
            <div>
              <input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Apple entry point" style={{ width: 260 }} dir="auto" />
              <div className="hint">Shown above the rule; the sentence already says what it does.</div>
            </div>
            <label>Re-arm gap</label>
            <div>
              <input className="num" value={f.hysteresis} onChange={(e) => set({ hysteresis: e.target.value })} style={{ width: 80 }} /> <span className="muted">{cond.unit === '%' ? 'percentage points' : cond.unit === 'price' ? `${ctx.currency(ticker) || 'price units'}` : 'units'}</span>
              <div className="hint">After it fires, the value must move back this far before the alert can fire again — stops repeats when the value hovers around the line.</div>
            </div>
            <label>Cool-down</label>
            <div>
              <input className="num" value={f.cooldown} onChange={(e) => set({ cooldown: e.target.value })} style={{ width: 80 }} /> <span className="muted">hours</span>
              <div className="hint">Minimum time between two alerts from this rule for the same company.</div>
            </div>
            <label>Daily limit</label>
            <div>
              <input className="num" value={f.cap} onChange={(e) => set({ cap: e.target.value })} style={{ width: 80 }} /> <span className="muted">alerts per day</span>
              <div className="hint">The most alerts this rule can send per company in one day (Israel time).</div>
            </div>
            <label>Quiet hours</label>
            <div>
              <label className="check">
                <input type="checkbox" checked={f.quietOn} onChange={(e) => set({ quietOn: e.target.checked })} /> Hold Telegram messages between
              </label>{' '}
              <input value={f.quietStart} onChange={(e) => set({ quietStart: e.target.value })} disabled={!f.quietOn} style={{ width: 70 }} aria-label="Quiet hours start" /> and{' '}
              <input value={f.quietEnd} onChange={(e) => set({ quietEnd: e.target.value })} disabled={!f.quietOn} style={{ width: 70 }} aria-label="Quiet hours end" />
              <div className="hint">Israel time. Messages that fire at night are sent together in the 09:00 morning digest.</div>
            </div>
            <label>Delayed quotes</label>
            <div>
              <label className="check">
                <input type="checkbox" checked={f.allowGrey} onChange={(e) => set({ allowGrey: e.target.checked })} /> Allow delayed quotes
              </label>
              <div className="hint">Let a delayed Yahoo Finance price trigger this alert. Off by default: only live, licensed prices (Finnhub) can.</div>
            </div>
          </div>
        </details>

        {preview && (
          <div className="notice info">
            <div className="grow">
              {preview}.{' '}
              <span className="muted">
                {f.telegram ? 'You’ll get a Telegram message and it will be listed under Recent alerts.' : 'It will be listed under Recent alerts.'}
              </span>
            </div>
          </div>
        )}
        {err && <div className="error">{err}</div>}
        <div className="row">
          <button className="primary" onClick={() => save.mutate()} disabled={save.isPending}>
            {save.isPending ? 'Saving…' : editing ? 'Save changes' : 'Create alert'}
          </button>
          {editing && (
            <button className="ghost" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </div>
    </Card>
  )
}
