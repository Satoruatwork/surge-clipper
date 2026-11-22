import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { errorText, load, toast } from '../lib/store'
import type { Source } from '../lib/types'
import { Modal, Range, Seg } from './ui'

type Box = [number, number, number, number]
type Grip = 'move' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'
const GRIPS: Exclude<Grip, 'move'>[] = ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']
const MIN_SIDE = 40 // smallest box side, in stream pixels
const START_ASPECT = 1080 / 692 // a new box starts in the shape of a split clip's usual top band
// In a split clip the facecam band takes the box's shape, between these shares of the 1920 px height (render.py)
const BAND = { min: 0.22, max: 0.55 }

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi))

/** The box after dragging one of its grips (or the whole box) by dx, dy stream pixels, kept inside the frame. */
function moveBox(start: Box, grip: Grip, dx: number, dy: number, fw: number, fh: number): Box {
  let [x, y, w, h] = start
  if (grip === 'move') return [Math.round(clamp(x + dx, 0, fw - w)), Math.round(clamp(y + dy, 0, fh - h)), w, h]
  if (grip.includes('e')) w = clamp(w + dx, MIN_SIDE, fw - x)
  if (grip.includes('s')) h = clamp(h + dy, MIN_SIDE, fh - y)
  if (grip.includes('w')) {
    const right = x + w
    x = clamp(x + dx, 0, right - MIN_SIDE)
    w = right - x
  }
  if (grip.includes('n')) {
    const bottom = y + h
    y = clamp(y + dy, 0, bottom - MIN_SIDE)
    h = bottom - y
  }
  return [Math.round(x), Math.round(y), Math.round(w), Math.round(h)]
}

function bandNote(box: Box): string {
  const share = (1080 * box[3]) / box[2] / 1920
  if (share < BAND.min) return `Very wide: a split clip shows this box's middle at ${Math.round(BAND.min * 100)}% of the height and trims its sides.`
  if (share > BAND.max) return `Very tall: a split clip shows this box's middle at ${Math.round(BAND.max * 100)}% of the height and trims its top and bottom.`
  return `In a split clip the facecam takes the top ${Math.round(share * 100)}% of the video, exactly this box.`
}

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
  const drag = useRef<{ grip: Grip; x: number; y: number; start: Box } | null>(null)

  useEffect(() => {
    api.post<{ url: string; frame: number[] }>(`/api/sources/${source.id}/snapshot`)
      .then((r) => {
        setShot(r)
        if (!box && r.frame) {
          const [fw, fh] = r.frame
          const w = Math.round(fw * 0.22)
          setBox([Math.round(fw * 0.04), Math.round(fh * 0.06), w, Math.round(w / START_ASPECT)])
        }
      })
      .catch((e) => setErr(errorText(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id])

  const frame = shot?.frame ?? st.frame ?? [1920, 1080]
  const scale = () => (stage.current ? stage.current.clientWidth / frame[0] : 1)

  const onDown = (grip: Grip) => (e: React.PointerEvent) => {
    if (!box) return
    e.preventDefault()
    e.stopPropagation()
    drag.current = { grip, x: e.clientX, y: e.clientY, start: box }
    setMode('manual')
    try { // keep receiving moves when the pointer leaves the grip; dragging works without it too
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch { /* no capture for this pointer */ }
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const k = scale()
    setBox(moveBox(d.start, d.grip, (e.clientX - d.x) / k, (e.clientY - d.y) / k, frame[0], frame[1]))
  }
  const onUp = () => {
    drag.current = null
  }
  /** Arrow keys move the box; Shift+arrows move its right or bottom edge; Alt for single pixels. */
  const onKey = (e: React.KeyboardEvent) => {
    const step = e.altKey ? 1 : 8
    const v = ({ ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] } as Record<string, number[]>)[e.key]
    if (!v || !box) return
    e.preventDefault()
    setMode('manual')
    setBox(moveBox(box, e.shiftKey ? (v[0] ? 'e' : 's') : 'move', v[0], v[1], frame[0], frame[1]))
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
            <div className="cam-stage" ref={stage} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
              <img src={shot.url} alt="Current frame of the stream" onLoad={() => setBox((b) => (b ? [...b] as Box : b))} />
              {box && mode !== 'none' && k > 0 && (
                <div className="cam-box" onPointerDown={onDown('move')} onKeyDown={onKey} tabIndex={0} role="group"
                  aria-label={`Facecam box, ${box[2]} by ${box[3]} pixels. Arrow keys move it, Shift with arrows resizes it.`}
                  style={{ left: box[0] * k, top: box[1] * k, width: box[2] * k, height: box[3] * k }}>
                  <span className="tag">Facecam · {box[2]}×{box[3]}</span>
                  {GRIPS.map((g) => <span key={g} className={`h ${g}`} onPointerDown={onDown(g)} />)}
                </div>
              )}
            </div>
          )}
          <p className="faint" style={{ fontSize: 13, marginBottom: 0 }}>
            Drag the box over the webcam and pull any edge or corner to fit it; arrow keys move it, Shift with arrows resizes.
            {box && mode !== 'none' ? ` ${bandNote(box)}` : ''}
          </p>
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
