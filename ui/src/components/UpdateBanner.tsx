import { useState } from 'react'
import { CircleArrowUp, X } from 'lucide-react'
import { openUpdate, useStore } from '../lib/store'

const KEY = 'surge.update.hidden'
const read = () => {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}
const write = (v: string) => {
  try {
    localStorage.setItem(KEY, v)
  } catch {
    /* storage blocked: the banner just comes back next start */
  }
}

/** Shown when GitHub has a newer release. Hiding it lasts until the version after that one. */
export function UpdateBanner() {
  const u = useStore((s) => s.update)
  const [hidden, setHidden] = useState(read)
  const [notes, setNotes] = useState(false)
  if (!u?.available || !u.latest || hidden === u.latest) return null
  const latest = u.latest
  const dismiss = () => {
    write(latest)
    setHidden(latest)
  }
  const mb = u.size ? ` (${Math.round(u.size / 1048576)} MB)` : ''
  return (
    <div className="update-bar" role="status">
      <CircleArrowUp size={20} strokeWidth={1.8} aria-hidden="true" />
      <div className="txt">
        <b>Surge {latest} is available</b>
        <span>You have {u.current}. Updating keeps your clips, accounts and settings.</span>
      </div>
      <button className="btn sm ghost" aria-expanded={notes} onClick={() => setNotes(!notes)}>What's new</button>
      <button className="btn sm primary" onClick={() => void openUpdate()}>Download update{mb}</button>
      <button className="btn sm ghost icon" aria-label={`Hide until the version after ${latest}`} title="Hide until the next version" onClick={dismiss}>
        <X size={16} />
      </button>
      {notes && (
        <div className="notes">
          <pre>{u.notes || 'No release notes.'}</pre>
          {u.sha256 && <p>Installer SHA-256: <code>{u.sha256}</code></p>}
        </div>
      )}
    </div>
  )
}
