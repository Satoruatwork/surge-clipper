import { useSyncExternalStore } from 'react'
import { api } from './api'
import type { Account, Clip, Group, Library, LogEntry, PostRow, Pulse, Settings, Source, Stats, WatchItem } from './types'

export interface Toast { id: number; text: string; tone: 'info' | 'good' | 'bad' }

export interface State {
  sources: Source[]
  clips: Clip[]
  accounts: Account[]
  posts: PostRow[]
  stats: Stats | null
  settings: Settings | null
  styles: Record<string, string>
  encoder: string
  hasGoogleSecret: boolean
  logs: LogEntry[]
  pulses: Record<number, Pulse[]>
  transcripts: Record<number, string>
  thumbs: Record<number, number>
  uploads: Record<number, number>
  spikes: Record<number, number[]>
  connected: boolean
  toasts: Toast[]
  library: Library | null
  watch: WatchItem[]
  groups: Group[]
}

let state: State = {
  sources: [], clips: [], accounts: [], posts: [], stats: null, settings: null, styles: {}, encoder: '',
  hasGoogleSecret: false, logs: [], pulses: {}, transcripts: {}, thumbs: {}, uploads: {}, spikes: {},
  connected: false, toasts: [], library: null, watch: [], groups: [],
}
const listeners = new Set<() => void>()

export function getState() {
  return state
}

export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const p = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...p }
  listeners.forEach((l) => l())
}

function subscribe(l: () => void) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => sel(state))
}

// ------------------------------------------------------------- toasts
let toastId = 0
export function toast(text: string, tone: Toast['tone'] = 'info') {
  const id = ++toastId
  setState((s) => ({ toasts: [...s.toasts.slice(-3), { id, text, tone }] }))
  setTimeout(() => setState((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), tone === 'bad' ? 7000 : 4200)
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// ------------------------------------------------------------- loaders
export const load = {
  async state() {
    const s = await api.get<{ settings: Settings; styles: Record<string, string>; encoder: string; logs: LogEntry[]; has_google_secret: boolean }>('/api/state')
    setState({ settings: s.settings, styles: s.styles, encoder: s.encoder, logs: s.logs, hasGoogleSecret: s.has_google_secret })
  },
  async sources() {
    setState({ sources: await api.get<Source[]>('/api/sources') })
  },
  async clips() {
    setState({ clips: await api.get<Clip[]>('/api/clips?limit=400') })
  },
  async accounts() {
    setState({ accounts: await api.get<Account[]>('/api/accounts') })
  },
  async posts() {
    setState({ posts: await api.get<PostRow[]>('/api/posts') })
  },
  async stats() {
    setState({ stats: await api.get<Stats>('/api/stats') })
  },
  async watch() {
    setState({ watch: await api.get<WatchItem[]>('/api/watch') })
  },
  async groups() {
    setState({ groups: await api.get<Group[]>('/api/groups') })
  },
  async library() {
    setState({ library: await api.get<Library>('/api/library') })
  },
  async pulses(id: number) {
    const p = await api.get<Pulse[]>(`/api/sources/${id}/pulse`)
    setState((s) => ({ pulses: { ...s.pulses, [id]: p } }))
  },
  async clip(id: number) {
    const c = await api.get<Clip>(`/api/clips/${id}`)
    setState((s) => {
      const i = s.clips.findIndex((x) => x.id === id)
      const clips = i >= 0 ? s.clips.map((x) => (x.id === id ? { ...x, ...c } : x)) : [c, ...s.clips]
      return { clips }
    })
    return c
  },
}

const timers: Record<string, number> = {}
function soon(key: string, fn: () => unknown, ms = 600) {
  if (timers[key]) return
  timers[key] = window.setTimeout(() => {
    delete timers[key]
    Promise.resolve(fn()).catch(() => {})
  }, ms)
}

// ------------------------------------------------------------- websocket
type Msg = { type: string; data: Record<string, unknown>; t: number }

function onMessage(m: Msg) {
  const d = m.data
  switch (m.type) {
    case 'pulse': {
      const p = d as unknown as Pulse
      setState((s) => {
        const prev = s.pulses[p.id] ?? []
        const next = prev.length >= 900 ? [...prev.slice(-899), p] : [...prev, p]
        return { pulses: { ...s.pulses, [p.id]: next } }
      })
      break
    }
    case 'source': {
      const id = d.id as number
      const known = state.sources.some((x) => x.id === id)
      if (!known) return soon('sources', load.sources)
      setState((s) => ({ sources: s.sources.map((x) => (x.id === id ? { ...x, ...(d as Partial<Source>) } : x)) }))
      break
    }
    case 'clip': {
      const id = d.id as number
      if (d.status === 'deleted') {
        setState((s) => ({ clips: s.clips.filter((c) => c.id !== id) }))
      } else if (['ready', 'rejected', 'failed', 'approved'].includes(String(d.status)) || !state.clips.some((c) => c.id === id)) {
        soon(`clip${id}`, () => load.clip(id), 250)
      } else {
        setState((s) => ({ clips: s.clips.map((c) => (c.id === id ? { ...c, ...(d as Partial<Clip>) } : c)) }))
      }
      if (d.status === 'queued' && typeof d.source_id === 'number') {
        const sid = d.source_id
        setState((s) => ({ spikes: { ...s.spikes, [sid]: [...(s.spikes[sid] ?? []).slice(-40), Date.now()] } }))
      }
      soon('stats', load.stats, 800)
      soon('sources', load.sources, 1500)
      break
    }
    case 'post': {
      const id = d.id as number
      if (typeof d.progress === 'number') setState((s) => ({ uploads: { ...s.uploads, [id]: d.progress as number } }))
      soon('posts', load.posts, 700)
      soon('stats', load.stats, 900)
      soon('accounts', load.accounts, 1200)
      break
    }
    case 'accounts': {
      soon('accounts', load.accounts, 100)
      if (d.ok === false) toast(`Couldn't connect: ${d.error}`, 'bad')
      else if (d.ok) toast('Account connected', 'good')
      break
    }
    case 'stats':
      soon('stats', load.stats, 100)
      soon('accounts', load.accounts, 200)
      soon('posts', load.posts, 300)
      break
    case 'watch':
      soon('watch', load.watch, 300)
      break
    case 'log':
      setState((s) => ({ logs: [...s.logs.slice(-199), d as unknown as LogEntry] }))
      break
    case 'thumb':
      setState((s) => ({ thumbs: { ...s.thumbs, [d.id as number]: d.v as number } }))
      break
    case 'transcript': {
      const id = d.id as number
      setState((s) => {
        const prev = s.transcripts[id] ?? ''
        const next = `${prev} ${d.text as string}`.trim()
        return { transcripts: { ...s.transcripts, [id]: next.slice(-260) } }
      })
      break
    }
  }
}

let ws: WebSocket | null = null
export function connect() {
  if (ws && ws.readyState <= 1) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws`)
  ws.onopen = () => {
    setState({ connected: true })
    void Promise.all([load.state(), load.sources(), load.clips(), load.accounts(), load.posts(), load.stats(), load.library(), load.watch(), load.groups()])
      .then(() => state.sources.filter((s) => s.kind === 'live' && s.running).forEach((s) => void load.pulses(s.id)))
  }
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  ws.onclose = () => {
    setState({ connected: false })
    setTimeout(connect, 1500)
  }
}
