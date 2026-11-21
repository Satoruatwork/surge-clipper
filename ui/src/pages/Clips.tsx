import { useMemo, useRef, useState } from 'react'
import { CheckCheck } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, toast, useStore } from '../lib/store'
import type { Clip } from '../lib/types'
import { ago, timecode } from '../lib/format'
import { Seg, Steps, STEP_LABEL } from '../components/ui'

const IN_FLIGHT = ['queued', 'cutting', 'transcribing', 'writing', 'rendering']
type Filter = 'ready' | 'approved' | 'working' | 'rejected' | 'all'

function scoreTone(s: number) {
  return s >= 8 ? 'hot' : s >= 6 ? 'warm' : 'cool'
}

function Poster({ c, onOpen }: { c: Clip; onOpen: () => void }) {
  const vid = useRef<HTMLVideoElement>(null)
  const [hover, setHover] = useState(false)
  const working = IN_FLIGHT.includes(c.status)
  const posted = (c.posts?.published ?? 0) + (c.posts?.scheduled ?? 0)
  const queued = (c.posts?.queued ?? 0) + (c.posts?.uploading ?? 0)
  return (
    <button
      className={`poster ${c.status}`}
      onClick={onOpen}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      aria-label={`${c.title ?? 'Clip'}, score ${c.score.toFixed(1)}`}
    >
      <div className="shot">
        {c.thumb_url && <img src={c.thumb_url} alt="" loading="lazy" />}
        {hover && c.video_url && !working && (
          <video ref={vid} src={c.video_url} muted autoPlay loop playsInline preload="none" />
        )}
        {working && (
          <div className="working">
            <span>{STEP_LABEL[c.status]}</span>
            <Steps status={c.status} />
          </div>
        )}
        {c.status === 'failed' && <div className="working"><span style={{ color: 'var(--sunset)' }}>Failed</span><span className="faint" style={{ fontSize: 12 }}>{c.error?.slice(0, 120)}</span></div>}
        {!working && c.status !== 'failed' && <span className={`score ${scoreTone(c.score)}`}>{c.score.toFixed(1)}</span>}
        {!working && c.duration ? <span className="dur">{timecode(c.duration)}</span> : null}
        {(posted > 0 || queued > 0) && (
          <span className="flag"><span className={`dot ${posted ? 'on' : 'warn'}`} />{posted ? `${posted} posted` : `${queued} queued`}</span>
        )}
      </div>
      <div className="cap">
        <b>{c.title || c.hook || 'Untitled moment'}</b>
        <span>{c.streamer ?? 'Unknown'}, {ago(c.created_at)}</span>
      </div>
    </button>
  )
}

export function Clips({ onOpen }: { onOpen: (id: number) => void }) {
  const clips = useStore((s) => s.clips)
  const autoScore = Number(useStore((s) => s.settings?.auto_approve_score) ?? 8)
  const [filter, setFilter] = useState<Filter>('ready')
  const [sort, setSort] = useState<'new' | 'best'>('new')
  const [busy, setBusy] = useState(false)

  const counts = useMemo(() => ({
    ready: clips.filter((c) => c.status === 'ready').length,
    approved: clips.filter((c) => c.status === 'approved').length,
    working: clips.filter((c) => IN_FLIGHT.includes(c.status)).length,
    rejected: clips.filter((c) => c.status === 'rejected' || c.status === 'failed').length,
    all: clips.length,
  }), [clips])

  const shown = useMemo(() => {
    let list = clips
    if (filter === 'ready') list = clips.filter((c) => c.status === 'ready')
    if (filter === 'approved') list = clips.filter((c) => c.status === 'approved')
    if (filter === 'working') list = clips.filter((c) => IN_FLIGHT.includes(c.status))
    if (filter === 'rejected') list = clips.filter((c) => c.status === 'rejected' || c.status === 'failed')
    return sort === 'best' ? [...list].sort((a, b) => b.score - a.score) : list
  }, [clips, filter, sort])

  const strong = clips.filter((c) => c.status === 'ready' && c.score >= autoScore && !c.spoiler)

  const approveStrong = async () => {
    setBusy(true)
    let n = 0
    for (const c of strong) {
      try {
        await api.post(`/api/clips/${c.id}/approve`, {})
        n++
      } catch (e) {
        toast(errorText(e), 'bad')
        break
      }
    }
    setBusy(false)
    toast(`Scheduled ${n} clip${n === 1 ? '' : 's'} across your accounts`, 'good')
    void load.clips()
    void load.posts()
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Clips</h1>
          <p>Every moment Surge cut, captioned and titled. Approve to schedule it on your accounts, or open one to fix words, framing or copy.</p>
        </div>
        <div className="actions">
          <button className="btn" disabled={!strong.length || busy} onClick={approveStrong}>
            <CheckCheck size={16} />{strong.length ? `Approve ${strong.length} scoring ${autoScore}+` : `No clips scoring ${autoScore}+ yet`}
          </button>
        </div>
      </div>
      <div className="toolbar">
        <Seg<Filter> label="Show" value={filter} onChange={setFilter} options={[
          { value: 'ready', label: 'To review', n: counts.ready },
          { value: 'approved', label: 'Approved', n: counts.approved },
          { value: 'working', label: 'In progress', n: counts.working },
          { value: 'rejected', label: 'Rejected', n: counts.rejected },
          { value: 'all', label: 'All', n: counts.all },
        ]} />
        <span className="grow" />
        <Seg label="Sort" value={sort} onChange={setSort} options={[
          { value: 'new', label: 'Newest' }, { value: 'best', label: 'Best first' },
        ]} />
      </div>
      {shown.length === 0 ? (
        <div className="empty">
          <h3>{filter === 'ready' ? 'Nothing waiting for review' : 'No clips here'}</h3>
          <p>{filter === 'ready'
            ? 'Add a live channel or a past broadcast on the Desk. Finished clips land here within a minute of the moment.'
            : 'Clips move here as you approve, reject or render them.'}</p>
        </div>
      ) : (
        <div className="posters">
          {shown.map((c) => <Poster key={c.id} c={c} onOpen={() => onOpen(c.id)} />)}
        </div>
      )}
    </>
  )
}
