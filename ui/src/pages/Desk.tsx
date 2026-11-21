import { useMemo, useState } from 'react'
import { Crop, Play, Square, Trash2, RotateCw } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, setState, toast, useStore } from '../lib/store'
import type { Clip, Source } from '../lib/types'
import { chatClassLabel, clock, compact, platformName, timecode } from '../lib/format'
import { LiveTrace, VodTrace } from '../components/HeatTrace'
import { Seg, Steps, STEP_LABEL, confirmDanger } from '../components/ui'
import { FramingModal } from '../components/FramingModal'
import { Watchlist } from '../components/Watchlist'

const IN_FLIGHT = ['queued', 'cutting', 'transcribing', 'writing', 'rendering']

function AddBar() {
  const [url, setUrl] = useState('')
  const [mode, setMode] = useState<'auto' | 'live' | 'vod'>('auto')
  const [target, setTarget] = useState(25)
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    if (!url.trim()) return
    setBusy(true)
    try {
      const s = await api.post<Source>('/api/sources', { url, kind: mode === 'auto' ? null : mode, target })
      setState((st) => ({ sources: [s, ...st.sources.filter((x) => x.id !== s.id)] }))
      toast(s.kind === 'live' ? `Watching ${s.channel ?? 'stream'} live` : 'Clipping the broadcast', 'good')
      setUrl('')
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="addbar" onSubmit={(e) => { e.preventDefault(); void submit() }}>
      <label className="sr-only" htmlFor="add-url">Stream or broadcast link</label>
      <input
        id="add-url"
        className="input"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="Paste a Twitch, Kick or YouTube link: a live channel or a past broadcast"
        autoComplete="off"
        spellCheck={false}
      />
      <Seg label="Mode" value={mode} onChange={setMode} options={[
        { value: 'auto', label: 'Detect' }, { value: 'live', label: 'Live' }, { value: 'vod', label: 'Past broadcast' },
      ]} />
      <label className="target" title="How many clips to make from a past broadcast">
        <input className="input num" type="number" min={3} max={60} value={target} onChange={(e) => setTarget(Number(e.target.value))} />
        clips
      </label>
      <button className="btn primary lg" disabled={busy || !url.trim()}>{busy ? 'Adding…' : 'Add stream'}</button>
    </form>
  )
}

function statusOf(s: Source): { dot: string; text: string } {
  switch (s.status) {
    case 'live': return { dot: 'live', text: 'Live' }
    case 'starting': return { dot: 'warn', text: 'Connecting' }
    case 'reconnecting': return { dot: 'warn', text: 'Reconnecting' }
    case 'offline': return { dot: '', text: 'Offline, waiting' }
    case 'analyzing': return { dot: 'warn', text: s.stage ?? 'Analyzing' }
    case 'rendering': return { dot: 'warn', text: s.stage ?? 'Rendering' }
    case 'done': return { dot: 'on', text: s.stage ?? 'Done' }
    case 'error': return { dot: 'live', text: 'Stopped by an error' }
    default: return { dot: '', text: 'Stopped' }
  }
}

