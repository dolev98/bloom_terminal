/** Scheduler jobs in plain words (Status page). */

export type JobRunInfo = { started_at: string; finished_at: string | null; status: string; message: string | null; duration_s: number | null }
export type ScheduledJob = { id: string; name: string; next_run: string | null; trigger: string; running: boolean; last_run?: JobRunInfo | null; last_ok_at?: string | null; last_work_at?: string | null }
export type JobsResponse = { scheduled: ScheduledJob[]; runs: (JobRunInfo & { id: number; job_id: string })[] }

/** "interval[0:30:00]" → "Every 30 minutes"; "cron[hour='1', minute='15']" → "Daily at 01:15". */
export function scheduleText(trigger: string): string {
  const iv = trigger.match(/^interval\[(?:(\d+) days?, )?(\d+):(\d\d):(\d\d)\]$/)
  if (iv) {
    const d = Number(iv[1] ?? 0)
    const h = Number(iv[2])
    const m = Number(iv[3])
    const s = Number(iv[4])
    if (d) return d === 1 ? 'Every day' : `Every ${d} days`
    if (h && !m) return h === 1 ? 'Every hour' : `Every ${h} hours`
    if (!h && m) return m === 1 ? 'Every minute' : `Every ${m} minutes`
    if (!h && !m && s) return `Every ${s} seconds`
    return `Every ${h} h ${m} min`
  }
  const cron = trigger.match(/^cron\[(.*)\]$/)
  if (cron) {
    const f: Record<string, string> = {}
    for (const part of cron[1].split(/,\s*/)) {
      const [k, v] = part.split('=')
      if (k && v) f[k.trim()] = v.replace(/'/g, '').trim()
    }
    const pad = (x?: string) => (x ?? '0').padStart(2, '0')
    const at = f.hour && /^\d+$/.test(f.hour) ? ` at ${pad(f.hour)}:${pad(f.minute)}` : ''
    const days = f.day_of_week ? (f.day_of_week === 'mon-fri' ? 'Weekdays' : f.day_of_week === 'sun-thu' ? 'Sunday to Thursday' : `On ${f.day_of_week}`) : 'Daily'
    if (at) return `${days}${at}`
  }
  return trigger
}

export function lastRunOf(j: JobsResponse, id: string): JobRunInfo | null {
  const s = j.scheduled.find((x) => x.id === id)
  return s?.last_run ?? j.runs.find((r) => r.job_id === id) ?? null
}
/** Last successful run; `undefined` means unknown (an older backend without `last_ok_at` and no recent run listed). */
export function lastOkOf(j: JobsResponse, id: string): string | null | undefined {
  const s = j.scheduled.find((x) => x.id === id)
  const recent = j.runs.find((r) => r.job_id === id && r.status === 'ok')?.finished_at
  if (s && 'last_ok_at' in s) return s.last_ok_at ?? recent ?? null
  return recent ?? undefined
}
/** Last successful run that actually did something (jobs return "" when there was nothing to do). */
export function lastWorkOf(j: JobsResponse, id: string): string | null {
  const s = j.scheduled.find((x) => x.id === id)
  if (s && 'last_work_at' in s) return s.last_work_at ?? null
  return j.runs.find((r) => r.job_id === id && r.status === 'ok' && r.message)?.finished_at ?? null
}

export const STATUS_WORD: Record<string, [string, string]> = {
  ok: ['OK', 'ok'],
  error: ['Failed', 'err'],
  skipped: ['Skipped', ''],
  running: ['Running', 'info'],
}
