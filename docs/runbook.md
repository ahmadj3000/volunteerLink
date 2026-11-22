# VolunteerLink — console runbook

Everything here is built by hand in the console, in **us-east-1**. Terraform only manages `infra/` (state bucket, SCPs, and later the CloudTrail org trail and the cross-account EventBridge and STS setup). **Rule:** never edit a Terraform-managed resource in the console, and never import a console resource into Terraform.

Log the date and your name under each section when you build it, so we can rebuild if something breaks.

**Infra done:** bootstrap (state bucket `vl-tfstate-565950323755`), org (SCPs incl. `p-gxgat3bx` EC2 deny, `p-wzsuwzee` region deny; both verified with explicit-deny tests). Ahmad, 18 Nov 2026.

---

## Slice 1: Cognito → API → Lambda → DynamoDB ✅ (smoke test passed 19 Nov 2026)

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
$TOKEN = aws cognito-idp initiate-auth --client-id $CLIENT_ID --auth-flow USER_PASSWORD_AUTH --auth-parameters USERNAME=$EMAIL,PASSWORD=$PW --query "AuthenticationResult.IdToken" --output text --profile vl-ngo --no-cli-pager
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

## Opportunities schema — agreed interface (planned)

Defined by: Malek Takieddine
Date: 20 Nov 2026
Status: Proposed; not deployed

Account: NGO (281780646139)
Region: us-east-1
Table: vl-opportunities
Partition key: opportunityId (String)

| Field | Type | Purpose |
|---|---|---|
| opportunityId | String | Backend-generated UUID |
| ngoId | String | Owning NGO; assigned and checked by backend |
| title | String | Opportunity title |
| description | String | Activities and requirements |
| location | String | Location in Lebanon |
| skills | List of strings | Required skills, lowercase and deduplicated |
| startAt | String | Start time in UTC, ISO 8601 |
| endAt | String | End time in UTC, ISO 8601 |
| capacity | Number | Maximum accepted volunteers |
| status | String | open, closed, or cancelled |
| isCrisis | Boolean | Urgent crisis-response opportunity |
| createdAt | String | Backend-generated UTC timestamp |
| updatedAt | String | Backend-generated UTC timestamp |

Only approved NGOs may create opportunities.
The backend enforces ownership for updates.
AI embeddings are managed separately by Tarek; clients cannot set them.
Application decisions and accepted-slot tracking are coordinated with Ahmad.


---

## Frontend — Cognito login

Built by: Malek Takieddine
Date: 21 Nov 2026

- Created React frontend with Vite in frontend/.
- Installed aws-amplify and react-router-dom.
- Configured existing Cognito pool us-east-1_is9qkiZoh
  and app client 6blnjutulha6k2gr0d1lf5fe9f.
- Implemented sign-in, new-password challenge, and sign-out.
- Created a manually verified Cognito test user.
- Verified initial sign-in and temporary-password replacement locally
  at http://localhost:5173.
- No Lambda or ECS backend is involved in this login flow.

Remaining: session restoration after refresh, role-aware routing,
and frontend deployment to S3/CloudFront.
---

## Frontend shell and volunteer role verification

Built by: Malek Takieddine
Date: 21 Nov 2026
AWS account: NGO — 281780646139
Region: us-east-1

- Added session restoration, sidebar navigation, and HashRouter routes.
- Created separate page folders for Malek, Ahmad, and Tarek.
- Assigned the existing test user to the Cognito volunteer group.
- Signed out and back in to obtain tokens containing the group.
- Verified volunteer navigation appears.
- Verified /#/admin redirects the volunteer to /#/.
- Production build and ESLint passed.

Pages remain placeholders pending backend integration.
Frontend route guards do not replace backend authorization.
S3/CloudFront hosting is not yet deployed.

## Frontend hosting — Malek Takieddine — 2026-11-21

Account: NGO (281780646139)
S3 region: us-east-1
Built manually through the AWS console.

### Resources and settings
- S3 bucket: vl-frontend-281780646139-us-east-1-an
- Uploaded frontend/dist contents to the bucket root.
- CloudFront distribution: E9RPPZQ6B75BA
- Default root object: index.html
- Viewer protocol policy: Redirect HTTP to HTTPS.
- WAF and Origin Shield disabled.
- Billing: pay-as-you-go.
- Bucket policy grants cloudfront.amazonaws.com s3:GetObject
  only for this distribution's source ARN:
  arn:aws:cloudfront::281780646139:distribution/E9RPPZQ6B75BA

### Verification
- Hosted frontend loaded through CloudFront.
- Cognito sign-in succeeded; volunteer navigation appeared.
- Direct unsigned S3 request to index.html returned AccessDenied.
- Confirmed HTTP-to-HTTPS redirect is selected in CloudFront.

### Remaining work
- Pages remain placeholders pending backend API integration.
- Frontend route guards do not replace backend authorization.

## Opportunities table — Malek Takieddine — 2026-11-21

Built manually in the AWS console.
Account: NGO (281780646139)
Region: us-east-1

- Table: vl-opportunities
- Partition key: opportunityId (String)
- No sort key or secondary indexes.
- Capacity: On-demand.
- Table class: DynamoDB Standard.
- Encryption: AWS-owned key.
- Enabled point-in-time recovery with a 35-day recovery period.
- Verified table Active and PITR On.
- PITR incurs additional charges.
- Backend integration is not yet implemented.