function Monitor({ s, clips, onFrame }: { s: Source; clips: Clip[]; onFrame: () => void }) {
  const pulses = useStore((st) => st.pulses[s.id])
  const ticker = useStore((st) => st.transcripts[s.id])
  const thumbV = useStore((st) => st.thumbs[s.id])
  const spikes = useStore((st) => st.spikes[s.id])
  const last = pulses?.[pulses.length - 1]
  const thumbUrl = s.thumb_url ?? (thumbV ? `/media/runs/src${String(s.id).padStart(4, '0')}/live.jpg` : null)
  const st = statusOf(s)
  const isLive = s.kind === 'live'
  const running = s.running || ['live', 'starting', 'reconnecting', 'offline', 'analyzing', 'rendering'].includes(s.status)
  const spiking = !!spikes?.length && Date.now() - spikes[spikes.length - 1] < 8000
  const cuts = useMemo(() => clips.filter((c) => c.source_id === s.id).map((c) => c.peak), [clips, s.id])
  const made = (s.counts.ready ?? 0) + (s.counts.approved ?? 0)
  const working = IN_FLIGHT.reduce((n, k) => n + (s.counts[k] ?? 0), 0)
  const name = s.stats.streamer || s.channel || 'Stream'
  const game = s.stats.game || s.settings.game

  const act = async (path: string, ok: string) => {
    try {
      await api.post(`/api/sources/${s.id}/${path}`)
      toast(ok)
      void load.sources()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const remove = async () => {
    if (!confirmDanger(`Remove ${name}? Clips already made stay in your library.`)) return
    await api.del(`/api/sources/${s.id}`)
    setState((x) => ({ sources: x.sources.filter((y) => y.id !== s.id) }))
  }

  return (
    <article className={`monitor${spiking ? ' is-spiking' : ''}`} aria-label={name}>
      <div className="frame">
        {thumbUrl ? <img src={`${thumbUrl}?v=${thumbV ?? 0}`} alt="" /> : <div className="nothumb">{isLive ? 'Preview appears once the stream connects' : 'Past broadcast'}</div>}
        <span className="badge"><span className={`dot ${st.dot}`} />{platformName[s.platform] ?? s.platform}</span>
      </div>
      <div className="body">
        <div className="head">
          <div className="who">
            <b>{name}</b>
            <span title={s.title ?? ''}>{s.title || game || s.url}</span>
          </div>
          <div className="readouts">
            {isLive ? (
              <>
                <div className="readout"><b>{last ? compact(last.rate) : '–'}</b><span>msgs/min</span></div>
                <div className="readout"><b>{last ? last.chatters : '–'}</b><span>chatters</span></div>
                <div className="readout"><b>{made}</b><span>clips{working ? ` +${working}` : ''}</span></div>
                <div className="readout"><b>{timecode(s.stats.uptime)}</b><span>watched</span></div>
              </>
            ) : (
              <>
                <div className="readout"><b>{timecode(s.stats.duration)}</b><span>length</span></div>
                <div className="readout"><b>{made}</b><span>clips{working ? ` +${working}` : ''}</span></div>
              </>
            )}
          </div>
          <div className="tools">
            {isLive && <button className="btn ghost icon sm" onClick={onFrame} aria-label="Facecam and layout" title="Facecam and layout"><Crop size={16} /></button>}
            {running ? (
              <button className="btn ghost icon sm" onClick={() => act('stop', isLive ? 'Stopped watching' : 'Cancelled')} aria-label="Stop" title="Stop"><Square size={15} /></button>
            ) : (
              <button className="btn ghost icon sm" onClick={() => act('start', isLive ? 'Watching again' : 'Running again')} aria-label={isLive ? 'Watch again' : 'Run again'} title={isLive ? 'Watch again' : 'Run again'}>{isLive ? <Play size={15} /> : <RotateCw size={15} />}</button>
            )}
            <button className="btn ghost icon sm" onClick={remove} aria-label="Remove" title="Remove"><Trash2 size={15} /></button>
          </div>
        </div>
        <div className="trace-wrap">
          {isLive ? (
            <LiveTrace pulses={pulses ?? []} cuts={cuts} />
          ) : s.stats.curve?.length ? (
            <VodTrace curve={s.stats.curve} step={s.stats.step ?? 1} peaks={s.stats.peaks ?? []} />
          ) : (
            <div style={{ height: 92, display: 'grid', alignContent: 'center', gap: 10 }}>
              <div className="progress"><i style={{ width: `${Math.round((s.progress || 0.02) * 100)}%` }} /></div>
            </div>
          )}
        </div>
        <div className="foot">
          <span className="status"><span className={`dot ${st.dot}`} />{st.text}</span>
          {!isLive && s.status !== 'done' && s.progress > 0 && <span className="faint num">{Math.round(s.progress * 100)}%</span>}
          {s.error && <span style={{ color: 'var(--sunset)' }} className="ticker">{s.error}</span>}
          {isLive && !s.error && (
            <div className="ticker" title="What the streamer is saying">
              <span>{ticker ? `“${ticker}”` : ''}</span>
            </div>
          )}
          {s.campaigns?.map((c) => <span key={c} className="chip clip" title="Clips post to this campaign's accounts">for {c}</span>)}
          {game && <span className="chip" title="Game">{game}</span>}
          {isLive && last?.cls && <span className={`chip ${last.cls}`}>{chatClassLabel[last.cls] ?? last.cls}</span>}
          {isLive && last?.top?.slice(0, 2).map(([m, n]) => (
            <span className="chip" key={m} title={m}>{m.length > 18 ? `${m.slice(0, 18)}…` : m}{n > 1 ? ` ×${n}` : ''}</span>
          ))}
        </div>
      </div>
    </article>
  )
}

function Pipeline({ clips, onOpen }: { clips: Clip[]; onOpen: (id: number) => void }) {
  const flying = clips.filter((c) => IN_FLIGHT.includes(c.status)).slice(0, 8)
  const fresh = clips.filter((c) => c.status === 'ready').slice(0, 6)
  const logs = useStore((s) => s.logs)
  return (
    <aside className="side">
      <Watchlist />
      <div className="panel">
        <h2>In the works <span className="n">{flying.length}</span></h2>
        {flying.length === 0 && <p className="faint" style={{ margin: '0 0 6px', fontSize: 13 }}>Moments show up here the second a spike is cut.</p>}
        {flying.map((c) => (
          <div className="job" key={c.id}>
            <div className="mini" />
            <div className="txt">
              <b>{c.hook || c.streamer || `Clip ${c.id}`}</b>
              <Steps status={c.status} />
              <span className="meta">{STEP_LABEL[c.status]}</span>
            </div>
          </div>
        ))}
      </div>
      <div className="panel">
        <h2>Ready to review <span className="n">{clips.filter((c) => c.status === 'ready').length}</span></h2>
        {fresh.length === 0 && <p className="faint" style={{ margin: '0 0 6px', fontSize: 13 }}>Finished clips wait here for your approval.</p>}
        {fresh.map((c) => (
          <button className="job" key={c.id} onClick={() => onOpen(c.id)} style={{ background: 'none', border: 0, width: '100%', textAlign: 'left', color: 'inherit', cursor: 'pointer', padding: '8px 0', borderTop: '1px solid var(--line)' }}>
            <div className="mini">{c.thumb_url && <img src={c.thumb_url} alt="" />}</div>
            <div className="txt">
              <b>{c.title || c.hook}</b>
              <span className="meta">{c.streamer}, {timecode(c.duration)}<span className="score-tag">{c.score.toFixed(1)}</span></span>
            </div>
          </button>
        ))}
      </div>
      <div className="panel">
        <h2>Activity</h2>
        <div className="log" aria-live="polite">
          {logs.slice(-60).reverse().map((l, i) => (
            <div key={`${l.t}-${i}`} className={l.level}><time>{clock(l.t)}</time><span>{l.text}</span></div>
          ))}
        </div>
      </div>
    </aside>
  )
}

export function Desk({ onOpenClip }: { onOpenClip: (id: number) => void }) {
  const sources = useStore((s) => s.sources)
  const clips = useStore((s) => s.clips)
  const [framing, setFraming] = useState<Source | null>(null)
  return (
    <div className="desk">
      <div style={{ minWidth: 0 }}>
        <AddBar />
        {sources.length === 0 ? (
          <div className="empty">
            <h3>Nothing on the desk yet</h3>
            <p>Paste a live channel to clip it as it happens, or a past broadcast to pull its best moments in one pass. Watch several streamers at once to fill your accounts faster.</p>
          </div>
        ) : (
          <div className="monitors">
            {sources.map((s) => (
              <Monitor key={s.id} s={s} clips={clips} onFrame={() => setFraming(s)} />
            ))}
          </div>
        )}
      </div>
      <Pipeline clips={clips} onOpen={onOpenClip} />
      {framing && <FramingModal source={framing} onClose={() => setFraming(null)} />}
    </div>
  )
}
