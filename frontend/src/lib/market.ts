/** Regular trading hours only. Exchange holidays come from the calendar (see useHolidays in App). */
export function usMarketOpen(d = new Date()) {
  const p = parts(d, 'America/New_York')
  if (p.wd === 0 || p.wd === 6) return false
  const m = p.h * 60 + p.m
  return m >= 9 * 60 + 30 && m < 16 * 60
}
export function taseMarketOpen(d = new Date()) {
  const p = parts(d, 'Asia/Jerusalem')
  // TASE trades Mon–Fri since Jan 2026 (verified from daily bars); Friday is a short session (close time approximate)
  if (p.wd === 0 || p.wd === 6) return false
  const m = p.h * 60 + p.m
  if (p.wd === 5) return m >= 9 * 60 + 59 && m < 14 * 60
  return m >= 9 * 60 + 59 && m < 17 * 60 + 25
}
export function localDate(tz: string, d = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d)
}
function parts(d: Date, tz: string) {
  const f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hour12: false, weekday: 'short', hour: '2-digit', minute: '2-digit' })
  const map: Record<string, string> = {}
  for (const x of f.formatToParts(d)) map[x.type] = x.value
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(map.weekday)
  return { wd, h: Number(map.hour) % 24, m: Number(map.minute) }
}
