import { useEffect, useState } from 'react'
import { AudioLines, BadgeDollarSign, CalendarClock, ChartNoAxesColumn, Clapperboard, MonitorPlay, PlugZap, Settings2, Users } from 'lucide-react'
import { api } from './lib/api'
import { connect, errorText, setState, toast, useStore } from './lib/store'
import { compact } from './lib/format'
import { Toasts } from './components/ui'
import { UpdateBanner } from './components/UpdateBanner'
import { ClipEditor } from './components/ClipEditor'
import { Desk } from './pages/Desk'
import { Clips } from './pages/Clips'
import { Schedule } from './pages/Schedule'
import { Accounts } from './pages/Accounts'
import { Settings } from './pages/Settings'
import { Sounds } from './pages/Sounds'
import { Setup } from './pages/Setup'
import { Campaigns } from './pages/Campaigns'
import { Performance } from './pages/Performance'
import type { Settings as S } from './lib/types'

type Page = 'desk' | 'clips' | 'sounds' | 'schedule' | 'accounts' | 'campaigns' | 'performance' | 'setup' | 'settings'
const PAGES: { id: Page; label: string; icon: typeof MonitorPlay }[] = [
  { id: 'desk', label: 'Desk', icon: MonitorPlay },
  { id: 'clips', label: 'Clips', icon: Clapperboard },
  { id: 'sounds', label: 'Sounds', icon: AudioLines },
  { id: 'schedule', label: 'Schedule', icon: CalendarClock },
  { id: 'accounts', label: 'Accounts', icon: Users },
  { id: 'campaigns', label: 'Campaigns', icon: BadgeDollarSign },
  { id: 'performance', label: 'Performance', icon: ChartNoAxesColumn },
  { id: 'setup', label: 'Setup', icon: PlugZap },
  { id: 'settings', label: 'Settings', icon: Settings2 },
]

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="mk" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0" stopColor="#3DD6C6" />
          <stop offset=".55" stopColor="#FFB347" />
          <stop offset="1" stopColor="#FF5E7E" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="#2A2136" />
      <path d="M4 22 L10 20 L14 21 L17 9 L20 18 L23 15 L28 16" fill="none" stroke="url(#mk)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TopBar() {
  const stats = useStore((s) => s.stats)
  const settings = useStore((s) => s.settings)
  const autopilot = !!settings?.autopilot
  const flip = async () => {
    try {
      const next = await api.put<S>('/api/settings', { autopilot: !autopilot })
      setState({ settings: next })
      toast(!autopilot ? 'Autopilot on: strong clips get scheduled automatically' : 'Autopilot off: you approve every clip', 'good')
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <header className="topbar">
      <div className="meter"><b>{stats?.live ?? 0}</b><span>streams watched</span></div>
      <div className="meter"><b>{stats?.in_flight ?? 0}</b><span>clips in the works</span></div>
      <div className="meter"><b>{stats?.clips_today ?? 0}</b><span>clips in 24 h</span></div>
      <div className="meter"><b>{stats?.posts_today ?? 0}</b><span>posts in 24 h</span></div>
      <div className="meter"><b>{compact(stats?.views)}</b><span>views tracked</span></div>
      <div className="spacer" />
      <label className="switch-row" title="Schedule clips above your score bar without asking">
        <button type="button" role="switch" aria-checked={autopilot} className="toggle" onClick={flip} />
        <span>Autopilot</span>
      </label>
    </header>
  )
}

export default function App() {
  const [page, setPage] = useState<Page>(() => (location.hash.slice(1) as Page) || 'desk')
  const [clipId, setClipId] = useState<number | null>(null)
  const connected = useStore((s) => s.connected)
  const encoder = useStore((s) => s.encoder)
  const update = useStore((s) => s.update)
  const ready = useStore((s) => s.clips.filter((c) => c.status === 'ready').length)
  const queued = useStore((s) => s.stats?.posts_queued ?? 0)
  const accounts = useStore((s) => s.accounts.filter((a) => a.active).length)

  useEffect(() => connect(), [])
  useEffect(() => {
    if (location.hash.slice(1) !== page) location.hash = page
  }, [page])
  useEffect(() => {
    const onHash = () => {
      const h = location.hash.slice(1) as Page
      if (PAGES.some((p) => p.id === h)) setPage(h)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const counts: Partial<Record<Page, number>> = { clips: ready, schedule: queued, accounts }
  return (
    <div className="app">
      <nav className="rail" aria-label="Main">
        <div className="brand"><Mark /><b>Surge</b></div>
        {PAGES.map(({ id, label, icon: Icon }) => (
          <button key={id} className="nav" aria-current={page === id ? 'page' : undefined} onClick={() => setPage(id)}>
            <Icon size={18} strokeWidth={1.8} />
            {label}
            {counts[id] ? <span className="count">{counts[id]}</span> : null}
          </button>
        ))}
        <div className="rail-foot">
          <div className="row"><span>Engine</span><span><span className={`dot ${connected ? 'on' : 'live'}`} /> {connected ? 'running' : 'reconnecting'}</span></div>
          <div className="row"><span>Encoder</span><span>{encoder ? encoder.replace('h264_', '').replace('lib', '').toUpperCase() : '…'}</span></div>
          <div className="row"><span>Version</span><span>{update ? `v${update.current}` : '…'}{update?.available && <> · <a href="#settings">update</a></>}</span></div>
        </div>
      </nav>
      <main className="main">
        <TopBar />
        <UpdateBanner />
        <div className="page">
          {page === 'desk' && <Desk onOpenClip={setClipId} />}
          {page === 'clips' && <Clips onOpen={setClipId} />}
          {page === 'sounds' && <Sounds />}
          {page === 'schedule' && <Schedule />}
          {page === 'accounts' && <Accounts goSetup={() => setPage('setup')} />}
          {page === 'campaigns' && <Campaigns />}
          {page === 'performance' && <Performance />}
          {page === 'setup' && <Setup go={setPage} />}
          {page === 'settings' && <Settings />}
        </div>
      </main>
      {clipId !== null && <ClipEditor id={clipId} onClose={() => setClipId(null)} />}
      <Toasts />
    </div>
  )
}
