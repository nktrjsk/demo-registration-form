import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backend, type Person } from './api'


interface Props {
  value: Person | null
  onChange: (p: Person | null) => void
  placeholder?: string
  id?: string
  isAdmin?: boolean
}


export function LeaderPicker({ value, onChange, placeholder, id, isAdmin }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [people, setPeople] = useState<Person[] | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  const reload = async () => {
    try {
      const d = await backend.get<{ people: Person[] }>('/people?limit=200')
      setPeople(d.people)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    reload()
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
    return people.filter(p =>
      p.display_name.toLowerCase().includes(q)
      || (p.email ?? '').toLowerCase().includes(q),
    )
  }, [query, people])

  const exactNameExists = useMemo(() => {
    if (!people) return false
    const q = query.trim().toLowerCase()
    if (!q) return false
    return people.some(p => p.display_name.toLowerCase() === q)
  }, [query, people])

  const pick = (p: Person) => {
    onChange(p)
    setQuery('')
    setOpen(false)
  }

  const createPlaceholder = async () => {
    const name = query.trim()
    if (!name) return
    setCreating(true)
    setError(null)
    try {
      const created = await backend.post<Person>('/people', { display_name: name })
      // Refresh the local catalog so future opens see the new entry.
      setPeople(prev => (prev ? [...prev, created].sort((a, b) =>
        a.display_name.localeCompare(b.display_name),
      ) : [created]))
      pick(created)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  const beginEdit = (p: Person) => {
    setEditingId(p.id)
    setEditDraft(p.display_name)
    setError(null)
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditDraft('')
  }

  const saveEdit = async () => {
    if (editingId === null) return
    const name = editDraft.trim()
    if (!name) return
    setSavingEdit(true)
    setError(null)
    try {
      const updated = await backend.patch<Person>(`/people/${editingId}`, {
        display_name: name,
      })
      setPeople(prev => (prev ? prev.map(p => p.id === updated.id ? updated : p)
        .sort((a, b) => a.display_name.localeCompare(b.display_name)) : prev))
      if (value?.id === updated.id) onChange(updated)
      cancelEdit()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSavingEdit(false)
    }
  }

  const displayInInput = open ? query : (value?.display_name ?? '')

  return (
    <div className="leader-picker" ref={wrapRef}>
      <input
        id={id}
        type="text"
        autoComplete="off"
        value={displayInInput}
        placeholder={placeholder ?? t('leaderPicker.placeholder')}
        onFocus={() => { setQuery(''); setOpen(true) }}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onKeyDown={e => {
          if (e.key === 'Escape') setOpen(false)
          if (e.key === 'Enter' && query.trim() && !exactNameExists) {
            e.preventDefault()
            createPlaceholder()
          }
        }}
      />
      {open && (
        <ul className="leader-picker__list" role="listbox">
          {people === null && !error && (
            <li className="leader-picker__empty">{t('leaderPicker.loading')}</li>
          )}
          {error && <li className="leader-picker__empty">{error}</li>}
          {people !== null && filtered.length === 0 && !query.trim() && (
            <li className="leader-picker__empty">{t('leaderPicker.empty')}</li>
          )}
          {filtered.map(p => (
            <li key={p.id} className="leader-picker__row">
              {editingId === p.id ? (
                <div className="leader-picker__edit" onMouseDown={e => e.stopPropagation()}>
                  <input
                    type="text"
                    autoFocus
                    value={editDraft}
                    onChange={e => setEditDraft(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') { e.preventDefault(); saveEdit() }
                      if (e.key === 'Escape') { e.preventDefault(); cancelEdit() }
                    }}
                  />
                  <button
                    type="button"
                    className="primary"
                    disabled={savingEdit || !editDraft.trim()}
                    onMouseDown={e => e.preventDefault()}
                    onClick={saveEdit}
                  >
                    {savingEdit ? t('leaderPicker.saving') : t('leaderPicker.save')}
                  </button>
                  <button
                    type="button"
                    onMouseDown={e => e.preventDefault()}
                    onClick={cancelEdit}
                  >
                    {t('leaderPicker.cancel')}
                  </button>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    role="option"
                    aria-selected={p.id === value?.id}
                    className={p.id === value?.id ? 'selected' : ''}
                    onMouseDown={e => e.preventDefault()}
                    onClick={() => pick(p)}
                  >
                    <span className="leader-picker__name">{p.display_name}</span>
                    {p.email ? (
                      <span className="leader-picker__email">{p.email}</span>
                    ) : (
                      <span className="leader-picker__badge">{t('leaderPicker.placeholderBadge')}</span>
                    )}
                  </button>
                  {isAdmin && (
                    <button
                      type="button"
                      className="leader-picker__edit-btn"
                      aria-label={t('leaderPicker.rename')}
                      title={t('leaderPicker.rename')}
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => beginEdit(p)}
                    >
                      ✎
                    </button>
                  )}
                </>
              )}
            </li>
          ))}
          {query.trim() && !exactNameExists && (
            <li className="leader-picker__create">
              <button
                type="button"
                onMouseDown={e => e.preventDefault()}
                onClick={createPlaceholder}
                disabled={creating}
              >
                {creating
                  ? t('leaderPicker.creating')
                  : t('leaderPicker.createOption', { name: query.trim() })}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
