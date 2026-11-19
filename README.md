# VolunteerLink

Serverless NGO ↔ volunteer platform on AWS (CMPS 297U). Three accounts under one AWS Organization.

**How it's built:** we use Terraform for the risky or repetitive parts: the org guardrails (SCPs, CloudTrail) and, later, the cross-account plumbing (EventBridge bus, STS roles). The app services are built by hand in the console, following [`docs/runbook.md`](docs/runbook.md). Each resource is managed one way only.

```
infra/
  bootstrap/   one-time: S3 bucket for Terraform state (management acct, local state)
  org/         SCP guardrails (management acct, Ahmad only)
docs/
  runbook.md   console build steps for the app services
backend/
  profile/     vl-profile Lambda + unit tests
scripts/
  smoke-test.sh
```

| Account | ID | Profile |
|---|---|---|
| Management | 565950323755 | `vl-mgmt` |
| NGO | 281780646139 | `vl-ngo` |
| Volunteer | 170813308606 | `vl-vol` |

**Requirements:** Terraform ≥ 1.10, AWS CLI v2, Python 3.13 (for tests).

---

## 0. AWS CLI login (everyone, once)

Add this to `~/.aws/config`. The profile names must match exactly, because Terraform uses them.

```ini
[sso-session vl]
sso_start_url = https://d-90666455e0.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[profile vl-mgmt]
sso_session = vl
sso_account_id = 565950323755
sso_role_name = AdministratorAccess   ; teammates: ReadOnlyAccess
region = us-east-1

[profile vl-ngo]
sso_session = vl
sso_account_id = 281780646139
sso_role_name = AdministratorAccess
region = us-east-1

[profile vl-vol]
sso_session = vl
sso_account_id = 170813308606
sso_role_name = AdministratorAccess
region = us-east-1
```

Run `aws sso login --sso-session vl` (sessions last 8h), then check with `aws sts get-caller-identity --profile vl-ngo`.

## 1. State bucket (Ahmad, once)

```bash
cd infra/bootstrap && terraform init && terraform apply
```

## 2. SCPs (Ahmad, management account)

Turn on the SCP policy type once:

```bash
ROOT=$(aws organizations list-roots --query 'Roots[0].Id' --output text --profile vl-mgmt)
aws organizations enable-policy-type --root-id "$ROOT" --policy-type SERVICE_CONTROL_POLICY --profile vl-mgmt
```

Then apply the policies:

```bash
cd infra/org && terraform init && terraform apply
```

There are three policies, each attached to the NGO and Volunteer accounts (`FullAWSAccess` stays attached as well):

| SCP | What it blocks |
|---|---|
| `vl-baseline` | leaving the org, the root user, stopping or deleting CloudTrail, deleting backups, editing `OrganizationAccountAccessRole` |
| `vl-region-lock` | anything outside us-east-1 (global services like IAM and CloudFront are exempt) |
| `vl-cost-guardrails` | EC2, NAT gateways, Elastic IPs, VPC endpoints, Fargate, EKS, load balancers, OpenSearch, RDS, Redshift, ElastiCache, Kinesis, SageMaker, WAF web ACLs, Bedrock provisioned throughput, and any Bedrock model except Titan embeddings |

**Prove they work.** Both commands should fail with *"explicit deny in a service control policy"*:

```bash
aws ec2 run-instances --dry-run --instance-type t3.micro \
  --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 --profile vl-ngo
aws dynamodb list-tables --region eu-west-1 --profile vl-vol
```

> SCPs never apply to the **management account**, so keep nothing running there. The $5 Budget is the only guard on that account.

## 3. Slice 1: Cognito → API → Lambda → DynamoDB (console)

Follow [`docs/runbook.md`](docs/runbook.md), sections A–F. It ends with the smoke test, which should show a 401 without a token, then return the saved profile on both PUT and GET.

```
Volunteer (browser) ──sign up / log in──▶ Cognito user pool        [NGO account]
        │  access token (JWT)
        ▼
HTTP API  GET|PUT /me/profile  (JWT authorizer, 10 rps / 20 burst)  [Volunteer account]
        ▼
Lambda vl-profile (arm64, Python 3.13) ──▶ DynamoDB vl-volunteers (on-demand, PITR)
```

- The user's identity comes from the token's `sub` claim, never from the request body, so users can only read or write their own profile.
- The Lambda role can do 3 DynamoDB actions on 1 table, and nothing else.
- Unit tests: `python -m pytest backend/profile`

## Before the demo
- [ ] Remove `ALLOW_USER_PASSWORD_AUTH` from the Cognito client (the frontend uses SRP)
- [ ] Set `deletion_protection = "ACTIVE"` on the user pool
- [ ] Add the CloudFront URL to `cors_allowed_origins`
