import { useMemo } from 'react'
import EChart, { type EChartsOption } from '../charts/EChart'
import { Card } from '../../ui'
import { money, ratioPct } from '../../lib/format'
import { VIZ, axisCat, axisVal, barData, compactMoney, vizBase } from './shared'
import { periodShort, type StatementsResp } from './model'

type SeriesDef = { name: string; values: (number | null)[]; color: string; kind: 'money' | 'pct' }

function tooltipFor(labels: string[], defs: SeriesDef[], currency: string) {
  return (params: any) => {
    const list = Array.isArray(params) ? params : [params]
    const i = list[0]?.dataIndex ?? 0
    const rows = defs
      .map((d) => {
        const v = d.values[i]
        const text = d.kind === 'pct' ? ratioPct(v === null ? null : v / 100) : money(v, currency)
        return `<div style="display:flex;gap:14px;justify-content:space-between"><span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${d.color};margin-right:6px"></span>${d.name}</span><b style="font-variant-numeric:tabular-nums">${text}</b></div>`
      })
      .join('')
    return `<div style="margin-bottom:4px;color:${VIZ.muted}">${labels[i]}</div>${rows}`
  }
}

function barOption(labels: string[], defs: SeriesDef[], currency: string): EChartsOption {
  return {
    ...vizBase,
    legend: { ...vizBase.legend, data: defs.map((d) => d.name) },
    tooltip: { ...vizBase.tooltip, axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,255,255,0.04)' } }, formatter: tooltipFor(labels, defs, currency) },
    grid: { left: 52, right: 8, top: 32, bottom: 26 },
    xAxis: { type: 'category', data: labels, ...axisCat },
    yAxis: { type: 'value', ...axisVal, axisLabel: { ...axisVal.axisLabel, formatter: (v: number) => compactMoney(v) } },
    series: defs.map((d) => ({
      name: d.name,
      type: 'bar' as const,
      data: barData(d.values),
      itemStyle: { color: d.color, borderRadius: [3, 3, 0, 0] },
      barMaxWidth: 18,
      barGap: '12%',
      barCategoryGap: '28%',
    })),
  } as EChartsOption
}

/** Three charts for the last 8 periods: revenue & profit (with operating margin in its own strip, not a second
 * y-axis), cash flow, balance sheet. */
export default function FinancialCharts({ data }: { data: StatementsResp }) {
  const cur = data.currency
  const opts = useMemo(() => {
    const labels = data.periods.map(periodShort)
    const f = (k: string) => data.fields[k] ?? labels.map(() => null)
    const rev = f('revenue')
    const ni = f('net_income')
    const oi = f('operating_income')
    const margin = rev.map((r, i) => (r && oi[i] !== null && oi[i] !== undefined ? ((oi[i] as number) / r) * 100 : null))
    const profitDefs: SeriesDef[] = [
      { name: 'Revenue', values: rev, color: VIZ.blue, kind: 'money' },
      { name: 'Net income', values: ni, color: VIZ.amber, kind: 'money' },
      { name: 'Operating margin', values: margin, color: VIZ.violet, kind: 'pct' },
    ]
    const profit: EChartsOption = {
      ...vizBase,
      legend: { ...vizBase.legend, data: profitDefs.map((d) => d.name) },
      tooltip: { ...vizBase.tooltip, axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,255,255,0.04)' } }, formatter: tooltipFor(labels, profitDefs, cur) },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 52, right: 8, top: 32, height: '52%' },
        { left: 52, right: 8, top: '76%', bottom: 26 },
      ],
      xAxis: [
        { type: 'category', data: labels, gridIndex: 0, ...axisCat, axisLabel: { show: false } },
        { type: 'category', data: labels, gridIndex: 1, ...axisCat },
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, ...axisVal, axisLabel: { ...axisVal.axisLabel, formatter: (v: number) => compactMoney(v) } },
        {
          type: 'value',
          gridIndex: 1,
          ...axisVal,
          scale: true,
          splitNumber: 2,
          name: 'Op. margin',
          nameLocation: 'end',
          nameGap: 6,
          nameTextStyle: { color: VIZ.muted, fontSize: 10, align: 'left' },
          axisLabel: { ...axisVal.axisLabel, formatter: (v: number) => `${Math.round(v)}%` },
        },
      ],
      series: [
        { name: 'Revenue', type: 'bar', data: barData(rev), itemStyle: { color: VIZ.blue, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 20, barGap: '12%' },
        { name: 'Net income', type: 'bar', data: barData(ni), itemStyle: { color: VIZ.amber, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 20 },
        { name: 'Operating margin', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: margin, lineStyle: { width: 2, color: VIZ.violet }, itemStyle: { color: VIZ.violet }, symbolSize: 7, connectNulls: false },
      ],
    } as EChartsOption
    const capex = f('capex')
    const cash = barOption(
      labels,
      [
        { name: 'Cash from operations', values: f('cfo'), color: VIZ.blue, kind: 'money' },
        { name: 'Capital expenditure', values: capex, color: VIZ.violet, kind: 'money' },
        { name: 'Free cash flow', values: f('fcf'), color: VIZ.amber, kind: 'money' },
      ],
      cur,
    )
    const balance = barOption(
      labels,
      [
        { name: 'Total assets', values: f('total_assets'), color: VIZ.blue, kind: 'money' },
        { name: 'Total liabilities', values: f('total_liabilities'), color: VIZ.violet, kind: 'money' },
        { name: 'Total equity', values: f('total_equity'), color: VIZ.amber, kind: 'money' },
      ],
      cur,
    )
    return { profit, cash, balance }
  }, [data, cur])

  const unit = cur ? `${cur}` : ''
  return (
    <div className="grid-3">
      <Card title="Revenue and profit" hint={unit}>
        <EChart option={opts.profit} height={250} />
      </Card>
      <Card title="Cash flow" hint={unit}>
        <EChart option={opts.cash} height={250} />
      </Card>
      <Card title="Balance sheet" hint={`${unit}, at period end`}>
        <EChart option={opts.balance} height={250} />
      </Card>
    </div>
  )
}
