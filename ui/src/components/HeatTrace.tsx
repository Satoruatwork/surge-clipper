import { useEffect, useRef } from 'react'
import type { Pulse } from '../lib/types'

const POOL = '#3dd6c6'
const SODIUM = '#ffb347'
const SUNSET = '#ff5e7e'

function sizeCanvas(c: HTMLCanvasElement) {
  const dpr = window.devicePixelRatio || 1
  const w = c.clientWidth
  const h = c.clientHeight
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
    c.width = Math.round(w * dpr)
    c.height = Math.round(h * dpr)
  }
  const ctx = c.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return { ctx, w, h }
}

function heatFill(ctx: CanvasRenderingContext2D, h: number, top: number) {
  const g = ctx.createLinearGradient(0, h, 0, top)
  g.addColorStop(0, 'rgba(61,214,198,0.05)')
  g.addColorStop(0.45, 'rgba(255,179,71,0.28)')
  g.addColorStop(1, 'rgba(255,94,126,0.55)')
  return g
}

function heatStroke(ctx: CanvasRenderingContext2D, h: number, top: number) {
  const g = ctx.createLinearGradient(0, h, 0, top)
  g.addColorStop(0, POOL)
  g.addColorStop(0.5, SODIUM)
  g.addColorStop(1, SUNSET)
  return g
}

/** Live excitement over the last `span` seconds, with the trigger threshold and the moments that were cut. */
export function LiveTrace({ pulses, span = 180, cuts = [] }: { pulses: Pulse[]; span?: number; cuts?: number[] }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const { ctx, w, h } = sizeCanvas(c)
    ctx.clearRect(0, 0, w, h)
    const pts = pulses.slice(-span)
    if (pts.length < 2) return
    const thr = pts[pts.length - 1].thr
    const maxV = Math.max(thr * 1.8, ...pts.map((p) => p.score), 1)
    const top = 6
    const y = (v: number) => h - 4 - (Math.min(v, maxV) / maxV) * (h - top - 4)
    const tEnd = pts.length ? pts[pts.length - 1].t : 0
    const x = (t: number) => w - ((tEnd - t) / span) * w

    // time grid every 30 s
    ctx.strokeStyle = 'rgba(222,205,255,0.06)'
    ctx.lineWidth = 1
    for (let t = Math.ceil((tEnd - span) / 30) * 30; t <= tEnd; t += 30) {
      const gx = Math.round(x(t)) + 0.5
      ctx.beginPath()
      ctx.moveTo(gx, top)
      ctx.lineTo(gx, h)
      ctx.stroke()
    }

    if (pts.length > 1) {
      ctx.beginPath()
      ctx.moveTo(x(pts[0].t), h)
      pts.forEach((p) => ctx.lineTo(x(p.t), y(p.score)))
      ctx.lineTo(x(pts[pts.length - 1].t), h)
      ctx.closePath()
      ctx.fillStyle = heatFill(ctx, h, top)
      ctx.fill()

      ctx.beginPath()
      pts.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p.score)) : ctx.moveTo(x(p.t), y(p.score))))
      ctx.strokeStyle = heatStroke(ctx, h, top)
      ctx.lineWidth = 1.75
      ctx.lineJoin = 'round'
      ctx.stroke()

      // audio level as a faint second line, so you can see a scream that chat hasn't caught yet
      ctx.beginPath()
      pts.forEach((p, i) => {
        const v = Math.max(0, p.audio) * 0.6
        return i ? ctx.lineTo(x(p.t), y(v)) : ctx.moveTo(x(p.t), y(v))
      })
      ctx.strokeStyle = 'rgba(124,184,255,0.35)'
      ctx.lineWidth = 1
      ctx.stroke()
    }

    // threshold
    ctx.setLineDash([4, 4])
    ctx.strokeStyle = 'rgba(244,236,228,0.38)'
    ctx.beginPath()
    ctx.moveTo(0, Math.round(y(thr)) + 0.5)
    ctx.lineTo(w, Math.round(y(thr)) + 0.5)
    ctx.stroke()
    ctx.setLineDash([])

    // cut markers
    cuts.forEach((t) => {
      if (t < tEnd - span) return
      const cx = Math.round(x(t)) + 0.5
      ctx.strokeStyle = SUNSET
      ctx.lineWidth = 1.5
      ctx.beginPath()
      ctx.moveTo(cx, top)
      ctx.lineTo(cx, h)
      ctx.stroke()
      ctx.fillStyle = SUNSET
      ctx.beginPath()
      ctx.moveTo(cx - 5, top - 2)
      ctx.lineTo(cx + 5, top - 2)
      ctx.lineTo(cx, top + 5)
      ctx.closePath()
      ctx.fill()
    })
  }, [pulses, span, cuts])

  const last = pulses[pulses.length - 1]
  if (pulses.length < 2) {
    return <div className="trace-empty">Listening. The baseline settles in about 30 seconds.</div>
  }
  return (
    <canvas
      ref={ref}
      role="img"
      aria-label={last ? `Excitement ${last.score.toFixed(1)} against a trigger level of ${last.thr.toFixed(1)}` : 'Waiting for signal'}
    />
  )
}

/** Whole-VOD excitement curve with the picked moments marked. */
export function VodTrace({ curve, step, peaks }: { curve: number[]; step: number; peaks: number[] }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const { ctx, w, h } = sizeCanvas(c)
    ctx.clearRect(0, 0, w, h)
    if (!curve.length) return
    const maxV = Math.max(...curve, 1)
    const top = 8
    const y = (v: number) => h - 2 - (v / maxV) * (h - top - 2)
    const x = (i: number) => (i / Math.max(1, curve.length - 1)) * w
    ctx.beginPath()
    ctx.moveTo(0, h)
    curve.forEach((v, i) => ctx.lineTo(x(i), y(v)))
    ctx.lineTo(w, h)
    ctx.closePath()
    ctx.fillStyle = heatFill(ctx, h, top)
    ctx.fill()
    ctx.beginPath()
    curve.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))))
    ctx.strokeStyle = heatStroke(ctx, h, top)
    ctx.lineWidth = 1.25
    ctx.stroke()
    peaks.forEach((p) => {
      const cx = Math.round(x(p / step)) + 0.5
      ctx.fillStyle = SUNSET
      ctx.beginPath()
      ctx.moveTo(cx - 4, 0)
      ctx.lineTo(cx + 4, 0)
      ctx.lineTo(cx, 6)
      ctx.closePath()
      ctx.fill()
    })
  }, [curve, step, peaks])
  return <canvas ref={ref} role="img" aria-label={`Excitement across the broadcast, ${peaks.length} moments picked`} />
}
