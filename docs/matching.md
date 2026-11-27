# AI matching — design, contracts and team asks (Tarek)

Status: **code written and unit-tested locally, NOT deployed.** Nothing in AWS
was created or changed for this slice yet. Everything under "Asks" is a
proposal for Ahmad (ECS/IAM/API Gateway) and Malek (NGO backend) to apply.

Code: `backend/volunteer/matching/` · tests: `backend/volunteer/tests/test_matching*.py`
· page: `frontend/src/pages/tarek/Recommendations.jsx`

---

## 1. The professor's constraint and what it changed

Backends run as **ECS Fargate containers**, not Lambdas. For matching that means:

| Before (original plan) | Now |
|---|---|
| A separate "Matching Lambda" | A plain Python package `matching/` inside the Volunteer container (`backend/volunteer`) |
| Ranking called over the network | Ahmad's applicants code calls `rank_volunteers(...)` as an ordinary function in the same process |
| OpenSearch k-NN for vector search | No OpenSearch (also blocked by the cost SCP). Cosine similarity in pure Python over DynamoDB rows — fine at demo scale |
| Could read opportunities from wherever | The Volunteer backend **must not read NGO tables**. It keeps its own read copy, `vl-opportunity-index`, filled by the NGO backend (§6) |

## 2. Deployed vs planned

| Thing | State |
|---|---|
| `vl-volunteers`, Cognito pool `us-east-1_is9qkiZoh`, Volunteer ECS service, API `57bc1lggo1` | Deployed (Ahmad, runbook) |
| `vl-profile` Lambda | Deployed but **retired** from the API (kept as rollback) |
| `matching/` package, `GET /me/recommendations`, `PUT /internal/opportunities/{id}` | Code only, in this branch |
| Tables `vl-volunteer-embeddings`, `vl-opportunity-index` | Planned (§7) |
| Bedrock permission on `vl-volunteer-task-role` | Planned (§7) |
| NGO → Volunteer opportunity sync | **Decided: option (a)** (27 Nov 2026); NGO-side call not built yet |

## 3. Titan access check

**SCP (checked in the repo, `infra/org/policies/cost-guardrails.json`):** statement
`OnlyTitanEmbeddingModels` denies `bedrock:InvokeModel` on every model **except**
`arn:aws:bedrock:*::foundation-model/amazon.titan-embed-*`. So Titan embeddings are
allowed, everything else is blocked. `region-lock.json` only allows us-east-1, which is
where we call it. `DenyBedrockProvisionedAndTraining` doesn't affect on-demand calls.

**Verified in the Volunteer account — Tarek, 27 Nov 2026** (SSO `tarek.kaadan`,
AdministratorAccess): `amazon.titan-embed-text-v2:0` is ACTIVE, ON_DEMAND,
`authorizationStatus: AUTHORIZED`; one test `invoke-model` returned 512 numbers with
length 1.0 (normalized), 4 input tokens. No resources created.

To repeat (`ReadOnlyAccess` can list models but cannot invoke; the last command needs
AdministratorAccess):

```powershell
aws bedrock list-foundation-models --by-provider amazon --by-output-modality EMBEDDING --query "modelSummaries[].modelId" --profile vl-vol --region us-east-1
```

```powershell
aws bedrock get-foundation-model --model-identifier amazon.titan-embed-text-v2:0 --query "modelDetails.[modelId,modelLifecycle.status]" --profile vl-vol --region us-east-1
```

```powershell
'{"inputText":"first aid volunteer","dimensions":512,"normalize":true}' | Set-Content -Encoding ascii body.json; aws bedrock-runtime invoke-model --model-id amazon.titan-embed-text-v2:0 --body fileb://body.json --content-type application/json --accept application/json --profile vl-vol --region us-east-1 out.json; (Get-Content out.json | ConvertFrom-Json).embedding.Count
```

Expect `amazon.titan-embed-text-v2:0` in the list, `ACTIVE`, and `512`. Bedrock now
enables serverless models by default, so there should be no "model access" step. If you get
`AccessDeniedException` that mentions an SCP, check the region. If it mentions model access,
open Bedrock → Model catalog → Titan Text Embeddings V2 once in the console.

## 4. How matching works

1. **Embedding.** Titan turns a text into a list of 512 numbers (a *vector*) that captures
   its meaning. Similar meanings give vectors pointing in similar directions.
