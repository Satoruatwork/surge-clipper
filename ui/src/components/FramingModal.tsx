import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { errorText, load, toast } from '../lib/store'
import type { Source } from '../lib/types'
import { Modal, Range, Seg } from './ui'

type Box = [number, number, number, number]
const CAM_ASPECT = 1080 / 692 // the facecam band on a 1080x1920 split layout

export function FramingModal({ source, onClose }: { source: Source; onClose: () => void }) {
  const [shot, setShot] = useState<{ url: string; frame: number[] } | null>(null)
  const [err, setErr] = useState('')
  const st = source.settings
  const [layout, setLayout] = useState(st.layout ?? 'auto')
  const [mode, setMode] = useState<'auto' | 'manual' | 'none'>(st.cam_mode === 'none' ? 'none' : st.cam ? 'manual' : 'auto')
  const [box, setBox] = useState<Box | null>((st.cam ?? st.cam_auto ?? null) as Box | null)
  const [focus, setFocus] = useState(Math.round((st.focus ?? 0.5) * 100))
  const [shift, setShift] = useState(Number((st as { audio_shift_ms?: number }).audio_shift_ms ?? 0))
  const stage = useRef<HTMLDivElement>(null)
  const drag = useRef<{ kind: 'move' | 'size'; x: number; y: number; start: Box } | null>(null)

  useEffect(() => {
    api.post<{ url: string; frame: number[] }>(`/api/sources/${source.id}/snapshot`)
      .then((r) => {
        setShot(r)
        if (!box && r.frame) {
          const [fw, fh] = r.frame
          const w = Math.round(fw * 0.22)
          setBox([Math.round(fw * 0.04), Math.round(fh * 0.06), w, Math.round(w / CAM_ASPECT)])
        }
      })
      .catch((e) => setErr(errorText(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id])

  const frame = shot?.frame ?? st.frame ?? [1920, 1080]
  const scale = () => (stage.current ? stage.current.clientWidth / frame[0] : 1)

  const onDown = (kind: 'move' | 'size') => (e: React.PointerEvent) => {
    if (!box) return
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    drag.current = { kind, x: e.clientX, y: e.clientY, start: box }
    setMode('manual')
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const k = scale()
    const dx = (e.clientX - d.x) / k
    const dy = (e.clientY - d.y) / k
    const [x, y, w, h] = d.start
    if (d.kind === 'move') {
      setBox([Math.round(Math.min(Math.max(0, x + dx), frame[0] - w)), Math.round(Math.min(Math.max(0, y + dy), frame[1] - h)), w, h])
    } else {
      const nw = Math.max(80, Math.min(frame[0] - x, w + dx))
      setBox([x, y, Math.round(nw), Math.round(Math.min(frame[1] - y, nw / CAM_ASPECT))])
    }
  }
  const onUp = () => {
    drag.current = null
  }

  const save = async () => {
    try {
      await api.patch(`/api/sources/${source.id}`, {
        settings: {
          layout,
          focus: focus / 100,
          audio_shift_ms: shift,
          cam: mode === 'manual' && box ? box : null,
          cam_mode: mode === 'none' ? 'none' : null,
          ...(mode === 'auto' ? { cam_auto: null } : {}),
        },
      })
      toast('Framing saved. New clips from this stream use it.', 'good')
      void load.sources()
      onClose()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }

  const k = shot ? scale() : 0
  return (
    <Modal title={`Framing for ${source.stats.streamer || source.channel}`} onClose={onClose} wide>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 260px', gap: 22 }}>
        <div>
          {err && <p style={{ color: 'var(--sodium)', marginTop: 0 }}>{err}</p>}
          {!shot && !err && <p className="muted">Grabbing a frame…</p>}
          {shot && (
            <div className="cam-stage" ref={stage} onPointerMove={onMove} onPointerUp={onUp}>
              <img src={shot.url} alt="Current frame of the stream" onLoad={() => setBox((b) => (b ? [...b] as Box : b))} />
              {box && mode !== 'none' && k > 0 && (
                <div className="cam-box" onPointerDown={onDown('move')}
                  style={{ left: box[0] * k, top: box[1] * k, width: box[2] * k, height: box[3] * k }}>
                  <span className="tag">Facecam</span>
                  <span className="h" onPointerDown={onDown('size')} />
                </div>
              )}
            </div>
          )}
          <p className="faint" style={{ fontSize: 13, marginBottom: 0 }}>Drag the box over the webcam and pull the corner to size it. The box keeps the shape of the top band in a split clip, so nothing gets stretched.</p>
        </div>
        <div style={{ display: 'grid', gap: 18, alignContent: 'start' }}>
          <div className="field">
            <span className="label">Facecam</span>
            <Seg label="Facecam" value={mode} onChange={setMode} options={[
              { value: 'auto', label: 'Find it' }, { value: 'manual', label: 'My box' }, { value: 'none', label: 'None' },
            ]} />
            <span className="hint">{mode === 'auto' ? 'Surge looks for a steady face on the first clip.' : mode === 'none' ? 'Clips use the whole game frame.' : 'Your box is used for every clip.'}</span>
          </div>
          <div className="field">
            <span className="label">Layout</span>
            <Seg label="Layout" value={layout} onChange={setLayout} options={[
              { value: 'auto', label: 'Auto' }, { value: 'split', label: 'Split' }, { value: 'full', label: 'Full' },
              { value: 'blur', label: 'Blur' }, { value: 'pip', label: 'PiP' },
            ]} />
            <span className="hint">Split puts the facecam on top of the gameplay, the GTA clip standard.</span>
          </div>
          <div className="field">
            <label htmlFor="focus">Gameplay focus</label>
            <Range id="focus" value={focus} min={0} max={100} onChange={setFocus} suffix="%" />
            <span className="hint">Where the vertical crop sits across the game: 50% is the centre.</span>
          </div>
          <div className="field">
            <label htmlFor="shift">Voice sync</label>
            <Range id="shift" value={shift} min={-1000} max={1000} step={50} onChange={setShift} suffix=" ms" />
            <span className="hint">Only if this streamer's own stream is out of sync. Voice late: move right. Voice early: move left. New clips use it; re-render older ones.</span>
          </div>
          <button className="btn primary" onClick={save}>Save framing</button>
        </div>
      </div>
    </Modal>
  )
}
