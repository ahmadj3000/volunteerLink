import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { volunteerApi } from '../../lib/volunteer-api'

// Mirrors the backend validator (backend/volunteer/profiles.py).
// The backend stays the authority: these limits only give early feedback.
const AVAILABILITY = [
  ['weekdays', 'Weekdays'],
  ['weekends', 'Weekends'],
  ['evenings', 'Evenings'],
  ['emergency', 'Emergency call-outs'],
]
const LIMITS = { fullName: 100, location: 60, bio: 1000, skill: 40, skills: 30 }
const EMPTY = { fullName: '', location: '', skills: '', bio: '', availability: [] }

function toForm(profile) {
  return {
    fullName: profile.fullName ?? '',
    location: profile.location ?? '',
    skills: Array.isArray(profile.skills) ? profile.skills.join(', ') : '',
    bio: profile.bio ?? '',
    availability: Array.isArray(profile.availability) ? profile.availability : [],
  }
}

function parseSkills(text) {
  const seen = new Set()
  return text
    .split(',')
    .map((s) => s.trim().replace(/\s+/g, ' ').toLowerCase())
    .filter((s) => s && !seen.has(s) && seen.add(s))
}

function validate(form) {
  if (!form.fullName.trim()) return 'Please enter your full name.'
  if (form.fullName.trim().length > LIMITS.fullName) return `Name must be at most ${LIMITS.fullName} characters.`
  if (form.location.trim().length > LIMITS.location) return `Location must be at most ${LIMITS.location} characters.`
  if (form.bio.trim().length > LIMITS.bio) return `About you must be at most ${LIMITS.bio} characters.`
  const skills = parseSkills(form.skills)
  if (skills.length > LIMITS.skills) return `You can list at most ${LIMITS.skills} skills.`
  if (skills.some((s) => s.length > LIMITS.skill)) return `Each skill must be at most ${LIMITS.skill} characters.`
  return ''
}

function toBody(form) {
  // Optional fields left empty are omitted, so the backend removes them.
  const body = { fullName: form.fullName.trim() }
  if (form.location.trim()) body.location = form.location.trim()
  if (form.bio.trim()) body.bio = form.bio.trim()
  const skills = parseSkills(form.skills)
  if (skills.length) body.skills = skills
  if (form.availability.length) body.availability = form.availability
  return body
}

export default function Profile() {
  const [form, setForm] = useState(EMPTY)
  const [exists, setExists] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const savingRef = useRef(false)

  useEffect(() => {
    let active = true
    volunteerApi('/me/profile')
      .then((profile) => {
        if (!active) return
        setForm(toForm(profile))
        setExists(true)
      })
      .catch((err) => {
        if (!active) return
        if (err.status === 404) setExists(false) // first visit: empty form
        else setError(err.message)
      })
      .finally(() => active && setLoading(false))
    return () => { active = false }
  }, [])

  function change(event) {
    const { name, value } = event.target
    setForm((f) => ({ ...f, [name]: value }))
    setSaved(false)
  }

  function toggleAvailability(value) {
    setForm((f) => ({
      ...f,
      availability: f.availability.includes(value)
        ? f.availability.filter((v) => v !== value)
        : [...f.availability, value],
    }))
    setSaved(false)
  }

  async function save(event) {
    event.preventDefault()
    if (savingRef.current) return
    const problem = validate(form)
    if (problem) {
      setError(problem)
      return
    }
    savingRef.current = true
    setSaving(true)
    setError('')
    try {
      const profile = await volunteerApi('/me/profile', {
        method: 'PUT',
        body: JSON.stringify(toBody(form)),
      })
      setForm(toForm(profile))
      setExists(true)
      setSaved(true)
    } catch (err) {
      setError(err.message)
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <section className="panel">
        <p role="status">Loading your profile…</p>
      </section>
    )
  }

  return (
    <section className="panel">
      <span className="eyebrow">YOUR VOLUNTEER PROFILE</span>
      <h2>{exists ? 'My profile' : 'Create your profile'}</h2>
      <p>
        NGOs see this when you apply, and your skills drive your recommendations.
        {!exists && ' You need a profile before you can apply.'}
      </p>

      <form onSubmit={save} style={{ display: 'grid', gap: '1rem', maxWidth: 600 }} noValidate>
        <label>
          Full name *
          <input name="fullName" value={form.fullName} onChange={change}
            maxLength={LIMITS.fullName} required autoComplete="name" disabled={saving} />
        </label>

        <label>
          Location
          <input name="location" value={form.location} onChange={change}
            maxLength={LIMITS.location} placeholder="e.g. Hamra, Beirut" disabled={saving} />
        </label>

        <label>
          Skills (comma-separated)
          <input name="skills" value={form.skills} onChange={change}
            placeholder="e.g. first aid, logistics, arabic" disabled={saving} />
        </label>

        <fieldset disabled={saving}>
          <legend>Availability</legend>
          {AVAILABILITY.map(([value, label]) => (
            <label key={value}>
              <input type="checkbox" checked={form.availability.includes(value)}
                onChange={() => toggleAvailability(value)} />
              {label}
            </label>
          ))}
        </fieldset>

        <label>
          About you
          <textarea name="bio" value={form.bio} onChange={change}
            maxLength={LIMITS.bio} rows={4} disabled={saving}
            placeholder="Experience, languages, anything an NGO should know" />
        </label>
        <p>{form.bio.length}/{LIMITS.bio} characters</p>

        {error && <p role="alert">{error}</p>}
        {saved && (
          <p role="status">
            Profile saved. <Link to="/">Explore opportunities</Link>
          </p>
        )}

        <button type="submit" disabled={saving}>
          {saving ? 'Saving…' : exists ? 'Save changes' : 'Create profile'}
        </button>
      </form>
    </section>
  )
}
