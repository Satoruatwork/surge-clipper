export type SourceStatus =
  | 'idle' | 'starting' | 'live' | 'offline' | 'reconnecting' | 'analyzing' | 'rendering' | 'done' | 'error' | 'stopped'

export interface Source {
  id: number
  url: string
  platform: 'twitch' | 'kick' | 'youtube' | 'file' | string
  channel: string | null
  title: string | null
  kind: 'live' | 'vod'
  status: SourceStatus
  stage: string | null
  progress: number
  error: string | null
  settings: {
    layout?: string
    cam?: number[]
    cam_auto?: number[] | null
    cam_kind?: string
    cam_mode?: string
    frame?: number[]
    focus?: number
    target?: number
    game?: string
    audio_shift_ms?: number
  }
  stats: {
    uptime?: number
    clips?: number
    chat_total?: number
    threshold?: number
    game?: string
    streamer?: string
    duration?: number
    curve?: number[]
    step?: number
    peaks?: number[]
  }
  running: boolean
  thumb_url: string | null
  counts: Record<string, number>
  created_at: number
  campaigns?: string[]
}

export type ClipStatus =
  | 'queued' | 'cutting' | 'transcribing' | 'writing' | 'rendering' | 'ready' | 'approved' | 'rejected' | 'failed'

export interface Word { w: string; s: number; e: number; p?: number }

export interface Variant { hook?: string; title?: string; description?: string }

export interface Clip {
  id: number
  source_id: number
  streamer: string | null
  platform: string | null
  game: string | null
  peak: number
  start: number | null
  end: number | null
  duration: number | null
  score: number
  kind: string | null
  chat_class: string | null
  signals: Record<string, number>
  chat: [string, number][]
  words: Word[]
  transcript: string | null
  hook: string | null
  title: string | null
  description: string | null
  hashtags: string[]
  variants: Variant[]
  layout: string | null
  caption_style: string | null
  cam: number[] | null
  video_url: string | null
  thumb_url: string | null
  status: ClipStatus
  error: string | null
  spoiler: number
  created_at: number
  updated_at: number
  posts?: Record<string, number>
  post_list?: PostRow[]
  fx?: FxPlan | Record<string, never>
}

export interface Account {
  id: number
  platform: 'youtube' | 'instagram'
  external_id: string
  name: string
  handle: string
  avatar: string | null
  tags: string[]
  windows: string[]
  daily_cap: number
  active: number
  status: string
  error: string | null
  followers: number
  views_total: number
  token_expires: number | null
  posts: Record<string, number>
  views: number
  today: number
  next_post: number | null
  group_id: number | null
  warmup: number
  cap_now: number
  failed_24h: number
  token_days: number | null
  last_post_at: number | null
  last_check: number | null
  followers_gained: number
}

export interface PostRow {
  id: number
  clip_id: number
  account_id: number
  scheduled_at: number
  status: 'queued' | 'uploading' | 'scheduled' | 'published' | 'failed' | 'cancelled'
  title: string
  description: string
  hashtags: string[]
  external_id: string | null
  url: string | null
  error: string | null
  views: number
  likes: number
  comments: number
  account_name?: string
  platform?: string
  account_platform?: string
  avatar?: string | null
  account_avatar?: string | null
  thumb_url?: string | null
  hook?: string | null
  streamer?: string | null
  duration?: number | null
}

export interface Pulse {
  id: number
  t: number
  score: number
  thr: number
  chat: number
  people: number
  audio: number
  voice: number
  rate: number
  chatters: number
  cls: string | null
  top: [string, number][]
  loud: number
}

export interface Stats {
  clips_total: number
  clips_today: number
  clips_ready: number
  in_flight: number
  posts_queued: number
  posts_scheduled: number
  posts_published: number
  posts_today: number
  views: number
  accounts: number
  live: number
}

export interface LogEntry { text: string; level: 'info' | 'warn' | 'error'; source_id: number | null; t: number }

export type Settings = Record<string, string | number | boolean>

export interface LibraryItem {
  id: string
  kind: 'sound' | 'song'
  name: string
  artist?: string
  tags?: string[]
  moods?: string[]
  gain: number
  cue?: 'hit' | 'lead' | 'tail'
  duration?: number
  drop?: number | null
  builtin: boolean
  credit: string | null
  url: string | null
  rank?: number
  source?: string | null
  license?: string | null
}

export interface CatalogSong { title: string; artist: string; moods: string[]; use: string; in_library: boolean }

export interface Library {
  sounds: LibraryItem[]
  songs: LibraryItem[]
  catalog: CatalogSong[]
  stickers: { name: string; url: string | null }[]
}

export type CueKind = 'sound' | 'zoom' | 'shake' | 'flash' | 'sticker'

export interface Cue { t: number; kind: CueKind; name?: string; tags?: string[]; d?: number; amount?: number; why?: string; end?: number }

export interface FxPlan { cues: Cue[]; music: { song: string; mood?: string } | null; level?: string; source?: string; credit?: string | null }

export interface Group { id: number; name: string; color: string | null; tags: string[]; windows: string[]; daily_cap: number; members: number }

export interface Campaign {
  id: number
  name: string
  url: string | null
  rate: number
  budget: number
  hashtags: string[]
  mentions: string[]
  streamers: string[]
  accounts: number[]
  platforms: string[]
  min_seconds: number
  max_seconds: number
  status: 'active' | 'paused' | 'ended'
  ends_at: number | null
  notes: string | null
  site_id: number | null
  campaign_url: string | null
  submit_url: string | null
  submit_mode: 'link' | 'tracked'
  code: string | null
  dedicated: number
  stats: { posts: number; published: number; views: number; likes: number; comments: number; earned: number; links: string[]; to_submit: number; submitted: number }
}

export interface Site { id: number; key: string; name: string; url: string | null; notes: string; enabled: number; custom: number; accounts: number[]; campaigns: number }

export interface Submission { id: number; url: string | null; title: string; views: number; likes: number; status: string; submit_status: string | null; submitted_at: number | null; scheduled_at: number; account: string; platform: string; thumb_url: string | null }

export interface WatchItem {
  id: number
  url: string
  platform: string
  channel: string | null
  name: string | null
  avatar: string | null
  priority: number
  auto: number
  category: string | null
  live: number
  viewers: number
  title: string | null
  game: string | null
  last_live: number | null
  source_id: number | null
}

export interface DiscoverStream { login: string; name: string; avatar: string | null; viewers: number; title: string; watched: boolean }

export interface SetupStatus {
  google: { client: { type: string | null; project_id: string | null; client_id: string | null } | null; channels: number; broken: string[]; uploads_today: number; upload_limit: number }
  meta: { app_id: boolean; secret: boolean; redirect: string; accounts: number; broken: string[]; expiring: string[] }
  ai: { key: boolean; model: string }
  engine: { encoder: string; models: string[]; disk_free_gb: number; disk_total_gb: number }
}

export interface Breakdown { name: string; posts: number; views: number; avg: number }

export interface Insights {
  total_views: number
  posts: number
  daily: { day: string; views: number }[]
  top: { title: string; account: string; platform: string; views: number; url: string | null; thumb_url: string | null }[]
  streamers: Breakdown[]
  accounts: Breakdown[]
  reactions: Breakdown[]
  layouts: Breakdown[]
  sounds: Breakdown[]
  campaigns: { name: string; posts: number; published: number; views: number; earned: number }[]
}
