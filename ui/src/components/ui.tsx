import { useEffect, useRef, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { useStore } from '../lib/store'

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="switch-row">
      <button type="button" role="switch" aria-checked={checked} className="toggle" onClick={() => onChange(!checked)} />
      <span>{label}</span>
    </label>
  )
}

export function Seg<T extends string>({ value, options, onChange, label }: {
  value: T
  options: { value: T; label: ReactNode; n?: number }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
          {o.n !== undefined && <span className="n">{o.n}</span>}
        </button>
      ))}
    </div>
  )
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  )
}

export function Range({ value, min, max, step = 1, onChange, suffix = '', id }: {
  value: number; min: number; max: number; step?: number; onChange: (v: number) => void; suffix?: string; id?: string
}) {
  return (
    <div className="range">
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <output>{value}{suffix}</output>
    </div>
  )
}

export function ChipsInput({ values, onChange, placeholder, normalize }: {
  values: string[]; onChange: (v: string[]) => void; placeholder?: string; normalize?: (s: string) => string
}) {
  const [draft, setDraft] = useState('')
  const add = (raw: string) => {
    const items = raw.split(/[,\s]+/).map((s) => (normalize ? normalize(s) : s.trim())).filter(Boolean)
    if (!items.length) return
    onChange([...values, ...items.filter((x) => !values.includes(x))])
    setDraft('')
  }
  return (
    <div className="chips-input">
      {values.map((v) => (
        <span className="chip" key={v}>
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
            <X size={12} />
          </button>
        </span>
      ))}
      <input
        value={draft}
        placeholder={values.length ? '' : placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            add(draft)
          } else if (e.key === 'Backspace' && !draft && values.length) {
            onChange(values.slice(0, -1))
          }
        }}
        onBlur={() => add(draft)}
      />
    </div>
  )
}

export function Avatar({ src, name, platform, large }: { src?: string | null; name?: string | null; platform?: string; large?: boolean }) {
  const [broken, setBroken] = useState(false)
  const initials = (name ?? '?').replace(/^@/, '').slice(0, 2).toUpperCase()
  return (
    <span className={`avatar${large ? ' lg' : ''}`}>
      {src && !broken ? (
        <img className={`avatar${large ? ' lg' : ''}`} src={src} alt="" onError={() => setBroken(true)} referrerPolicy="no-referrer" />
      ) : (
        initials
      )}
      {platform && <span className={`pf ${platform}`} aria-label={platform} />}
    </span>
  )
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])
}

export function Modal({ title, onClose, children, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEscape(onClose)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => ref.current?.focus(), [])
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className={`modal${wide ? ' wide' : ''}`} role="dialog" aria-modal="true" tabIndex={-1} ref={ref}>
        <div className="overlay-head">
          <h2>{title}</h2>
          <button className="btn ghost icon close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="overlay-body">{children}</div>
      </div>
    </>
  )
}

export function Drawer({ title, onClose, children, footer, aside }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; aside?: ReactNode
}) {
  useEscape(onClose)
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-modal="true">
        <div className="overlay-head">
          <h2>{title}</h2>
          {aside}
          <button className="btn ghost icon close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        {children}
        {footer && <div className="editor-foot">{footer}</div>}
      </div>
    </>
  )
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts)
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>
      ))}
    </div>
  )
}

export const CLIP_STEPS = ['queued', 'cutting', 'transcribing', 'writing', 'rendering'] as const
export const STEP_LABEL: Record<string, string> = {
  queued: 'Waiting for a free worker',
  cutting: 'Cutting the moment',
  transcribing: 'Transcribing for captions',
  writing: 'Writing hook and title',
  rendering: 'Rendering 9:16',
}

export function Steps({ status }: { status: string }) {
  const i = CLIP_STEPS.indexOf(status as (typeof CLIP_STEPS)[number])
  return (
    <div className="steps" aria-label={STEP_LABEL[status] ?? status}>
      {CLIP_STEPS.map((s, j) => (
        <i key={s} className={j < i ? 'done' : j === i ? 'now' : ''} />
      ))}
    </div>
  )
}

export function confirmDanger(text: string): boolean {
  return window.confirm(text)
}
