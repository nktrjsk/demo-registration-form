import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { backend, type CurrentMeeting, type MyEntry } from './api'


export function MeetingForm() {
  const { t } = useTranslation()
  const [meeting, setMeeting] = useState<CurrentMeeting | null>(null)
  const [loading, setLoading] = useState(true)
  const [entry, setEntry] = useState<MyEntry | null>(null)
  const [draftAttended, setDraftAttended] = useState(false)
  const [draftEntries, setDraftEntries] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [addingProject, setAddingProject] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [newProjectLeader, setNewProjectLeader] = useState('')
  const [projectError, setProjectError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    backend
      .get<{ meeting: CurrentMeeting | null }>('/meeting/current')
      .then(async ({ meeting: m }) => {
        if (!alive) return
        setMeeting(m)
        if (m) {
          const e = await backend.get<MyEntry>(`/meeting/${m.id}/my-entry`)
          if (!alive) return
          setEntry(e)
          setDraftAttended(e.attended)
          setDraftEntries(
            Object.fromEntries(e.project_entries.map(pe => [pe.project_id, pe.description]))
          )
        }
      })
      .catch(err => alive && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  if (loading) return <div className="card"><p>{t('form.loading')}</p></div>

  if (!meeting) {
    return (
      <div className="card">
        <h2>{t('form.title')}</h2>
        <p>{t('form.noMeeting')}</p>
      </div>
    )
  }

  if (!entry) return null

  const toggleProject = (id: number) => {
    setSavedAt(null)
    setDraftEntries(prev => {
      const copy = { ...prev }
      if (id in copy) delete copy[id]
      else copy[id] = ''
      return copy
    })
  }

  const updateDescription = (id: number, text: string) => {
    setSavedAt(null)
    setDraftEntries(prev => ({ ...prev, [id]: text }))
  }

  const submitNewProject = async () => {
    setProjectError(null)
    const name = newProjectName.trim()
    const leader = newProjectLeader.trim()
    if (!name || !leader) {
      setProjectError(t('form.projectFieldsRequired'))
      return
    }
    try {
      await backend.post(`/meeting/${meeting.id}/projects`, { name, leader })
      const refreshed = await backend.get<{ meeting: CurrentMeeting | null }>('/meeting/current')
      if (refreshed.meeting) setMeeting(refreshed.meeting)
      setNewProjectName('')
      setNewProjectLeader('')
      setAddingProject(false)
    } catch (e) {
      setProjectError(e instanceof Error ? e.message : String(e))
    }
  }

  const submit = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await backend.put<MyEntry>(`/meeting/${meeting.id}/my-entry`, {
        attended: draftAttended,
        project_entries: Object.entries(draftEntries).map(([pid, desc]) => ({
          project_id: parseInt(pid, 10),
          description: desc,
        })),
      })
      setEntry(updated)
      setSavedAt(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card">
      <h2>{t('form.title')}</h2>
      <p>{t('form.meetingDate')}: <strong>{meeting.meeting_date}</strong></p>
      <p>
        {t('form.yourEmail')}:{' '}
        <strong data-testid="meeting-form-email">{entry.user_email}</strong>
      </p>

      <label>
        <input
          type="checkbox"
          checked={draftAttended}
          onChange={e => { setDraftAttended(e.target.checked); setSavedAt(null) }}
        />{' '}
        {t('form.attending')}
      </label>

      <h3>{t('form.projects')}</h3>
      {!addingProject && (
        <button onClick={() => { setAddingProject(true); setProjectError(null) }}>
          {t('form.addProject')}
        </button>
      )}
      {addingProject && (
        <div className="add-project">
          <label>
            {t('form.projectName')}:{' '}
            <input
              type="text"
              value={newProjectName}
              onChange={e => setNewProjectName(e.target.value)}
            />
          </label>{' '}
          <label>
            {t('form.leader')}:{' '}
            <input
              type="text"
              value={newProjectLeader}
              onChange={e => setNewProjectLeader(e.target.value)}
            />
          </label>{' '}
          <button onClick={submitNewProject}>{t('form.add')}</button>{' '}
          <button onClick={() => { setAddingProject(false); setProjectError(null) }}>
            {t('form.cancel')}
          </button>
          {projectError && <p className="expired">{projectError}</p>}
        </div>
      )}
      {meeting.projects.length === 0 ? (
        <p>{t('form.noProjects')}</p>
      ) : (
        <ul className="project-list">
          {meeting.projects.map(p => (
            <li key={p.id} className="project-row">
              <label>
                <input
                  type="checkbox"
                  checked={p.id in draftEntries}
                  onChange={() => toggleProject(p.id)}
                />{' '}
                <strong>{p.name}</strong>{' '}
                <span className="project-leader">({t('form.leader')}: {p.leader})</span>
              </label>
              {p.id in draftEntries && (
                <textarea
                  value={draftEntries[p.id]}
                  onChange={e => updateDescription(p.id, e.target.value)}
                  placeholder={t('form.descriptionPlaceholder')}
                  rows={2}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      <button onClick={submit} disabled={saving}>
        {saving ? t('form.saving') : t('form.save')}
      </button>
      {savedAt && <span className="form-saved"> {t('form.savedAt', { time: savedAt.toLocaleTimeString() })}</span>}
      {error && <p className="expired">{error}</p>}
    </div>
  )
}
