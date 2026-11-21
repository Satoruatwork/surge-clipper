import { useEffect, useState } from 'react'
import { Radar, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, setState, toast, useStore } from '../lib/store'
import type { DiscoverStream, Settings as S } from '../lib/types'
import { compact } from '../lib/format'
import { Avatar, Modal } from './ui'

function Discover({ onClose }: { onClose: () => void }) {
  const category = useStore((s) => String(s.settings?.watch_category ?? 'Grand Theft Auto VI'))
  const [game, setGame] = useState(category)
  const [data, setData] = useState<{ game: string; viewers: number; streams: DiscoverStream[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const fetchIt = async (g: string) => {
    setBusy(true)
    try {
      setData(await api.get(`/api/discover?game=${encodeURIComponent(g)}`))
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    void fetchIt(category)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const add = async (s: DiscoverStream) => {
    try {
      await api.post('/api/watch', { url: `https://www.twitch.tv/${s.login}`, auto: true })
      toast(`${s.name} is on your watchlist`, 'good')
      setData((d) => d && { ...d, streams: d.streams.map((x) => (x.login === s.login ? { ...x, watched: true } : x)) })
      void load.watch()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <Modal title="Live right now" onClose={onClose} wide>
      <div className="inline-form" style={{ marginBottom: 14 }}>
        <input className="input" value={game} onChange={(e) => setGame(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && fetchIt(game)} aria-label="Game" />
        <button className="btn" onClick={() => fetchIt(game)} disabled={busy}>Search Twitch</button>
      </div>
      {data && <p className="muted" style={{ marginTop: 0 }}>{compact(data.viewers)} people are watching {data.game} on Twitch. Only clip streamers who allow it, such as through their own clipping campaign.</p>}
      <div className="discover">
        {data?.streams.map((s) => (
          <div className="disc-row" key={s.login}>
            <Avatar src={s.avatar} name={s.name} platform="twitch" />
            <div className="disc-name"><b>{s.name}</b><span title={s.title}>{s.title}</span></div>
            <span className="num disc-viewers"><span className="dot live" /> {compact(s.viewers)}</span>
            {s.watched ? <span className="faint">On your list</span> : <button className="btn sm" onClick={() => add(s)}>Add to watchlist</button>}
          </div>
        ))}
      </div>
    </Modal>
  )
}

export function Watchlist() {
  const watch = useStore((s) => s.watch)
  const maxLive = useStore((s) => Number(s.settings?.max_live ?? 3))
  const [url, setUrl] = useState('')
  const [disc, setDisc] = useState(false)
  const add = async () => {
    try {
      await api.post('/api/watch', { url, auto: true })
      setUrl('')
      void load.watch()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const setMax = async (v: number) => {
    const next = await api.put<S>('/api/settings', { max_live: v })
    setState({ settings: next })
  }
  return (
    <div className="panel watchlist">
      <h2><Radar size={16} />Watchlist <span className="n">{watch.filter((w) => w.live).length} live</span></h2>
      <p className="faint" style={{ margin: '0 0 10px', fontSize: 13 }}>
        Surge starts watching when they go live in GTA VI, up to
        <input className="input num mini-num" type="number" min={1} max={8} value={maxLive} onChange={(e) => setMax(Number(e.target.value))} aria-label="Streams watched at once" />
        at once, biggest first.
      </p>
      <div className="inline-form">
        <input className="input" placeholder="twitch.tv/name or kick.com/name" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && url.trim() && add()} aria-label="Channel to watch" />
        <button className="btn sm" disabled={!url.trim()} onClick={add}>Add</button>
      </div>
      <div className="watch-rows">
        {watch.map((w) => (
          <div className="watch-row" key={w.id}>
            <Avatar src={w.avatar} name={w.name ?? w.channel} />
            <div className="disc-name">
              <b>{w.name ?? w.channel}</b>
              <span>{w.live ? `${compact(w.viewers)} watching${w.game ? `, ${w.game}` : ''}` : 'Offline'}</span>
            </div>
            <span className={`dot ${w.live ? 'live' : ''}`} aria-label={w.live ? 'Live' : 'Offline'} />
            <button type="button" role="switch" aria-checked={!!w.auto} aria-label={`Auto-watch ${w.name}`} title="Start watching automatically" className="toggle"
              onClick={() => api.patch(`/api/watch/${w.id}`, { auto: !w.auto }).then(() => load.watch())} />
            <button className="btn ghost icon sm" aria-label={`Remove ${w.name}`} onClick={() => api.del(`/api/watch/${w.id}`).then(() => load.watch())}><Trash2 size={13} /></button>
          </div>
        ))}
      </div>
      <button className="btn sm" style={{ marginTop: 10, width: '100%' }} onClick={() => setDisc(true)}>Find live GTA VI streams</button>
      {disc && <Discover onClose={() => setDisc(false)} />}
    </div>
  )
}
