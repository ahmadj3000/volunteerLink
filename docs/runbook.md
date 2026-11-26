# VolunteerLink — console runbook

Everything here is built by hand in the console, in **us-east-1**. Terraform only manages `infra/` (state bucket, SCPs, and later the CloudTrail org trail and the cross-account EventBridge and STS setup). **Rule:** never edit a Terraform-managed resource in the console, and never import a console resource into Terraform.

Log the date and your name under each section when you build it, so we can rebuild if something breaks.

**Infra done:** bootstrap (state bucket `vl-tfstate-565950323755`), org (SCPs incl. `p-gxgat3bx` EC2 deny, `p-wzsuwzee` region deny; both verified with explicit-deny tests). Ahmad, 18 Nov 2026.

---

## Slice 1: Cognito → API → Lambda → DynamoDB ✅ (smoke test passed 19 Nov 2026)

> **Superseded 25 Nov 2026:** the profile API now runs on ECS. See
> "Volunteer backend on ECS" below. Sections C–D describe the retired Lambda.

Sign in at https://d-90666455e0.awsapps.com/start. Console labels shift a little over time, so if a name below doesn't match exactly, look for the closest one.

**Values for slice 1**
- User pool ID: `us-east-1_is9qkiZoh` (NGO account)
- App client ID: `6blnjutulha6k2gr0d1lf5fe9f`
- Volunteer API invoke URL: `https://57bc1lggo1.execute-api.us-east-1.amazonaws.com`
- Table: `vl-volunteers` (Volunteer account) — **hyphen, not underscore**

### A. DynamoDB table (Volunteer account)
1. DynamoDB → **Create table**
   - Name: `vl-volunteers`
   - Partition key: `volunteerId` (String). This holds the Cognito `sub`.
   - Table settings: **Customize** → Capacity mode **On-demand**. Leave encryption on **Owned by Amazon DynamoDB** (free).
2. After it's created: table → **Backups** tab → **Point-in-time recovery** → Edit → **Enable**.

### B. Cognito user pool (NGO account)
1. Cognito → **Create user pool**
   - Application type: **Single-page application (SPA)**, app name `vl-web`
   - Sign-in identifiers: **Email**. Required attributes: `email`.
   - Create. Rename the pool to `vl-users` if the console lets you.
2. Pool → **Authentication methods** → Password policy → **Custom**: min length 10, uppercase, lowercase and numbers required, symbols not required.
3. Pool → **App clients** → `vl-web` → Edit:
   - Auth flows: `ALLOW_USER_SRP_AUTH`, `ALLOW_REFRESH_TOKEN_AUTH`, plus `ALLOW_USER_PASSWORD_AUTH` (for the CLI smoke test only; remove it before the demo). Untick the default `ALLOW_USER_AUTH` (choice-based sign-in), we don't use it.
   - Access and ID token expiry: 1 hour. Refresh token: 30 days.
   - Prevent user existence errors: **on**. No client secret.
4. Pool → **Groups** → create `admin` (precedence 1), `ngo` (2) and `volunteer` (3).
5. Write down the **User pool ID** (`us-east-1_...`) and the **Client ID**.

### C. Lambda role and log group (Volunteer account)
1. CloudWatch → Log groups → **Create**: `/aws/lambda/vl-profile`, retention **14 days**. Creating it first stops it from defaulting to "never expire".
2. IAM → Roles → **Create role** → AWS service → **Lambda** → skip managed policies → name `vl-profile-fn`.
3. Role → Add permissions → **Create inline policy** → JSON → name it `vl-profile-fn`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:170813308606:table/vl-volunteers"
    },
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:us-east-1:170813308606:log-group:/aws/lambda/vl-profile:*"
    }
  ]
}
```

### D. Lambda function (Volunteer account)
1. Lambda → **Event Functions** (not Web Functions) → **Create function** → Author from scratch
   - Name `vl-profile`, runtime **Python 3.13**, architecture **arm64**
   - Execution role: **Use an existing role** → `vl-profile-fn`
2. Code tab: replace the contents of `lambda_function.py` with `backend/profile/handler.py` → **Deploy**. The handler stays `lambda_function.lambda_handler`.
3. Configuration → **Environment variables**: `TABLE_NAME` = `vl-volunteers`
4. Configuration → **General**: memory 256 MB, timeout 10 s.

### E. HTTP API (Volunteer account)
1. API Gateway → **Create API** → **HTTP API** → Build
   - Integration: Lambda → `vl-profile`. Name: `vl-volunteer-api`.
   - Routes: `GET /me/profile` and `PUT /me/profile`, both to `vl-profile`
   - Stage: `$default`, auto-deploy on
2. **Authorization** → Manage authorizers → Create → **JWT**
   - Name `cognito-jwt`, identity source `$request.header.Authorization`
   - Issuer: `https://cognito-idp.us-east-1.amazonaws.com/<USER_POOL_ID>`
   - Audience: `<CLIENT_ID>`
   - Attach it to **both** routes.
