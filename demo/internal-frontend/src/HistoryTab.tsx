import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  backend,
  type MeetingSummary,
  type MeetingDetails,
  type Attendee,
  type MeetingProject,
} from './api'


function formatDate(iso: string, locale: string): string {
  const d = new Date(iso + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(locale, {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function formatDateLong(iso: string, locale: string): string {
  const d = new Date(iso + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(locale, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}


export function HistoryTab({ isAdmin }: { isAdmin: boolean }) {
  const { t, i18n } = useTranslation()
  const [meetings, setMeetings] = useState<MeetingSummary[] | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [details, setDetails] = useState<MeetingDetails | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    backend
      .get<{ meetings: MeetingSummary[] }>('/meetings')
      .then(d => setMeetings(d.meetings))
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const loadDetails = useCallback((id: number) => {
    setSelectedId(id)
    setDetails(null)
    backend
      .get<MeetingDetails>(`/meeting/${id}/details`)
      .then(setDetails)
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  if (meetings === null && !error) return <div className="card"><p>{t('history.loading')}</p></div>

  return (
    <div className="card">
      <h2>{t('history.title')}</h2>
      {error && <p className="expired">{error}</p>}
      {meetings && meetings.length === 0 && <p>{t('history.empty')}</p>}
      {meetings && meetings.length > 0 && (
        <ul className="history-list">
          {meetings.map(m => (
            <li key={m.id}>
              <button
                onClick={() => loadDetails(m.id)}
                className={selectedId === m.id ? 'active' : ''}
              >
                {formatDate(m.meeting_date, i18n.language)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {details && (
        <MeetingDetailView
          details={details}
          isAdmin={isAdmin}
          onChange={() => loadDetails(details.meeting.id)}
        />
      )}
    </div>
  )
}


function MeetingDetailView({
  details,
  isAdmin,
  onChange,
}: {
  details: MeetingDetails
  isAdmin: boolean
  onChange: () => void
}) {
  const { t, i18n } = useTranslation()
  return (
    <div className="history-detail">
      <h3>{formatDateLong(details.meeting.meeting_date, i18n.language)}</h3>
      <h4>{t('form.projects')}</h4>
      {details.meeting.projects.length === 0 ? (
        <p>{t('form.noProjects')}</p>
      ) : (
        <ul>
          {details.meeting.projects.map(p => (
            <li key={p.id}>
              <strong>{p.name}</strong> ({t('form.leader')}: {p.leader})
            </li>
          ))}
        </ul>
      )}
      <h4>{t('history.attendees')}</h4>
      <ul className="attendee-list">
        {details.attendees.map(a => (
          <AttendeeRow
            key={a.email}
            attendee={a}
            meetingId={details.meeting.id}
            projects={details.meeting.projects}
            isAdmin={isAdmin}
            onSaved={onChange}
          />
        ))}
      </ul>
    </div>
  )
}


function AttendeeRow({
  attendee,
  meetingId,
  projects,
  isAdmin,
  onSaved,
}: {
  attendee: Attendee
  meetingId: number
  projects: MeetingProject[]
  isAdmin: boolean
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [editing, setEditing] = useState(false)
  const [draftAttending, setDraftAttending] = useState(attendee.attending)
  const [draftEntries, setDraftEntries] = useState<Record<number, string>>(
    Object.fromEntries(attendee.project_entries.map(pe => [pe.project_id, pe.description]))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startEdit = () => {
    setDraftAttending(attendee.attending)
    setDraftEntries(
      Object.fromEntries(attendee.project_entries.map(pe => [pe.project_id, pe.description]))
    )
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      await backend.put(`/meeting/${meetingId}/entries/${encodeURIComponent(attendee.email)}`, {
        attending: draftAttending,
        project_entries: Object.entries(draftEntries).map(([pid, desc]) => ({
          project_id: parseInt(pid, 10),
          description: desc,
        })),
      })
      setEditing(false)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <li className="attendee-row">
        <span className={attendee.attending ? 'attending' : 'not-attending'}>
          {attendee.attending ? '✓' : '✗'}
        </span>{' '}
        {attendee.email}
        {attendee.project_entries.length > 0 && (
          <ul className="attendee-notes">
            {attendee.project_entries.map(pe => {
              const proj = projects.find(p => p.id === pe.project_id)
              return (
                <li key={pe.project_id}>
                  <em>{proj?.name ?? `#${pe.project_id}`}:</em> {pe.description}
                </li>
              )
            })}
          </ul>
        )}
        {isAdmin && (
          <button onClick={startEdit}>{t('history.edit')}</button>
        )}
      </li>
    )
  }

  const toggleProject = (id: number) => {
    setDraftEntries(prev => {
      const copy = { ...prev }
      if (id in copy) delete copy[id]
      else copy[id] = ''
      return copy
    })
  }

  return (
    <li className="attendee-row editing">
      <label>
        <input
          type="checkbox"
          checked={draftAttending}
          onChange={e => setDraftAttending(e.target.checked)}
        />{' '}
        <strong>{attendee.email}</strong>
      </label>
      <ul>
        {projects.map(p => (
          <li key={p.id}>
            <label>
              <input
                type="checkbox"
                checked={p.id in draftEntries}
                onChange={() => toggleProject(p.id)}
              />{' '}
              {p.name}
            </label>
            {p.id in draftEntries && (
              <textarea
                value={draftEntries[p.id]}
                onChange={e => setDraftEntries({ ...draftEntries, [p.id]: e.target.value })}
                rows={2}
              />
            )}
          </li>
        ))}
      </ul>
      <button className="primary" onClick={save} disabled={saving}>
        {saving ? t('form.saving') : t('history.save')}
      </button>{' '}
      <button onClick={() => setEditing(false)}>{t('history.cancel')}</button>
      {error && <p className="expired">{error}</p>}
    </li>
  )
}
