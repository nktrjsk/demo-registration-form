import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backend } from './api'


interface Props {
  value: string
  onChange: (email: string) => void
  placeholder?: string
  id?: string
}


export function LeaderPicker({ value, onChange, placeholder, id }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [people, setPeople] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    backend
      .get<{ people: string[] }>('/people?limit=200')
      .then(d => alive && setPeople(d.people))
      .catch(e => alive && setError(e instanceof Error ? e.message : String(e)))
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const filtered = useMemo(() => {
    if (!people) return []
    const q = query.trim().toLowerCase()
    if (!q) return people
    return people.filter(p => p.toLowerCase().includes(q))
  }, [query, people])

  const pick = (email: string) => {
    onChange(email)
    setQuery('')
    setOpen(false)
  }

  return (
    <div className="leader-picker" ref={wrapRef}>
      <input
        id={id}
        type="text"
        autoComplete="off"
        value={open ? query : value}
        placeholder={placeholder ?? t('leaderPicker.placeholder')}
        onFocus={() => { setQuery(''); setOpen(true) }}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onKeyDown={e => { if (e.key === 'Escape') setOpen(false) }}
      />
      {open && (
        <ul className="leader-picker__list" role="listbox">
          {people === null && !error && (
            <li className="leader-picker__empty">{t('leaderPicker.loading')}</li>
          )}
          {error && <li className="leader-picker__empty">{error}</li>}
          {people !== null && filtered.length === 0 && (
            <li className="leader-picker__empty">
              {t('leaderPicker.noMatches', { query })}
            </li>
          )}
          {filtered.map(email => (
            <li key={email}>
              <button
                type="button"
                role="option"
                aria-selected={email === value}
                className={email === value ? 'selected' : ''}
                onMouseDown={e => e.preventDefault()}
                onClick={() => pick(email)}
              >
                {email}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