2. **Texts.** Profile = skills, availability, location, bio from `vl-volunteers`. **The name is
   not used** (it says nothing about fit). Opportunity = title, skills, location, description.
   Both use the same labels (`Skills: …`) so they land in comparable regions.
3. **Normalization on, 512 dimensions.** We ask Titan for unit-length vectors, so cosine
   similarity = dot product. 512 is AWS's middle option: about half the storage of 1024 with
   nearly the same retrieval quality. It's one env var (`EMBED_DIMENSIONS`) to change.
4. **Score.** Cosine similarity in [-1, 1], higher = closer. It's a *relative* score for
   ordering, not a probability.
5. **Only re-embed when needed.** Every vector is stored with `modelId`, `dimensions`,
   `sourceTextHash` (SHA-256 of the exact text) and `embeddedAt`. If the hash, model or size
   differs from now, the vector is re-made. Otherwise it's reused (no Bedrock call).
6. **When vectors are made (never blocking a save).**
   - Opportunity: in a FastAPI background task *after* the sync call has already answered.
   - Volunteer: lazily, the first time they're ranked or open recommendations.
     Optional: Ahmad can add `refresh_volunteer_embedding(sub)` as a BackgroundTask in
     `PUT /me/profile` (§7.5). It never raises.
   - Lazily made vectors are capped at 10 Bedrock calls per request, so the first page load
     stays fast. The rest are filled on the next request.
7. **Storage.** Vectors are packed as float32 bytes (DynamoDB Binary): 2 KB per 512-d vector
   instead of ~10 KB as a list of Numbers.

**One deviation from my original plan:** volunteer vectors live in their **own table**
`vl-volunteer-embeddings` (PK `volunteerId`), not on the `vl-volunteers` row. Reasons:
(1) `GET /me/profile` returns the whole row, so a stored vector would leak into the response, and
FastAPI can't JSON-encode DynamoDB Binary, so it would actually **break** the profile page;
(2) least privilege: matching only *reads* `vl-volunteers` and never writes to Ahmad's table;
(3) profile saves and matching can't interfere. Going back to one table means editing only
`matching/store.py`, plus a projection in Ahmad's `get_profile`.

### Failure behaviour (Bedrock must never break saves or applications)

| What fails | What happens |
|---|---|
| Bedrock slow/down/throttled/denied | `rank_volunteers` → everyone `score: null`, original order. Recommendations → 200, `ranked: false`, soonest first. Opportunity sync still 200 (vector filled later). Timeouts: 2 s connect, 5 s read, 2 attempts |
| DynamoDB error inside `rank_volunteers` | Unranked list, never an exception (the applicants screen keeps working) |
| DynamoDB error in `GET /me/recommendations` | 503 via the app's existing handler |
| Profile saves, applications | Don't call matching at all, so they can't be affected |

## 5. Contracts

### 5.1 `rank_volunteers` (for Ahmad, same container)

```python
from matching import rank_volunteers

rank_volunteers(opportunity_id: str, volunteer_ids: list[str]) -> list[dict]
# [{"volunteerId": "abc", "score": 0.7312}, ..., {"volunteerId": "new", "score": None}]
```

- Highest score first. Equal scores are ordered by `volunteerId`, so the result is deterministic.
- Volunteers with no vector (no profile, empty profile, Bedrock down, lazy cap reached) go
  **last, in the order given, with `score: None`. Never dropped.**
- Each id comes back once (duplicates collapsed). `[]` → `[]`.
- **Never raises.** On any failure, everyone comes back unscored in the given order.

