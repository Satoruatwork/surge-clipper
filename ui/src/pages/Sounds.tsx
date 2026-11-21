import { useEffect, useRef, useState } from 'react'
import { Pause, Play, Trash2, Upload } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, toast, useStore } from '../lib/store'
import type { LibraryItem } from '../lib/types'
import { timecode } from '../lib/format'
import { ChipsInput, confirmDanger } from '../components/ui'

let player: HTMLAudioElement | null = null

function usePlayer() {
  const [playing, setPlaying] = useState<string | null>(null)
  useEffect(() => () => player?.pause(), [])
  const toggle = (id: string, url: string | null) => {
    if (!url) return
    if (playing === id) {
      player?.pause()
      setPlaying(null)
      return
    }
    player?.pause()
    player = new Audio(url)
    player.volume = 0.8
    player.onended = () => setPlaying(null)
    void player.play()
    setPlaying(id)
  }
  return { playing, toggle }
}

function UploadButton({ kind, label }: { kind: 'sound' | 'song'; label: string }) {
  const input = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const send = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    const fd = new FormData()
    Array.from(files).forEach((f) => fd.append('files', f))
    try {
      const r = await api.post<{ added: LibraryItem[]; errors: string[] }>(`/api/library/upload?kind=${kind}`, fd)
      if (r.added.length) toast(`Added ${r.added.length} ${kind === 'sound' ? 'sound' : 'song'}${r.added.length === 1 ? '' : 's'}`, 'good')
      r.errors.forEach((e) => toast(e, 'bad'))
      void load.library()
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }
  return (
    <>
      <input ref={input} type="file" accept="audio/*" multiple hidden onChange={(e) => send(e.target.files)} />
      <button className="btn" disabled={busy} onClick={() => input.current?.click()}>
        <Upload size={15} />{busy ? 'Adding…' : label}
      </button>
    </>
  )
}

