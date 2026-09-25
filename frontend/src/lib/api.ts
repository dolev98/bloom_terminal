export async function api<T = any>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) }
  let body = init?.body
  if (init?.json !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(init.json)
  }
  const res = await fetch(path, { ...init, headers, body })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail ?? JSON.stringify(j)
    } catch {}
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json()
}

export type SeriesSpec = {
  series_id: string
  provider: string
  provider_key: string
  field_name?: string | null
  name: string
  description?: string
  freq: string
  unit: string
  unit_mult?: number
  sa?: boolean
  value_kind: string
  default_transform: string
  country?: string | null
  category: string
  tags: string[]
  publication_lag_days?: number
  plausible_min?: number | null
  plausible_max?: number | null
  license_note?: string | null
  formula?: string | null
  enabled: boolean
  params?: Record<string, unknown>
  meta?: Record<string, any>
}

export type Observations = {
  series_id: string
  name: string
  unit: string
  freq: string
  transform: string
  n: number
  ts: string[]
  value: number[]
  meta?: Record<string, any> | null
  attribution?: string | null
}

export const fmt = {
  num(v: number | null | undefined, d = 2) {
    if (v === null || v === undefined || Number.isNaN(v)) return '—'
    return v.toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 })
  },
  ts(s: string | null | undefined) {
    if (!s) return '—'
    return s.replace('T', ' ').slice(0, 16)
  },
  ago(s: string | null | undefined) {
    if (!s) return 'never'
    const ms = Date.now() - new Date(s + (s.endsWith('Z') ? '' : 'Z')).getTime()
    const m = Math.round(ms / 60000)
    if (m < 60) return `${m}m ago`
    const h = Math.round(m / 60)
    if (h < 48) return `${h}h ago`
    return `${Math.round(h / 24)}d ago`
  },
}
