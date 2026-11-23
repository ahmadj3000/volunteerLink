# Applications API contract (proposed)

Defined by: Ahmad Jaber
Date: 23 Nov 2026
Status: **Proposed, not implemented.** Formats may change slightly during
implementation; any change gets announced and reflected here.

## Conventions (same as the NGO API)

- Auth: `Authorization: Bearer <Cognito access token>` on every route except `/health`.
- Body and responses: JSON.
- Errors: `{"detail": "<human-readable message>"}`, so `ngoApi`-style helpers work as-is.
  - 400 invalid input, 401 missing/invalid token, 403 wrong role / not the owner,
    404 not found, 409 conflict (state doesn't allow the action), 503 AWS unavailable.
- Pagination: `?cursor=<value>` in, `"nextCursor": "<value>" | null` out.
  Treat the cursor as an opaque string; just send back what you received.
- Timestamps: UTC ISO 8601 strings, e.g. `2026-11-23T19:00:00+00:00`.
- Application status values: `pending`, `accepted`, `rejected`, `expired`
  (`expired` = the NGO never decided before the workflow gave up).

## Which API serves what

| Frontend page | Calls | Base URL env var |
|---|---|---|
| Apply button, My applications, Profile | **Volunteer API** (Volunteer account) | `VITE_VOLUNTEER_API_URL` (URL TBD after ECS deploy) |
| NGO applicants + accept/reject | **NGO API** (NGO account), which calls the Volunteer API internally | `VITE_NGO_API_URL` (existing) |

The browser never calls the other account's internal routes. NGO screens only
talk to the NGO API, which checks the NGO owns the opportunity before asking
the Volunteer account for applicants or recording a decision.

## Application object

```json
{
  "applicationId": "<opportunityId>_<volunteerId>",
  "opportunityId": "uuid",
  "volunteerId": "cognito sub",
  "ngoId": "cognito sub of owning NGO",
  "opportunityTitle": "Animal shelter support",
  "opportunityStartAt": "2026-12-05T08:00:00+00:00",
  "message": "I have first aid training",
  "status": "pending",
  "appliedAt": "2026-11-23T19:00:00+00:00",
  "decidedAt": null
}
```

- `applicationId` is built from the opportunity and volunteer IDs, so one
  volunteer can apply to one opportunity only once.
- `opportunityTitle` and `opportunityStartAt` are copied at apply time, so
  My applications doesn't need to call the NGO account for every row.
- `message` is optional (max 500 characters).

---

## Volunteer API: browser routes (volunteer group)

### POST /applications
Apply to an open opportunity.

Request:
```json
{ "opportunityId": "uuid", "message": "optional, max 500 chars" }
```

Responses:
- `201` → `{ "application": <Application> }` with `status: "pending"`
- `400` bad body / unknown fields
- `403` caller isn't in the volunteer group
- `404` opportunity doesn't exist
- `409` already applied / opportunity not open / volunteer has no profile yet

### GET /me/applications?cursor=
The signed-in volunteer's applications, newest first.

`200` →
```json
{ "items": [<Application>, ...], "nextCursor": null }
```

### Profile (already implemented, for reference)
- `GET /me/profile` → profile object, or `404` if none yet
- `PUT /me/profile` with `{ "fullName", "location", "bio", "skills", "availability" }`
  → saved profile. `availability` values: `weekdays`, `weekends`, `evenings`, `emergency`.

---

## NGO API: browser routes (approved NGO that owns the opportunity)

### GET /ngo/opportunities/{opportunityId}/applicants?cursor=
Applicants for one of the caller's opportunities. Ranked by match score
once Tarek's ranking is integrated. Until then, `score` is `null` and the
order is newest first.

`200` →
```json
{
  "opportunityId": "uuid",
  "capacity": 10,
  "acceptedCount": 3,
  "items": [
    {
      "applicationId": "...",
      "volunteerId": "...",
      "fullName": "Test User",
      "location": "Beirut",
      "skills": ["first aid", "driving"],
      "availability": ["weekends"],
      "message": "...",
      "status": "pending",
      "appliedAt": "...",
      "decidedAt": null,
      "score": null
    }
  ],
  "nextCursor": null
}
```
- `403` not an approved NGO, or not the owner of this opportunity
- `404` opportunity not found

### POST /ngo/applications/{applicationId}/decision
Accept or reject one applicant.

Request:
```json
{ "decision": "accepted" }
```
(`"accepted"` or `"rejected"`)

Responses:
- `200` → `{ "application": <Application> }` with the new status and `decidedAt`
- `403` not the owner
- `404` application not found
- `409` already decided, expired, or (for accept) capacity is full

The volunteer is emailed automatically after a decision. The frontend does
nothing extra.

---

## Internal cross-account routes (not for the browser)

Signed with IAM (SigV4) using a short-lived STS role. Defined here so both
backends agree on them.

**Volunteer API, called by the NGO backend:**
- `GET /internal/opportunities/{opportunityId}/applications?cursor=`
- `POST /internal/applications/{applicationId}/decision`
  with `{ "decision", "capacity", "decidedBy" }`

**NGO API, called by the Volunteer backend (needed from Malek):**
- `GET /internal/opportunities/{opportunityId}` →
  `{ opportunityId, ngoId, title, status, capacity, startAt, endAt, contactEmail }`
  (`contactEmail` = where "new applicant" and reminder emails go; needs a
  `contactEmail` field on NGO registration.)

## Open points to agree

1. The two NGO-side applicant routes in `backend/ngo/` are Ahmad's feature
   (applicants + decision handling). Ahmad writes them as a separate
   `applications.py` module; Malek reviews since it's his codebase.
2. The volunteer **profile page** isn't assigned in the team split. Proposal:
   Ahmad, since he owns the profile API.
3. Apply, My applications and NGO applicants pages are Ahmad's in the split.
   Malek has offered to connect them; Ahmad and Malek to confirm who does it.
4. Malek: add `GET /internal/opportunities/{opportunityId}` and a
   `contactEmail` field on NGO registration.
5. Withdrawing an application is out of scope unless time allows.
