#!/usr/bin/env bash
# End-to-end check of slice 1: Cognito sign-up -> token -> PUT/GET profile.
# Usage: POOL_ID=... CLIENT_ID=... API=... ./scripts/smoke-test.sh you+vltest@gmail.com 'Passw0rdTest'
set -euo pipefail
: "${POOL_ID:?set POOL_ID (Cognito user pool ID)}"
: "${CLIENT_ID:?set CLIENT_ID (Cognito app client ID)}"
: "${API:?set API (HTTP API invoke URL, no trailing slash)}"

EMAIL="${1:?email}"
PASSWORD="${2:?password (10+ chars, upper, lower, number)}"
echo "1) sign up (ignore 'UsernameExistsException' on re-runs)"
aws cognito-idp sign-up --client-id "$CLIENT_ID" --username "$EMAIL" --password "$PASSWORD" \
  --user-attributes Name=email,Value="$EMAIL" --profile vl-ngo >/dev/null || true

echo "2) confirm the user as admin (skips the email code)"
aws cognito-idp admin-confirm-sign-up --user-pool-id "$POOL_ID" --username "$EMAIL" --profile vl-ngo 2>/dev/null || true

echo "3) log in"
TOKEN=$(aws cognito-idp initiate-auth --client-id "$CLIENT_ID" --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME="$EMAIL",PASSWORD="$PASSWORD" \
  --query 'AuthenticationResult.AccessToken' --output text --profile vl-ngo)

echo "4) no token -> expect 401"
curl -s -o /dev/null -w "%{http_code}\n" "$API/me/profile"

echo "5) PUT profile"
curl -s -X PUT "$API/me/profile" -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" \
  -d '{"fullName":"Smoke Test","skills":["First Aid","Logistics"],"location":"Beirut","availability":["weekends","emergency"]}'
echo

echo "6) GET profile"
curl -s "$API/me/profile" -H "Authorization: Bearer $TOKEN"
echo