Suggested wiring in `internal.list_applicants` (Ahmad's file, his call). It ranks within the
page of 20 he already loads:

```python
from matching import rank_volunteers
...
ranking = rank_volunteers(opportunity_id, [i["volunteerId"] for i in items])
scores = {r["volunteerId"]: r["score"] for r in ranking}
order = {r["volunteerId"]: n for n, r in enumerate(ranking)}
for row in out:
    row["score"] = scores.get(row["volunteerId"])
out.sort(key=lambda r: order.get(r["volunteerId"], len(order)))
```

The page is a "newest 20" page, so this ranks *within* each page. Ranking all applicants
needs all ids first, which is fine at demo scale (see §8).

### 5.2 `GET /me/recommendations?limit=10` (browser)

- Auth: Cognito **access token** (API Gateway JWT authorizer + the container's own check),
  `volunteer` group required (403 otherwise). `limit` is 1–50, default 10.
- The volunteer is **always the token's `sub`**. There's no id parameter, and any
  `?volunteerId=` is ignored.
- Only `status == "open"` and `startAt` in the future (applying to a started one is refused anyway).

```json
{
  "items": [{"opportunityId": "…", "title": "…", "location": "…", "startAt": "…", "score": 0.71}],
  "ranked": true,
  "reason": null
}
```

`ranked: false` with `reason` `"profile_missing"` or `"matching_unavailable"` → same item
shape, `score: null`, soonest first. The page shows a notice and still lists them.

### 5.3 `PUT /internal/opportunities/{opportunityId}` (NGO backend only)

Same protection as Ahmad's other `/internal/*` routes: API Gateway **AWS_IAM** route plus the
container checks `x-vl-caller-arn == NGO_BACKEND_CALLER_ARN`.

```json
{"ngoId": "…", "title": "…", "description": "…", "location": "…",
 "skills": ["first aid"], "startAt": "2026-12-05T08:00:00+00:00",
 "endAt": "2026-12-05T14:00:00+00:00", "status": "open|closed|cancelled",
 "updatedAt": "2026-11-27T10:00:00.123456+00:00"}
```

→ `200 {"opportunityId": "…", "result": "created" | "updated" | "unchanged" | "stale"}`

**Idempotent:** same `updatedAt` → `unchanged`. Older → `stale`, nothing written. Enforced by
the DynamoDB condition `attribute_not_exists(opportunityId) OR updatedAt < :u`, so two racing
calls can't put an older version over a newer one. Times are normalized to fixed-width UTC
before comparing. Unknown fields (e.g. `embedding`) → 422. If only the status changed, the
vector is kept, with no Bedrock call. Any 2xx means "done", so retrying is always safe.

## 6. How `vl-opportunity-index` gets filled — DECIDED: option (a), 27 Nov 2026

### (a) NGO backend calls the Volunteer internal route (STS + SigV4) — **chosen**

After every successful save of an opportunity (create, edit, close), the NGO backend calls
`PUT /internal/opportunities/{id}` with the saved row.

- **Almost all the plumbing already exists.** `backend/ngo/volunteer_client.py` already
  assumes `vl-volunteer-internal-api-caller`, signs with SigV4, and calls IAM-only
  Volunteer routes. The container already checks the caller ARN. New pieces: one API route,
  one IAM statement, one client function, three call sites.
- **Risk: a dual write.** If the call fails *after* the NGO save, the copy is stale.
  Mitigations: (1) send it in a background task after the response, with 3 retries
  (0.2 s / 0.5 s / 1 s); (2) a **resync** that replays every row, which is safe because the
  upsert is idempotent; (3) the worst case is mild. A closed opportunity still showing in
  recommendations is refused by `POST /applications`, which asks the NGO API live.

NGO-side sketch (Malek's code, his call):

```python
# volunteer_client.py
def sync_opportunity(item: dict) -> None:
    fields = ("ngoId", "title", "description", "location", "skills",
              "startAt", "endAt", "status", "updatedAt")
    status, _ = _call("PUT", f"/internal/opportunities/{item['opportunityId']}",
                      {k: item[k] for k in fields})
    if status != 200:
        raise VolunteerServiceUnavailable(f"sync returned {status}")

# opportunities.py, after put_item / update_item succeed (use ReturnValues="ALL_NEW"):
background.add_task(sync_with_retries, item)   # 3 tries, then log; resync repairs
```

Resync: an admin-only NGO route (e.g. `POST /admin/opportunities/resync`) that scans
`vl-opportunities` and calls `sync_opportunity` per row. It has to run inside the NGO
container, because only `vl-ngo-task-role` may assume the caller role.

### (b) DynamoDB Stream → EventBridge Pipe → cross-account EventBridge → Volunteer consumer

Matches the team's "no dual write" rule (the NGO save is the only write). Needs: a stream on
`vl-opportunities`, a Pipe + bus (or rule) in the NGO account, a cross-account bus policy, a
rule in the Volunteer account, and a consumer. The consumer can't simply call the container:
the ALB is internal, and API destinations need a public endpoint. So it would be SQS (polled
by the container) or a small Lambda writing the index. Owned by Ahmad. `vl-applications-bus`
exists, but it's same-account and for applications, so this would be mostly new plumbing.

**Recommendation:** (a) with the idempotent upsert and the resync route, given the deadline
and that (a) reuses working, tested plumbing. Choose (b) only if Ahmad wants the
cross-account bus anyway. The Volunteer-side contract (§5.3) is the same either way:
a (b) consumer would call the same `service.upsert_opportunity`.

## 7. Asks for Ahmad (deployment owner). Nothing here has been applied

### 7.1 Tables (Volunteer account, us-east-1, on-demand, AWS-owned key)
- `vl-opportunity-index`: PK `opportunityId` (String). No GSI. PITR optional (rebuildable via resync).
- `vl-volunteer-embeddings`: PK `volunteerId` (String). No GSI. PITR not needed (rebuildable).

### 7.2 Task definition env (`vl-volunteer-api`, new revision)
```
BEDROCK_MODEL_ID=amazon.titan-embed-text-v2:0
EMBED_DIMENSIONS=512
OPPORTUNITY_INDEX_TABLE=vl-opportunity-index
VOLUNTEER_EMBEDDINGS_TABLE=vl-volunteer-embeddings
```
(`VOLUNTEERS_TABLE` and `NGO_BACKEND_CALLER_ARN` are already set/defaulted.)

### 7.3 Task role `vl-volunteer-task-role`: new inline policy `vl-matching-access`
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TitanEmbeddingsOnly",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
    },
    {
      "Sid": "ReadProfiles",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:BatchGetItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:170813308606:table/vl-volunteers"
    },
    {
      "Sid": "VolunteerVectors",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:BatchGetItem", "dynamodb:PutItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:170813308606:table/vl-volunteer-embeddings"
    },
    {
      "Sid": "OpportunityIndex",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"],
      "Resource": "arn:aws:dynamodb:us-east-1:170813308606:table/vl-opportunity-index"
    }
  ]
}
```

### 7.4 API Gateway `57bc1lggo1`
- `GET /me/recommendations` → integration `qlzy375`, authorizer `cognito-jwt` (CORS already allows GET).
- `PUT /internal/opportunities/{opportunityId}` → integration `x4f0r37`, **AWS_IAM**. Must be `x4f0r37` (the integration that overwrites `x-vl-caller-arn` with the verified caller), never `qlzy375`.
- Role `vl-volunteer-internal-api-caller`: allow
  `execute-api:Invoke` on `arn:aws:execute-api:us-east-1:170813308606:57bc1lggo1/*/PUT/internal/opportunities/*`.

### 7.5 Shared code changes in this branch (please review)
- `backend/volunteer/main.py`: `import matching` + `app.include_router(matching.router)`.
  Same entrypoint, same `requirements.txt` (no new dependencies).
- `backend/volunteer/Dockerfile`: `COPY matching/ ./matching/` (the old `COPY *.py` skips folders).
- Optional, in your `profiles.put_profile`: `background.add_task(matching.refresh_volunteer_embedding, user["userId"])`.

### 7.6 Network
Tasks already have a public IP for outbound traffic. Bedrock is reached through its public
endpoint `bedrock-runtime.us-east-1.amazonaws.com`, the same way as DynamoDB. No VPC endpoint
is needed, and none is allowed anyway: the SCP denies `ec2:CreateVpcEndpoint`.

## 8. Scaling limits (honest)

- Recommendations **scan every open opportunity** and score each in Python: O(open
  opportunities) per request. Fine for tens to a few hundred. Beyond that: a vector index
  (OpenSearch Serverless / pgvector, both blocked by our SCP today) or pre-computed top-N per
  volunteer.
- Applicants are ranked **within each page of 20**.
- The first request after many new profiles/opportunities embeds at most 10 per request; the rest
  are unscored until the next request.
- Titan v2 cost is about $0.02 per million input tokens (check the Bedrock pricing page). A
  profile is ~50 tokens, so demo cost is effectively zero. Reuse via the text hash keeps it that way.

## 9. Q&A cheat sheet

- **What's an embedding?** A list of numbers that represents meaning. Close meaning → vectors
  pointing in similar directions.
- **What's cosine similarity?** The cosine of the angle between two vectors: 1 = same direction,
  0 = unrelated. With unit-length vectors it's just multiply-and-add (dot product).
- **Why not keyword matching?** "paramedic" and "first aid" share no words but have close vectors.
- **Why no OpenSearch?** It's an always-on cost, our SCP blocks it, and at our scale a Python loop is milliseconds.
- **Why a read copy of opportunities?** Account boundary: the Volunteer account has no
  permission on NGO tables, by design. Each account owns its data, and the copy is fed through
  an authenticated interface.
- **What is STS / SigV4?** STS hands out temporary credentials for a role in another account.
  SigV4 is the signature AWS checks on each request, so API Gateway knows exactly which role
  called. No passwords or stored keys.
- **What does idempotent mean here?** Sending the same update twice (or an old one late)
  changes nothing. That makes retries and resyncs safe.
- **Can a volunteer see someone else's recommendations?** No. The id comes only from the
  verified token's `sub`, and there's no parameter to ask for another id.
- **What if Bedrock is down?** Users still get a list (unranked). Saves and applications never call Bedrock.

---

## Runbook section: paste into `docs/runbook.md` when it's built

```markdown
## AI matching (Titan embeddings) — Tarek Kaadan — <DATE BUILT>

Account: Volunteer (170813308606). Region: us-east-1.
Status: <Planned / Deployed on DATE by WHO>. Design + contracts: docs/matching.md.

Matching runs inside the Volunteer ECS container (backend/volunteer/matching/),
not a Lambda, and uses no OpenSearch. Ahmad's applicants code calls
rank_volunteers() in-process. The Volunteer backend never reads NGO tables; it
keeps its own copy, vl-opportunity-index, filled by PUT /internal/opportunities/{id}.

### Resources
- Bedrock: amazon.titan-embed-text-v2:0, on-demand, 512 dims, normalize=true.
  SCP OnlyTitanEmbeddingModels allows only amazon.titan-embed-*.
- DynamoDB vl-opportunity-index: PK opportunityId (S), on-demand, AWS-owned key.
- DynamoDB vl-volunteer-embeddings: PK volunteerId (S), on-demand, AWS-owned key.
- Task role vl-volunteer-task-role, inline policy vl-matching-access:
  bedrock:InvokeModel on the Titan v2 ARN only; GetItem/BatchGetItem on vl-volunteers;
  GetItem/BatchGetItem/PutItem on vl-volunteer-embeddings;
  GetItem/PutItem/UpdateItem/Scan on vl-opportunity-index.
- Task definition vl-volunteer-api:<REV> env: BEDROCK_MODEL_ID, EMBED_DIMENSIONS=512,
  OPPORTUNITY_INDEX_TABLE, VOLUNTEER_EMBEDDINGS_TABLE. Image tag <TAG>.
- API 57bc1lggo1: GET /me/recommendations (cognito-jwt → qlzy375);
  PUT /internal/opportunities/{opportunityId} (AWS_IAM → x4f0r37).
  vl-volunteer-internal-api-caller may invoke PUT /internal/opportunities/*.

### Verification
1. Local: cd backend/volunteer && python -m pytest  → all pass (175 on 27 Nov 2026).
2. Titan: aws bedrock-runtime invoke-model (docs/matching.md §3) → 512 numbers.
3. No token: curl $API/me/recommendations → 401.
4. NGO-group token → 403. Volunteer token → 200, items sorted by score, ranked=true.
5. Unsigned PUT /internal/opportunities/x → 403 (API Gateway).
6. NGO edit of an opportunity → row in vl-opportunity-index with the new updatedAt;
   replaying the same call → result "unchanged"; closing it → it disappears from
   recommendations.
7. Logs: aws logs tail /ecs/vl-volunteer-api --since 15m --profile vl-vol
   → no "matching:" errors (they mean Bedrock/DynamoDB fell back to unranked).

### Cleanup (after the presentations)
1. Remove the two API routes; remove the invoke statement from vl-volunteer-internal-api-caller.
2. Delete inline policy vl-matching-access from vl-volunteer-task-role.
3. Delete tables vl-opportunity-index and vl-volunteer-embeddings (both rebuildable).
4. Bedrock is pay-per-call: nothing to delete.
```
