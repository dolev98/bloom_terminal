export default function Sparkline({ values, width = 80, height = 20, color, fill = true }: { values: (number | null | undefined)[]; width?: number; height?: number; color?: string; fill?: boolean }) {
  const v = values.filter((x): x is number => typeof x === 'number' && !Number.isNaN(x))
  if (v.length < 2) return <svg className="sparkline" width={width} height={height} />
  const min = Math.min(...v)
  const max = Math.max(...v)
  const span = max - min || 1
  const step = width / (v.length - 1)
  const pts = v.map((x, i) => `${(i * step).toFixed(1)},${(height - 1 - ((x - min) / span) * (height - 2)).toFixed(1)}`)
  const c = color ?? (v[v.length - 1] >= v[0] ? 'var(--green)' : 'var(--red)')
  return (
    <svg className="sparkline" width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      {fill && <polygon points={`0,${height} ${pts.join(' ')} ${width},${height}`} fill={c} opacity={0.12} />}
      <polyline points={pts.join(' ')} fill="none" stroke={c} strokeWidth={1.4} />
    </svg>
  )
}
