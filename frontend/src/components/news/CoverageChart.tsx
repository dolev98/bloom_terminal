import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { dateLabel } from '../../lib/format'
import EChart, { AXIS_STYLE } from '../charts/EChart'
import type { EChartsOption } from '../charts/EChart'
import { Card, Empty, ErrorBox, Help, Loading } from '../../ui'
import type { NewsStats } from './model'

const DAYS = 30

/** Daily article count (bars) and average tone (line) for one ticker over the last 30 days. Days without
 *  articles are shown as zero; the tone line has gaps on those days. */
export default function CoverageChart({ ticker }: { ticker: string }) {
  const q = useQuery({ queryKey: ['news-stats', ticker, DAYS], queryFn: () => api<NewsStats>(`/api/news/stats?ticker=${encodeURIComponent(ticker)}&days=${DAYS}`), staleTime: 300_000 })
  const model = useMemo(() => {
    if (!q.data) return null
    const byDay = new Map(q.data.ts.map((t, i) => [t.slice(0, 10), { n: q.data!.count[i], s: q.data!.sentiment[i] }]))
    const days: string[] = []
    const today = new Date()
    for (let i = DAYS - 1; i >= 0; i--) days.push(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i)).toISOString().slice(0, 10))
    const count = days.map((d) => byDay.get(d)?.n ?? 0)
    const tone = days.map((d) => {
      const s = byDay.get(d)?.s
      return s === null || s === undefined ? null : Math.round(s * 100) / 100
    })
    const first = q.data.ts.length ? q.data.ts[0].slice(0, 10) : null
    const total = count.reduce((a, b) => a + b, 0)
    return { days, count, tone, first, total }
  }, [q.data])

  const option = useMemo<EChartsOption | null>(() => {
    if (!model) return null
    const label = (d: string) => new Date(`${d}T12:00:00Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
    return {
      grid: { left: 34, right: 34, top: 14, bottom: 24 },
      legend: { show: false },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1b2029',
        borderColor: '#343c4a',
        textStyle: { color: '#e7eaf0', fontSize: 12 },
        formatter: (ps: any) => {
          const i = ps[0]?.dataIndex ?? 0
          const s = model.tone[i]
          const toneTxt = s === null ? 'no articles' : `tone ${s > 0 ? '+' : ''}${s.toFixed(2)}`
          return `${label(model.days[i])}<br/>${model.count[i]} article${model.count[i] === 1 ? '' : 's'} · ${toneTxt}`
        },
      },
      xAxis: { type: 'category', data: model.days.map(label), ...AXIS_STYLE, axisLabel: { color: '#8a93a3', fontSize: 10, interval: 6 }, axisTick: { show: false } },
      yAxis: [
        { type: 'value', minInterval: 1, ...AXIS_STYLE, splitLine: { lineStyle: { color: '#262d38' } }, axisLabel: { color: '#8a93a3', fontSize: 10 } },
        { type: 'value', min: -1, max: 1, interval: 1, position: 'right', ...AXIS_STYLE, splitLine: { show: false }, axisLabel: { color: '#8a93a3', fontSize: 10, formatter: (v: number) => (v > 0 ? `+${v}` : `${v}`) } },
      ],
      series: [
        { name: 'Articles', type: 'bar', data: model.count, itemStyle: { color: 'rgba(90,169,255,.55)' }, barMaxWidth: 10 },
        { name: 'Tone', type: 'line', yAxisIndex: 1, data: model.tone, connectNulls: false, symbol: 'circle', symbolSize: 5, lineStyle: { width: 1.5, color: '#f5a524' }, itemStyle: { color: '#f5a524' } },
      ],
    }
  }, [model])

  return (
    <Card
      title="Coverage, last 30 days"
      actions={<Help text="Bars: articles per day that mention the company. Line: their average tone from −1 (negative) to +1 (positive), estimated automatically from the wording." />}
      foot={
        model && model.total > 0
          ? `Blue bars: articles per day (left scale). Orange line: average tone (right scale).${model.first && model.first > model.days[2] ? ` News collection for ${ticker} started ${dateLabel(model.first)}.` : ''}`
          : undefined
      }
    >
      {q.isLoading && <Loading lines={3} />}
      {q.error && <ErrorBox error={q.error} what="news coverage" />}
      {model && model.total === 0 && <Empty title="No articles in the last 30 days">No news source has mentioned {ticker} yet.</Empty>}
      {option && model && model.total > 0 && <EChart option={option} height={170} />}
    </Card>
  )
}