3. **CORS**: origin `http://localhost:5173`, headers `authorization,content-type`, methods `GET,PUT,OPTIONS`, max age 3600.
4. **Throttling** (Protect → Throttling, or Stages → `$default`): burst 20, rate 10.
5. Write down the **Invoke URL**.

### F. Test it

**Bash (Git Bash / Mac / Linux):**

```bash
export POOL_ID=us-east-1_xxxx CLIENT_ID=xxxx API=https://xxxx.execute-api.us-east-1.amazonaws.com
./scripts/smoke-test.sh you+vltest@gmail.com 'Passw0rdTest'
```

**PowerShell (Windows), same steps by hand:**

```powershell
aws sso login --sso-session vl
$POOL_ID   = "us-east-1_is9qkiZoh"
$CLIENT_ID = "6blnjutulha6k2gr0d1lf5fe9f"
$API       = "https://57bc1lggo1.execute-api.us-east-1.amazonaws.com"
$EMAIL     = "you+vltest@gmail.com"
$PW        = "Passw0rdTest"

# 1. create + confirm a test user (skip if it already exists)
aws cognito-idp sign-up --client-id $CLIENT_ID --username $EMAIL --password $PW --user-attributes Name=email,Value=$EMAIL --profile vl-ngo --no-cli-pager
aws cognito-idp admin-confirm-sign-up --user-pool-id $POOL_ID --username $EMAIL --profile vl-ngo --no-cli-pager
aws cognito-idp admin-get-user --user-pool-id $POOL_ID --username $EMAIL --profile vl-ngo --no-cli-pager --query UserStatus   # want "CONFIRMED"

# 2. token + 401 check
$TOKEN = aws cognito-idp initiate-auth --client-id $CLIENT_ID --auth-flow USER_PASSWORD_AUTH --auth-parameters USERNAME=$EMAIL,PASSWORD=$PW --query "AuthenticationResult.AccessToken" --output text --profile vl-ngo --no-cli-pager
curl.exe -s -o NUL -w "no token: %{http_code}`n" "$API/me/profile"   # want 401

# 3. save + read profile
$H = @{ Authorization = "Bearer $TOKEN" }
$BODY = '{"fullName":"Test User","location":"Beirut","skills":["first aid","driving"],"availability":["weekends","emergency"]}'
Invoke-RestMethod -Method Put -Uri "$API/me/profile" -Headers $H -ContentType "application/json" -Body $BODY | ConvertTo-Json
Invoke-RestMethod -Method Get -Uri "$API/me/profile" -Headers $H | ConvertTo-Json
```

You should get a 401 without a token, then your profile back from both the PUT and the GET.

**Common errors**
- **401 with a token:** wrong issuer or audience, or a trailing `/` in the issuer. Or the token expired (1 hour), so get a new one.
- **`{"message": "internal error"}` (500):** the Lambda ran and crashed. Read the logs: `aws logs tail /aws/lambda/vl-profile --since 15m --profile vl-vol --no-cli-pager`. Usually it's a missing or misspelled `TABLE_NAME` or a typo in the role policy. (19 Nov: `TABLE_NAME` was `vl_volunteers` with an underscore, which caused an AccessDenied. Fixed.)
- **`admin-confirm-sign-up` hangs:** Ctrl+C, check with `admin-get-user`, run it again.
- **AccessDenied mentioning "service control policy":** you're outside us-east-1.

**Before the demo:** remove `ALLOW_USER_PASSWORD_AUTH` from the `vl-web` app client.

Built by: Ahmad  Date: 18–19 Nov 2026

---

## Volunteer backend on ECS — Ahmad Jaber — 2026-11-25

Moves the profile API (`GET/PUT /me/profile`) from the `vl-profile` Lambda to a
FastAPI container on ECS Fargate, behind the **same** API URL. Same pattern as
the NGO side (API Gateway → VPC link → internal ALB → Fargate).

Account: Volunteer — 170813308606. Region: us-east-1.
Built via console, except ECR, log group and execution role (CLI).

### Request path

```
browser ──HTTPS──▶ API Gateway 57bc1lggo1 (JWT authorizer, CORS, throttling)
        ──▶ VPC link o866vx [vl-volunteer-vpclink-sg]
        ──HTTP 80──▶ internal ALB vl-volunteer-api-alb [vl-volunteer-alb-sg]
        ──HTTP 8000──▶ Fargate task [vl-volunteer-ecs-sg]
        ──▶ DynamoDB vl-volunteers (task role)
