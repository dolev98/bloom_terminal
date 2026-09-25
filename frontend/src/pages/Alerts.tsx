import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { useProfiles, useWatchlists } from '../lib/hooks'
import { toast } from '../lib/toast'
import { Card, ErrorBox, Loading, Page } from '../ui'
import Inbox from '../components/alerts/Inbox'
import RulesTable from '../components/alerts/RulesTable'
import RuleForm from '../components/alerts/RuleForm'
import type { CatalogItem } from '../components/alerts/RuleForm'
import { SERIES_UNITS, isSkip, reasonText } from '../components/alerts/model'
import type { Ctx, EvalResponse, Fired, Rule, RuleState, RuleTypes } from '../components/alerts/model'

type Secrets = { secrets: Record<string, { set: boolean }> }

export default function AlertsPage() {
  const qc = useQueryClient()
  const formRef = useRef<HTMLDivElement>(null)
  const [editing, setEditing] = useState<Rule | null>(null)
  const [lastEval, setLastEval] = useState<{ at: string; r: EvalResponse } | null>(null)

  const types = useQuery({ queryKey: ['alert-types'], queryFn: () => api<RuleTypes>('/api/alerts/rule-types'), staleTime: 3600_000 })
  const rules = useQuery({ queryKey: ['alert-rules'], queryFn: () => api<Rule[]>('/api/alerts/rules') })
  const states = useQuery({ queryKey: ['alert-state'], queryFn: () => api<RuleState[]>('/api/alerts/state'), refetchInterval: 60_000 })
  const history = useQuery({ queryKey: ['alert-history'], queryFn: () => api<Fired[]>('/api/alerts/history?limit=100'), refetchInterval: 60_000 })
  const digest = useQuery({ queryKey: ['alert-digest'], queryFn: () => api<unknown[]>('/api/alerts/digest'), refetchInterval: 120_000 })
  const settings = useQuery({ queryKey: ['sys-settings'], queryFn: () => api<Secrets>('/api/sys/settings'), staleTime: 300_000 })
  const telegramReady = settings.data ? !!(settings.data.secrets.telegram_bot_token?.set && settings.data.secrets.telegram_chat_id?.set) : undefined

  const lists = useWatchlists()
  const watch = useMemo(() => [...new Set((lists.data ?? []).flatMap((w) => w.items.map((i) => i.ticker.toUpperCase())))], [lists.data])
  const ruleTickers = useMemo(() => [...new Set([...watch, ...(rules.data ?? []).map((r) => r.ticker).filter((t) => t && t !== 'ALL'), ...(history.data ?? []).map((h) => h.ticker)])], [watch, rules.data, history.data])
  const profiles = useProfiles(ruleTickers)
  const seriesIds = useMemo(() => [...new Set((rules.data ?? []).map((r) => r.series_id).filter(Boolean) as string[])], [rules.data])
  const catalog = useQuery({ queryKey: ['catalog-all'], queryFn: () => api<{ items: CatalogItem[] }>('/api/catalog'), enabled: seriesIds.length > 0, staleTime: 600_000 })
  const seriesById = useMemo(() => new Map((catalog.data?.items ?? []).map((s) => [s.series_id, s])), [catalog.data])

  const ctx: Ctx = useMemo(
    () => ({
      names: Object.fromEntries(Object.entries(profiles.data ?? {}).map(([t, p]) => [t, p.name])),
      seriesName: (id) => seriesById.get(id)?.name ?? id,
      currency: (t) => {
        const c = profiles.data?.[t]?.currency ?? (t.endsWith('.TA') ? 'ILS' : 'USD')
        return c === 'ILS' ? '₪' : c === 'USD' ? '$' : `${c} `
      },
    }),
    [profiles.data, seriesById],
  )
  const seriesUnit = (id: string | null) => (id ? (SERIES_UNITS[seriesById.get(id)?.unit ?? ''] ?? '') : '')

  const refresh = () => ['alert-rules', 'alert-state', 'alert-history', 'alert-digest'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
  const evaluate = useMutation({
    mutationFn: () => api<EvalResponse>('/api/alerts/evaluate', { method: 'POST' }),
    onSuccess: (r) => {
      setLastEval({ at: new Date().toISOString(), r })
      const skipped = r.results.filter((x) => x.action === 'skip' || x.action === 'error').length
      toast(
        `Checked ${r.rules} alert${r.rules === 1 ? '' : 's'}: ${r.fired ? `${r.fired} triggered` : 'nothing triggered'}${skipped ? `, ${skipped} could not be checked` : ''}.`,
        r.fired ? 'ok' : 'info',
      )
      refresh()
    },
    onError: (e) => toast(`Could not check the alerts: ${String((e as Error).message ?? e)}`, 'err'),
  })
  const toggle = useMutation({ mutationFn: (r: Rule) => api(`/api/alerts/rules/${r.id}`, { method: 'PATCH', json: { enabled: !r.enabled } }), onSuccess: refresh })
  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/alerts/rules/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast('Alert deleted', 'info')
      refresh()
    },
  })
  const ack = useMutation({ mutationFn: (id: number) => api(`/api/alerts/history/${id}/ack`, { method: 'POST' }), onSuccess: refresh })
  const snooze = useMutation({ mutationFn: (id: number) => api(`/api/alerts/history/${id}/snooze`, { method: 'POST', json: { hours: 24 } }), onSuccess: refresh })
  const flush = useMutation({
    mutationFn: () => api('/api/alerts/digest/flush', { method: 'POST' }),
    onSuccess: () => {
      toast('Held Telegram messages sent', 'ok')
      refresh()
    },
  })

  const edit = (r: Rule | null) => {
    setEditing(r)
    setTimeout(() => formRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
  }
  const skippedNow = (lastEval?.r.results ?? []).filter((x) => x.action === 'skip' && isSkip(x.reason))
  const held = digest.data?.length ?? 0

  return (
    <Page
      title="Alerts"
      sub="Rules that watch prices, fair values and data series and tell you when a condition becomes true."
      actions={
        <button onClick={() => evaluate.mutate()} disabled={evaluate.isPending || !rules.data?.length} title="Check every active rule against the latest prices now">
          {evaluate.isPending ? 'Checking…' : 'Check now'}
        </button>
      }
    >
      <div className="stack" style={{ maxWidth: 1100 }}>
        {lastEval && (
          <div className="notice info">
            <div className="grow">
              Checked {lastEval.r.rules} alert{lastEval.r.rules === 1 ? '' : 's'} just now{lastEval.r.evaluated > lastEval.r.rules ? ` (${lastEval.r.evaluated} company checks)` : ''}: {lastEval.r.fired ? `${lastEval.r.fired} triggered` : 'nothing triggered'}.
              {skippedNow.length > 0 && <> {skippedNow.length === 1 ? `One was skipped — ${reasonText(skippedNow[0].reason)?.text.replace(/^Skipped: /, '')}` : `${skippedNow.length} were skipped; the reasons are shown under each rule.`}</>}
            </div>
            <button className="ghost" onClick={() => setLastEval(null)} aria-label="Dismiss">
              ✕
            </button>
          </div>
        )}

        <Card
          title="Recent alerts"
          hint="Newest first · times are Israel time"
          actions={
            held > 0 ? (
              <button onClick={() => flush.mutate()} disabled={flush.isPending} title="Messages that fired during quiet hours wait for the 09:00 digest">
                Send {held} held message{held === 1 ? '' : 's'} now
              </button>
            ) : undefined
          }
        >
          {history.isLoading && <Loading lines={2} />}
          {history.error && <ErrorBox error={history.error} what="recent alerts" />}
          {history.data && <Inbox items={history.data} ctx={ctx} onAck={(id) => ack.mutate(id)} onSnooze={(id) => snooze.mutate(id)} busy={ack.isPending || snooze.isPending} />}
        </Card>

        <Card
          title="Your alert rules"
          hint={states.data?.length ? 'Status is from the latest check' : undefined}
          foot="Only live, licensed prices trigger alerts unless a rule allows delayed quotes. A price older than 15 minutes while its market is open is skipped rather than trusted."
        >
          {rules.isLoading && <Loading lines={3} />}
          {rules.error && <ErrorBox error={rules.error} what="your alert rules" />}
          {rules.data && (
            <RulesTable
              rules={rules.data}
              states={states.data ?? []}
              evals={lastEval?.r.results ?? []}
              evalAt={lastEval?.at ?? null}
              ctx={ctx}
              seriesUnit={seriesUnit}
              onEdit={(r) => edit(r)}
              onToggle={(r) => toggle.mutate(r)}
              onDelete={(r) => window.confirm('Delete this alert rule? Past alerts stay under Recent alerts.') && remove.mutate(r.id)}
              onCreate={() => edit(null)}
            />
          )}
        </Card>

        <div ref={formRef}>
          <RuleForm
            types={types.data}
            editing={editing}
            watch={watch}
            ctx={ctx}
            telegramReady={telegramReady}
            onSaved={() => {
              setEditing(null)
              refresh()
            }}
            onCancel={() => setEditing(null)}
          />
        </div>
      </div>
    </Page>
  )
}