## NGO backend container — Malek Takieddine — 2026-11-22

- Created FastAPI backend in backend/ngo/.
- Added Dockerfile using Python 3.13 and a non-root application user.
- Built local image: vl-ngo-api:dev.
- Ran the container with port 8000 bound to localhost.
- Verified GET /health returned:
  {"status":"ok","service":"vl-ngo-api"}

Local verification only. Not deployed to ECS.
Cognito authorization and DynamoDB integration are not implemented yet.

## NGO backend authentication — Malek Takieddine — 2026-11-22

- Added Cognito access-token verification to the Docker backend.
- Checks RS256 signature using the pool's published signing keys,
  token expiry, issuer, token_use=access, and app client_id.
- GET /health remains public.
- GET /me requires a valid access token and returns userId and groups.
- Added a local Vite proxy to the backend.
- Added a development-only frontend authentication test button.

Verified:
- Request without a token was rejected.
- Signed-in frontend request succeeded and returned the test user's
  Cognito sub and volunteer group.
- Frontend production build and ESLint passed.

Role authorization and DynamoDB integration are not implemented yet.
Backend is local only; not deployed to ECS.

## Backend role checks — Malek Takieddine — 2026-11-22

- Added group-protected GET /ngo/access and GET /admin/access.
- Groups come from the verified Cognito access token.
- Tested with the volunteer account:
  - /me succeeded.
  - /ngo/access rejected access: ngo role required.
  - /admin/access rejected access: admin role required.
- Added development-only frontend buttons for these checks.

NGO/admin successful-access tests remain pending.
NGO approval and opportunity ownership checks are not implemented yet.

## Successful role-access tests — Malek Takieddine — 2026-11-22

- Created separate Cognito test users in the ngo and admin groups.
- Verified the NGO user's /ngo/access request succeeded.
- Verified the admin user's /admin/access request succeeded.
- Earlier volunteer tests rejected both restricted routes.
- Group membership alone does not constitute NGO approval.

## NGO registration and approval — Malek Takieddine — 2026-11-22

### AWS resources
- Created vl-ngos manually in NGO account 281780646139, us-east-1.
- Partition key: ngoId (String); no sort key.
- Capacity: On-demand; encryption: AWS-owned key.
- Created local development role vl-ngo-dev.
- Trust policy permits the existing NGO SSO administrator role to assume it.
- Inline policy vl-ngo-dev-data allows GetItem, PutItem, UpdateItem,
  and Scan on vl-ngos and vl-opportunities only.
- Configured CLI profiles vl-ngo and vl-ngo-dev.
- Verified assumed-role identity and DynamoDB access from Docker.

### Implementation
- GET /ngo/registration: NGO reads its own registration.
- POST /ngo/registration: creates a pending registration.
- ngoId comes from the verified Cognito sub.
- Client-supplied status and unknown fields are rejected.
- Conditional creation prevents overwriting an existing registration.
- GET /admin/ngos: admin-only listing, paginated.
- PATCH /admin/ngos/{ngoId}: admin approves or rejects a pending NGO.
- Records reviewer identity and review timestamp.
- Prevents self-review and repeat review of an already decided registration.
- Connected NGO and admin dashboards.
- Added scripts/run-ngo-local.py to pass temporary limited-role
  credentials to Docker without writing them to project files.

### Verification
- NGO registration submitted successfully.
- Admin approval succeeded.
- NGO dashboard retrieved approved status from DynamoDB.
- Frontend production build and ESLint passed.

### Remaining
- Rejection flow and additional authorization/validation tests.
- Registration editing and resubmission.
- Opportunity posting with backend approval and ownership enforcement.
- ECS deployment; backend currently runs locally in Docker.
- Updated frontend has not been uploaded to S3/CloudFront.

## Opportunity posting — Malek Takieddine — 2026-11-22

- Added an opportunity form to the approved NGO dashboard.
- Backend requires the ngo group and checks saved NGO approval.
- Backend assigns the opportunity ID, owner, status, and timestamps.
- Validates fields, capacity, and future start/end dates.
- Converts dates to UTC before storing them in vl-opportunities.
- Supports an urgent crisis-response flag.

Verified:
- Approved NGO posted "Animal shelter support" successfully.
- Frontend production build and ESLint passed.

Restricted posting tests remain pending.
Opportunity listing, editing, and closing are not implemented yet.
Backend remains local; not deployed to ECS.

## Opportunity browsing — Malek Takieddine — 2026-11-22

- Added authenticated GET /opportunities with pagination.
- Returns open opportunities and selected display fields.
- Connected Explore opportunities to the backend.
- Fixed the DynamoDB reserved-word alias for capacity.
- Verified "Animal shelter support" appears for both NGO and volunteer accounts.

Editing, closing, and restricted-posting tests remain pending.
Backend remains local.

## NGO opportunity management — Malek Takieddine — 2026-11-22

- Added a paginated list of the signed-in NGO's opportunities.
- Added closing with backend checks for ownership, open status,
  and the expected updatedAt value.
- Closing preserves the database record.
- Verified Animal shelter support changed to closed in the NGO
  dashboard and disappeared from Explore opportunities.
- Added dashboard form styling and button spacing.
- Frontend production build and ESLint passed.

Cross-NGO ownership rejection tests and editing remain pending.
Backend remains local.