```

The token is checked twice: by the API Gateway JWT authorizer and again in
FastAPI (`backend/volunteer/auth.py`, access tokens only).

### Resources

**Image**
- ECR repo `vl-volunteer-api` (tags IMMUTABLE, scan on push)
- Image `170813308606.dkr.ecr.us-east-1.amazonaws.com/vl-volunteer-api:v1`
  (index digest `sha256:b1bcfaa2…`, linux/amd64 image `sha256:a8dbb5d3…`, ~73 MB)
- Built from `backend/volunteer/Dockerfile`:
  `docker build --platform linux/amd64 -t vl-volunteer-api:dev .`
  Add `--provenance=false --sbom=false` for future tags so scan-on-push works.

**IAM**
- `vl-volunteer-execution-role`: trust `ecs-tasks.amazonaws.com`, policy
  `AmazonECSTaskExecutionRolePolicy` (pull from ECR, write logs).
- `vl-volunteer-task-role`: trust `ecs-tasks.amazonaws.com`, inline policy
  `vl-volunteers-profile-access` = `dynamodb:GetItem`, `dynamodb:UpdateItem` on
  `arn:aws:dynamodb:us-east-1:170813308606:table/vl-volunteers` only.

**Logs**
- `/ecs/vl-volunteer-api`, retention 14 days.

**Network** (default VPC `vpc-06bde51126cd27e68`, public subnets via IGW)
- Subnets: us-east-1a `subnet-0b82a17a7d1c55f19`, us-east-1b `subnet-0cfa9960130c445b3`
- `vl-volunteer-vpclink-sg` `sg-00720119e7304dcae`: no inbound
- `vl-volunteer-alb-sg` `sg-04addbdb3bf0b9107`: TCP 80 from vpclink SG only
- `vl-volunteer-ecs-sg` `sg-0c9723a3738487ebd`: TCP 8000 from ALB SG only
- Tasks get a public IP for **outbound only** (ECR, Cognito keys, DynamoDB,
  logs). No inbound rule allows the internet. No NAT gateway, no endpoints.

**Load balancer**
- ALB `vl-volunteer-api-alb`, **internal**, IPv4, both subnets, listener HTTP 80
  → `vl-volunteer-api-tg`. DNS (private only):
  `internal-vl-volunteer-api-alb-740373852.us-east-1.elb.amazonaws.com`
- Target group `vl-volunteer-api-tg`: IP targets, HTTP 8000, health check
  `GET /health` → 200 (default thresholds: 5 × 30 s to healthy).

**ECS**
- Cluster `vl-volunteer-cluster` (Fargate only, Container Insights off)
- Task definition `vl-volunteer-api:1`: 0.25 vCPU / 0.5 GB, Linux/x86_64,
  awsvpc, container `vl-volunteer-api` port 8000, env
  `COGNITO_USER_POOL_ID=us-east-1_is9qkiZoh`,
  `COGNITO_CLIENT_ID=6blnjutulha6k2gr0d1lf5fe9f`, awslogs → `/ecs/vl-volunteer-api`
  (prefix `ecs`). `VOLUNTEERS_TABLE` defaults to `vl-volunteers`.
- Service `vl-volunteer-api-service`: 1 task, both subnets, `vl-volunteer-ecs-sg`,
  public IP on, attached to `vl-volunteer-api-tg`, health check grace 60 s,
  circuit breaker with rollback.

**API Gateway** (`57bc1lggo1`, unchanged URL, JWT authorizer, CORS, throttling)
- VPC link (V2) `vl-volunteer-vpc-link` `o866vx`: both subnets, vpclink SG
- Integration `qlzy375`: private resource → ALB listener HTTP 80 via `o866vx`
- `GET /me/profile` and `PUT /me/profile` → `qlzy375`
- Old Lambda integration (`vl-profile`) deleted from the API. The `vl-profile`
  function, its role and log group are kept until after the demo as a rollback.

### Verification (25 Nov 2026)
- Local: `docker run -p 8001:8000 …` → `/health` 200, `/me/profile` 401.
- ECS: deployment COMPLETED, 1 running task, target `healthy`.
- Through the public URL with an **access token**:
  - no token → 401 (API Gateway)
  - `GET /me/profile` → existing 19 Nov profile from `vl-volunteers`
  - `PUT /me/profile` → `location` updated, `updatedAt` new, `createdAt`
    unchanged (19 Nov), `volunteerId` = Cognito `sub`
- `aws logs tail /ecs/vl-volunteer-api` shows `"GET /me/profile" 200` and
  `"PUT /me/profile" 200` from `172.31.x.x` (the ALB), so the container served them.

Repeat with `scripts/smoke-test.sh` or the PowerShell steps in slice 1 F
(they now request the **AccessToken**).

### Issues hit (and fixes)
- Docker Desktop crashing on Windows: C: drive full. Reinstalled with
  `--installation-dir` and `--wsl-default-data-root` on D:.
- `docker login` → "The stub received bad data": the ECR token is too long for
  Windows Credential Manager. Removed `credsStore` and renamed
  `docker-credential-wincred.exe` so the 12-hour token is stored in
  `~/.docker/config.json` instead.
- Execution role first created in the NGO account by mistake: deleted there,
  recreated in Volunteer with `--profile vl-vol`. Always check the account ID.
- Task role trusted `ecs.amazonaws.com` (wrong use case) so it didn't appear in
  the task definition: trust changed to `ecs-tasks.amazonaws.com`.
- Service first created with the default SG/all subnets/no load balancer, then
  recreated; later updated from the default SG to `vl-volunteer-ecs-sg`
  (rolling deployment, no downtime).
- ECR basic scan of v1: 9 HIGH / 5 MEDIUM, all in the base image's Debian
  `openssl`. Accepted for the demo (private behind an internal ALB, non-root);
  for production, add `apt-get upgrade` to the image and rescan.

### Cost (approx., us-east-1 list prices — check Billing)
| Item | Hourly | Per day | 30 days |
|---|---|---|---|
| ALB (fixed, plus tiny LCU usage) | ~$0.0225 | ~$0.54 | ~$16.20 |
| Fargate 0.25 vCPU + 0.5 GB | ~$0.0123 | ~$0.30 | ~$8.90 |
| Public IPv4 on the task | $0.005 | $0.12 | $3.60 |
| VPC link (HTTP API) | none | — | — |
| ECR storage (~73 MB), logs, DynamoDB, API requests | — | cents | < $0.50 |
| **Total, Volunteer side** | | **~$0.96** | **~$29** |

The NGO side has the same shape, so roughly double for the whole project.
That does **not** fit the original $5/month target if left running. Budget
alerts only warn; they don't stop spending.

### Stop / cleanup
- **Pause compute** (keeps everything): ECS service → Update → desired tasks **0**.
  Back to **1** to resume (~3 min to healthy). The ALB keeps billing.
- **After the presentations**, delete in this order:
  1. ECS service (scale to 0, then delete) and cluster; deregister task definitions
  2. API routes' integration `qlzy375` (or the whole API if retired), then VPC link `o866vx`
  3. ALB `vl-volunteer-api-alb`, then target group `vl-volunteer-api-tg`
  4. Security groups (ecs, alb, vpclink)
  5. ECR repo `vl-volunteer-api` (with images), log group `/ecs/vl-volunteer-api`
  6. IAM roles `vl-volunteer-task-role`, `vl-volunteer-execution-role`
  7. Retired Lambda: `vl-profile`, role `vl-profile-fn`, log group `/aws/lambda/vl-profile`
- Keep `vl-volunteers` until the team agrees the data is no longer needed.


---

## Applications API + cross-account calls — Ahmad Jaber — 2026-11-25

(Summary; contract in `docs/api-applications.md`.)

Account: Volunteer — 170813308606 (+ roles in NGO — 281780646139). Region: us-east-1.

- **Routes on API `57bc1lggo1`**
  - Browser, Cognito JWT (`cognito-jwt`), integration `qlzy375` (strips `x-vl-caller-arn`):
    `POST /applications`, `GET /me/applications`
  - Internal, **IAM auth only**, integration `x4f0r37` (overwrites `x-vl-caller-arn = $context.identity.userArn`):
    `GET /internal/opportunities/{opportunityId}/applications`,
    `POST /internal/applications/{applicationId}/decision`
- **Tables (Volunteer):** `vl-applications` (PK `applicationId` = `<opportunityId>_<volunteerId>`,
  GSIs `byVolunteer`, `byOpportunity`, stream NEW_AND_OLD_IMAGES), `vl-opportunity-slots` (`acceptedCount`).
- **Cross-account roles (STS AssumeRole + SigV4, no stored keys):**
  - NGO `vl-ngo-internal-api-caller` ← trusted by `vl-volunteer-task-role`, session `vl-volunteer-api`.
    Inline policies: `invoke-internal-get-opportunity`, and (26 Nov) `vl-volunteer-email-lookup`
    = `cognito-idp:ListUsers` on pool `us-east-1_is9qkiZoh` only.
  - Volunteer `vl-volunteer-internal-api-caller` ← trusted by `vl-ngo-task-role`, session `vl-ngo-api`.
- **NGO API `ozb9ner0t1`:** `GET /internal/opportunities/{opportunityId}` and `/internal/{proxy+}` are IAM-only,
  header stamped the same way; browser integration strips it.
- **Code:** `backend/volunteer/applications.py`, `ngo_client.py`, `internal.py`; NGO side
  `backend/ngo/applications.py`, `volunteer_client.py`. Accept = one DynamoDB transaction
  (status pending→accepted + `acceptedCount < capacity`).
- **Verified 25 Nov:** unsigned internal calls → 403; no token → 401; SSO admin can't assume either caller role;
  admin-signed call refused by the caller-ARN check.

---

## Application workflow (step 4) — Ahmad Jaber — 2026-11-26

Event-driven workflow for every application: notify the NGO, wait for the decision
(48 h, one reminder, then expire), notify the volunteer, remind them the day before
the event. Built from CloudShell with `scripts/step4-setup.sh` (re-runnable);
definitions and IAM policies in `infra/workflow/`.

Account: Volunteer — 170813308606. Region: us-east-1.

### Flow

```
POST /applications ──▶ vl-applications (PutItem, the ONLY write)
   stream ──▶ Pipe vl-applications-pipe  (filters: INSERT status=pending
             │                            | MODIFY pending→accepted/rejected)
             ▼
        bus vl-applications-bus
          ├─ rule vl-application-created ──▶ Standard SFN vl-application-workflow
          └─ rule vl-application-decided ──▶ Express SFN vl-decision-relay ─ SendTaskSuccess(token)