function Row({ item, playing, onPlay }: { item: LibraryItem; playing: boolean; onPlay: () => void }) {
  const [tags, setTags] = useState(item.kind === 'sound' ? item.tags ?? [] : item.moods ?? [])
  const save = async (v: string[]) => {
    setTags(v)
    try {
      await api.patch(`/api/library/${item.id}`, item.kind === 'sound' ? { tags: v } : { moods: v })
      void load.library()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const remove = async () => {
    if (!confirmDanger(`Remove ${item.name} from the library? The file is deleted.`)) return
    await api.del(`/api/library/${item.id}`)
    void load.library()
  }
  return (
    <div className="lib-row">
      <button className="btn icon sm" onClick={onPlay} aria-label={playing ? `Pause ${item.name}` : `Play ${item.name}`}>
        {playing ? <Pause size={14} /> : <Play size={14} />}
      </button>
      <div className="lib-name">
        <b>{item.name}</b>
        <span>
          {item.kind === 'song' ? (item.artist || 'Unknown artist') : item.builtin ? 'Pixabay, free licence' : 'Yours'}
          {item.kind === 'song' && item.drop ? `, drop at ${timecode(item.drop)}` : ''}
        </span>
      </div>
      <div className="lib-tags">
        {item.builtin ? (
          tags.map((t) => <span key={t} className="chip">{t}</span>)
        ) : (
          <ChipsInput values={tags} onChange={save} normalize={(s) => s.trim().toLowerCase()} placeholder="add a tag" />
        )}
      </div>
      <span className="faint num lib-len">{item.duration ? timecode(item.duration) : ''}</span>
      {item.builtin ? (
        item.kind === 'sound'
          ? <a className="lib-badge" href={item.source ?? undefined} target="_blank" rel="noreferrer" title={item.license ?? ''}>Shorts rank {item.rank ?? 5}/10</a>
          : <span className="lib-badge">CC BY, credited</span>
      ) : (
        <button className="btn ghost icon sm" onClick={remove} aria-label={`Remove ${item.name}`}><Trash2 size={14} /></button>
      )}
    </div>
  )
}

export function Sounds() {
  const lib = useStore((s) => s.library)
  const { playing, toggle } = usePlayer()
  const [over, setOver] = useState(false)
  useEffect(() => {
    void load.library()
  }, [])

  const drop = async (e: React.DragEvent) => {
    e.preventDefault()
    setOver(false)
    const files = Array.from(e.dataTransfer.files).filter((f) => f.type.startsWith('audio') || /\.(mp3|wav|ogg|m4a|flac|aac|opus)$/i.test(f.name))
    if (!files.length) return
    const long = files.filter((f) => f.size > 2_500_000)
    const short = files.filter((f) => f.size <= 2_500_000)
    for (const [kind, list] of [['song', long], ['sound', short]] as const) {
      if (!list.length) continue
      const fd = new FormData()
      list.forEach((f) => fd.append('files', f))
      try {
        const r = await api.post<{ added: LibraryItem[]; errors: string[] }>(`/api/library/upload?kind=${kind}`, fd)
        if (r.added.length) toast(`Added ${r.added.length} ${kind}${r.added.length === 1 ? '' : 's'}`, 'good')
        r.errors.forEach((x) => toast(x, 'bad'))
      } catch (err) {
        toast(errorText(err), 'bad')
      }
    }
    void load.library()
  }

  if (!lib) return <p className="muted">Loading the sound library…</p>
  const mine = lib.sounds.filter((s) => !s.builtin)
  const pack = lib.sounds.filter((s) => s.builtin)
  return (
    <div onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={drop}
      className={over ? 'drop-over' : undefined}>
      <div className="page-head">
        <div>
          <h1>Sounds</h1>
          <p>The meme sounds Shorts edits run on, plus music and stickers. The editor drops them where the moment calls for it, rotates them so clips don't sound the same, and gives sounds to only part of your clips so they keep their punch (Settings). Drop extra files anywhere on this page: Surge reads the name (vine boom, bruh, phonk…) and files them.</p>
        </div>
        <div className="actions">
          <UploadButton kind="sound" label="Add meme sounds" />
          <UploadButton kind="song" label="Add songs" />
        </div>
      </div>

      <section className="lib-section">
        <header><h2>Your sounds</h2><span className="faint">{mine.length}, picked ahead of the pack when both fit</span></header>
        {mine.length === 0 ? (
          <div className="empty">
            <h3>Got a sound the pack is missing?</h3>
            <p>Drop it here (Fahhh, Boowomp, the real Emotional Damage clip…). Surge tags it from the file name and uses it ahead of the pack. Only add files you're allowed to use.</p>
          </div>
        ) : (
          <div className="panel lib-list">{mine.map((i) => <Row key={i.id} item={i} playing={playing === i.id} onPlay={() => toggle(i.id, i.url)} />)}</div>
        )}
      </section>

      <section className="lib-section">
        <header><h2>Shorts meme pack</h2><span className="faint">{pack.length} of the sounds that carry Shorts edits, ranked by how often editors reach for them. Pixabay licence: free for monetized videos, no credit needed</span></header>
        <div className="panel lib-list">{[...pack].sort((a, b) => (b.rank ?? 0) - (a.rank ?? 0)).map((i) => <Row key={i.id} item={i} playing={playing === i.id} onPlay={() => toggle(i.id, i.url)} />)}</div>
      </section>

      <section className="lib-section">
        <header><h2>Music</h2><span className="faint">The drop is lined up with the clip's peak, and the music ducks under the streamer's voice</span></header>
        <div className="panel lib-list">{lib.songs.map((i) => <Row key={i.id} item={i} playing={playing === i.id} onPlay={() => toggle(i.id, i.url)} />)}</div>
      </section>

      <section className="lib-section">
        <header><h2>Clipping favourites</h2><span className="faint">{lib.catalog.filter((c) => c.in_library).length} of {lib.catalog.length} in your library</span></header>
        <p className="lib-warn">These 30 tracks carry the clipping scene, grouped by the moment they fit. They are commercial songs: on uploads made through the API, Content ID claims them and the label can take the revenue or block the Short, and Instagram can mute them. Add one only if you hold a licence. Surge recognises them by file name and files them under the right mood.</p>
        <table className="table">
          <thead><tr><th>Song</th><th>Artist</th><th>Mood</th><th>Fits</th><th>Status</th></tr></thead>
          <tbody>
            {lib.catalog.map((c) => (
              <tr key={c.title}>
                <td><b style={{ fontWeight: 600 }}>{c.title}</b></td>
                <td className="muted">{c.artist}</td>
                <td>{c.moods.map((m) => <span key={m} className="chip" style={{ marginRight: 4 }}>{m}</span>)}</td>
                <td className="muted">{c.use}</td>
                <td><span className="status"><span className={`dot ${c.in_library ? 'on' : ''}`} />{c.in_library ? 'In library' : 'Not added'}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="lib-section">
        <header><h2>Stickers</h2><span className="faint">Pop in on reactions</span></header>
        <div className="sticker-grid">
          {lib.stickers.map((s) => (
            <figure key={s.name}>{s.url && <img src={s.url} alt="" />}<figcaption>{s.name}</figcaption></figure>
          ))}
        </div>
      </section>
    </div>
  )
}
