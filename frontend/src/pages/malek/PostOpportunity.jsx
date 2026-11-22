import { useState } from 'react'
import { ngoApi } from '../../lib/ngo-api'

const initialForm = {
  title: '',
  description: '',
  location: '',
  skills: '',
  startAt: '',
  endAt: '',
  capacity: '10',
  isCrisis: false,
}

function localDate(value) {
  const date = new Date(value)
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return shifted.toISOString().slice(0, 16)
}

export default function PostOpportunity({ opportunity = null, onSaved, onCancel }) {
  const editing = Boolean(opportunity)
  const [form, setForm] = useState(() => opportunity ? {
    title: opportunity.title,
    description: opportunity.description,
    location: opportunity.location,
    skills: (opportunity.skills || []).join(', '),
    startAt: localDate(opportunity.startAt),
    endAt: localDate(opportunity.endAt),
    capacity: String(opportunity.capacity),
    isCrisis: Boolean(opportunity.isCrisis),
  } : initialForm)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState(null)

  function change(event) {
    const { name, value, type, checked } = event.target
    setForm((previous) => ({
      ...previous,
      [name]: type === 'checkbox' ? checked : value,
    }))
  }

  async function submit(event) {
    event.preventDefault()
    setError('')
    setCreated(null)

    const start = new Date(form.startAt)
    const end = new Date(form.endAt)

    if (
      !Number.isFinite(start.getTime()) ||
      !Number.isFinite(end.getTime()) ||
      start <= new Date() ||
      end <= start
    ) {
      setError('Choose a future start time and an end time after it.')
      return
    }

    const skills = form.skills.split(',').map((skill) => skill.trim()).filter(Boolean)
    if (skills.length > 30 || skills.some((skill) => skill.length > 80)) {
      setError('Enter up to 30 skills, each at most 80 characters.')
      return
    }

    setBusy(true)
    try {
      const endpoint = editing
        ? `/ngo/opportunities/${encodeURIComponent(opportunity.opportunityId)}`
        : '/ngo/opportunities'
      const data = await ngoApi(endpoint, {
        method: editing ? 'PATCH' : 'POST',
        body: JSON.stringify({
          title: form.title.trim(),
          description: form.description.trim(),
          location: form.location.trim(),
          skills,
          startAt: start.toISOString(),
          endAt: end.toISOString(),
          capacity: Number(form.capacity),
          isCrisis: form.isCrisis,
          ...(editing ? { expectedUpdatedAt: opportunity.updatedAt } : {}),
        }),
      })
      if (editing) {
        onSaved(data.opportunity)
      } else {
        setCreated(data.opportunity)
        setForm(initialForm)
      }
    } catch (error) {
      setError(error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section style={{ borderTop: '1px solid #ddd', marginTop: 24, paddingTop: 20 }}>
      <h3>{editing ? 'Edit opportunity' : 'Post an opportunity'}</h3>
      <p>Dates use your device’s local time and are stored in UTC.</p>
      <form onSubmit={submit} style={{ display: 'grid', gap: '1rem', maxWidth: 600 }}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, display: 'grid', gap: '1rem' }}>
          <label>
            Title
            <input name="title" value={form.title} onChange={change}
              minLength={2} maxLength={160} required />
          </label>
          <label>
            Description
            <textarea name="description" value={form.description} onChange={change}
              minLength={10} maxLength={4000} rows={5} required
              style={{ display: 'block', width: '100%' }} />
          </label>
          <label>
            Location in Lebanon
            <input name="location" value={form.location} onChange={change}
              minLength={2} maxLength={160} required />
          </label>
          <label>
            Required skills, separated by commas
            <input name="skills" value={form.skills} onChange={change}
              placeholder="first aid, driving" maxLength={2429} />
          </label>
          <label>
            Start
            <input type="datetime-local" name="startAt"
              value={form.startAt} onChange={change} required />
          </label>
          <label>
            End
            <input type="datetime-local" name="endAt"
              value={form.endAt} onChange={change} required />
          </label>
          <label>
            Volunteer capacity
            <input type="number" name="capacity" min={1} max={10000} step={1}
              value={form.capacity} onChange={change} required />
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <input type="checkbox" name="isCrisis" checked={form.isCrisis}
              onChange={change} style={{ width: 'auto' }} />
            Urgent crisis-response opportunity
          </label>
          <button type="submit">
            {busy ? 'Saving…' : editing ? 'Save changes' : 'Post opportunity'}
          </button>
          {editing && (
            <button type="button" onClick={onCancel}>Cancel editing</button>
          )}
        </fieldset>
      </form>
      {error && <p role="alert">{error}</p>}
      {created && (
        <div role="status">
          <p><strong>Posted successfully: {created.title}</strong></p>
          <p>Opportunity ID: {created.opportunityId}</p>
        </div>
      )}
    </section>
  )
}