vl-application-workflow:
 Config(48h) → Claim (set workflowExecution IF not set; else Duplicate → stop)
 → NotifyNgoAndWait (SQS .waitForTaskToken, timeout 48h)
      email worker parks the token on the row (IF status=pending), emails the NGO
   ├ decision (relay returns token) → NotifyVolunteer
   └ timeout → CheckStatus: decided? → NotifyVolunteer
                            pending  → RemindNgoAndWait (NEW token, 48h)
                                          └ timeout → CheckStatus → Expire (status=expired IF pending)
                                                                   → email volunteer "expired"
 accepted → reminderAt = startAt − 24h → Wait → event reminder email
```

Email jobs: `vl-email-queue` → `vl-email-worker` (Lambda) → SES. After 3 failed
receives a job moves to `vl-email-dlq`. Pipe/rule delivery failures go to `vl-events-dlq`.

### Design decisions (Q&A)
- **No dual write:** the backend only writes the row; events come from the table stream.
  The decision route is unchanged: its status update *is* the decision event.
- **Duplicates are harmless:** Pipes/EventBridge can't set an execution name, so identity
  is the `applicationId` via the conditional `Claim` write. A 2nd execution ends in `Duplicate`.
- **Expired tokens are never reused:** a timeout goes to `CheckStatus` (reads the real status);
  a reminder uses a fresh token which the worker overwrites on the row. A stale token in the
  relay → `TaskTimedOut` → ignored.
- **Races:** decided before the token is parked → the worker's conditional write fails and the
  worker returns the token itself. Decided at the last second before expiry → `Expire` condition
  fails → workflow continues with the stored decision.
- **Tokens stay server-side:** on the row only; API responses use a field whitelist.
- **Only Lambda = email worker** (small add-on). The relay is a 1-step Express workflow (SDK call).
- **Volunteer email:** looked up in Cognito by the token's `sub` at apply time (cross-account,
  `ListUsers` only), stored as `volunteerEmail`. Never from the browser. Lookup failure doesn't block applying.

### Resources

| Resource | Name / details |
|---|---|
| SQS | `vl-email-queue` (visibility 60 s, retention 4 d, SSE-SQS, redrive → `vl-email-dlq` after 3), `vl-email-dlq` (14 d), `vl-events-dlq` (14 d, policy: only rules on `vl-applications-bus`) |
| SES (sandbox) | identities `ahmadj3100+vl-noreply@gmail.com` (sender), `ahmadj3100+ngo@gmail.com`, `ahmadj3100@gmail.com`, `malektakieddine11@gmail.com` — all Verified |
| Lambda | `vl-email-worker`, Python 3.13 arm64, handler `handler.handler`, 10 s, env `APPLICATIONS_TABLE`, `SENDER_EMAIL`; SQS trigger batch 5, ReportBatchItemFailures, max concurrency 2; role `vl-email-worker-role-g5tnmi0k` (+ `AWSLambdaSQSQueueExecutionRole`, inline `vl-email-worker-access`) |
| EventBridge | bus `vl-applications-bus`; rules `vl-application-created` (INSERT → workflow, input transformer `{applicationId, opportunityStartAt}`), `vl-application-decided` (MODIFY → relay, input = NewImage); retries 10 / 1 h, DLQ `vl-events-dlq`; role `vl-applications-events-role` (StartExecution on both) |
| Pipe | `vl-applications-pipe`: `vl-applications` stream (LATEST, batch 1, 3 retries, DLQ `vl-events-dlq`) → bus; role `vl-applications-pipe-role` |
| Step Functions | `vl-application-workflow` (STANDARD, role `vl-application-workflow-role`: GetItem/UpdateItem on `vl-applications`, SendMessage on `vl-email-queue`); `vl-decision-relay` (EXPRESS, role `vl-decision-relay-role`: SendTaskSuccess). Logs ERROR → `/aws/vendedlogs/states/<name>` (14 d) |
| Logs | `/aws/lambda/vl-email-worker` 14 d |
| Volunteer backend | image `vl-volunteer-api:v2-email`, task def `vl-volunteer-api:3` (rev 2 = rollback), built + deployed from CloudShell |

### Verification (26 Nov 2026, `scripts/step4-e2e-test.sh`)
- `start`: pending row inserted → pipe RUNNING → execution started → `workflowExecution` set,
  token parked; worker log `sent ngo_new_applicant`; NGO email received.
- `dup`: second execution for the same application → `Duplicate` → succeeded, nothing sent.
- `decide accepted`: relay returned the token → `NotifyVolunteer` → `WasAccepted` →
  `ComputeReminderTime` → `WaitUntilReminder`; worker log `sent decision`; email received.
- `vl-email-dlq` = 0. Test execution stopped, test row deleted.
- Volunteer backend v2-email: deployment stable, `/me/profile` without token → 401.
- **Not yet shown:** the timeout path (set `Config` to 120 s for the demo), full UI run.

### Demo: show the reminder/expiry path
Step Functions → `vl-application-workflow` → Edit → state `Config` → `decisionTimeoutSeconds` 120 → Save.
Apply, don't decide: ~2 min reminder email, ~4 min `expired` + volunteer email.
**Set it back to 172800 afterwards.**

### Cost (approx., list prices)
Nothing here bills while idle. Per application roughly 15 Standard state transitions
($0.025/1,000), a few events/pipe requests/SQS messages ($0.40–1/million), 2–4 Lambda
invocations and SES emails ($0.10/1,000). The whole demo costs well under $0.10.
Waiting (48 h, until the event) is free in Standard workflows.

### Stop / cleanup (after the presentations)
1. Stop running executions of `vl-application-workflow`.
2. Pipe `vl-applications-pipe` → Stop, then delete.
3. Rules `vl-application-created`/`-decided` (remove targets first), bus `vl-applications-bus`.
4. State machines `vl-application-workflow`, `vl-decision-relay`; their log groups.
5. Lambda `vl-email-worker` (trigger first), its role and log group.
6. Queues `vl-email-queue`, `vl-email-dlq`, `vl-events-dlq`.
7. IAM roles `vl-applications-pipe-role`, `vl-application-workflow-role`, `vl-decision-relay-role`,
   `vl-applications-events-role`; NGO inline policy `vl-volunteer-email-lookup`.
8. SES identities (optional, free).

---

## Volunteer profile page — Ahmad Jaber — 2026-11-26

`frontend/src/pages/ahmad/Profile.jsx` (branch `malek-ahmad-integration`), route `/#/profile`,
"My profile" in the volunteer nav. Create/edit via `GET`/`PUT /me/profile` (already live).
Limits mirror `profiles.py`; the backend still validates. Apply errors about a missing profile
link to the page. Lint + build pass; browser test pending (local `npm run dev`).
