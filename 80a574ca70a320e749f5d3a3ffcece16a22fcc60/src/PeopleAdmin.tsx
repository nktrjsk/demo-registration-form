import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backend, type Person } from './api'


export function PeopleAdmin() {
  const { t } = useTranslation()
  const [people, setPeople] = useState<Person[] | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    backend.get<{ people: Person[] }>('/people?limit=500')
      .then(d => setPeople(d.people))
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const filtered = useMemo(() => {
    if (!people) return []
    const q = query.trim().toLowerCase()
    if (!q) return people
    return people.filter(p =>
      p.display_name.toLowerCase().includes(q)
      || (p.email ?? '').toLowerCase().includes(q),
    )
  }, [query, people])

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
    setSaving(true)
    setError(null)
    try {
      const updated = await backend.patch<Person>(`/people/${editingId}`, {
        display_name: name,
      })
      setPeople(prev => (prev ? prev.map(p => p.id === updated.id ? updated : p)
        .sort((a, b) => a.display_name.localeCompare(b.display_name)) : prev))
      cancelEdit()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card">
      <h2>{t('peopleAdmin.title')}</h2>
      <p className="muted">{t('peopleAdmin.description')}</p>

      <input
        type="search"
        className="people-admin-search"
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder={t('peopleAdmin.searchPlaceholder')}
      />

      {error && <p className="expired">{error}</p>}

      {people === null ? (
        <p className="muted">{t('peopleAdmin.loading')}</p>
      ) : filtered.length === 0 ? (
        <p className="muted">{t('peopleAdmin.empty')}</p>
      ) : (
        <ul className="people-admin-list">
          {filtered.map(p => (
            <li key={p.id} className="people-admin-row">
              {editingId === p.id ? (
                <div className="leader-picker__edit people-admin-edit">
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
                    disabled={saving || !editDraft.trim()}
                    onClick={saveEdit}
                  >
                    {saving ? t('peopleAdmin.saving') : t('peopleAdmin.save')}
                  </button>
                  <button type="button" onClick={cancelEdit}>
                    {t('peopleAdmin.cancel')}
                  </button>
                </div>
              ) : (
                <>
                  <div className="people-admin-info">
                    <span className="people-admin-name">{p.display_name}</span>
                    {p.email ? (
                      <span className="people-admin-email">{p.email}</span>
                    ) : (
                      <span className="leader-picker__badge">{t('leaderPicker.placeholderBadge')}</span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="leader-picker__edit-btn"
                    aria-label={t('peopleAdmin.rename')}
                    title={t('peopleAdmin.rename')}
                    onClick={() => beginEdit(p)}
                  >
                    ✎
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
