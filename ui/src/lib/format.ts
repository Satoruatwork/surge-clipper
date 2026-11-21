export function timecode(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return '0:00'
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}` : `${m}:${String(r).padStart(2, '0')}`
}

export function compact(n: number | null | undefined): string {
  const v = n ?? 0
  if (v >= 1e9) return `${(v / 1e9).toFixed(v >= 1e10 ? 0 : 1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}M`
  if (v >= 1e4) return `${Math.round(v / 1e3)}K`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return String(Math.round(v))
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return ''
  const d = Date.now() / 1000 - ts
  if (d < 45) return 'just now'
  if (d < 3600) return `${Math.round(d / 60)} min ago`
  if (d < 86400) return `${Math.round(d / 3600)} h ago`
  return `${Math.round(d / 86400)} d ago`
}

export function until(ts: number | null | undefined): string {
  if (!ts) return ''
  const d = ts - Date.now() / 1000
  if (d <= 60) return 'now'
  if (d < 3600) return `in ${Math.round(d / 60)} min`
  if (d < 86400) return `in ${Math.floor(d / 3600)} h ${Math.round((d % 3600) / 60)} min`
  return `in ${Math.round(d / 86400)} d`
}

export function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
}

export function dayClock(ts: number): string {
  const d = new Date(ts * 1000)
  const today = new Date()
  const tomorrow = new Date()
  tomorrow.setDate(today.getDate() + 1)
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  const day = same(d, today) ? 'Today' : same(d, tomorrow) ? 'Tomorrow' : d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
  return `${day}, ${clock(ts)}`
}

export function toLocalInput(ts: number): string {
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function fromLocalInput(v: string): number {
  return new Date(v).getTime() / 1000
}

export const platformName: Record<string, string> = {
  twitch: 'Twitch', kick: 'Kick', youtube: 'YouTube', file: 'Local file', instagram: 'Instagram',
}

export const chatClassLabel: Record<string, string> = {
  funny: 'Laughing', hype: 'Hyped', shock: 'Shocked', fail: 'Fail', clip: 'Clip requests',
}
