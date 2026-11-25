"""Calls the NGO account's internal API. Owned by Ahmad.

The Volunteer account never reads NGO tables directly. To learn about an
opportunity it asks the NGO backend:

    GET /internal/opportunities/{opportunityId}
    -> 200 {opportunityId, ngoId, title, status, capacity, startAt, endAt, contactEmail}
    -> 404 {"detail": "Opportunity not found."}

How the call is authorized (cross-account, no stored keys):
1. The container's task role (vl-volunteer-task-role) asks STS to assume
   vl-ngo-internal-api-caller in the NGO account. STS returns credentials
   that expire after 1 hour. We cache them and refresh 5 minutes early.
2. The request is signed with SigV4 for service "execute-api".
3. The NGO API's route uses AWS_IAM auth, so API Gateway checks the
   signature and that the role may invoke that route before forwarding.

Config (ECS task definition environment):
    NGO_INTERNAL_API_URL   https://ozb9ner0t1.execute-api.us-east-1.amazonaws.com
    NGO_CALLER_ROLE_ARN    arn:aws:iam::281780646139:role/vl-ngo-internal-api-caller
If either is missing, every call raises OpportunityServiceUnavailable (-> 503).

The session name is fixed, so the NGO side sees a predictable caller:
    arn:aws:sts::281780646139:assumed-role/vl-ngo-internal-api-caller/vl-volunteer-api
"""

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError

REGION = "us-east-1"
SESSION_NAME = "vl-volunteer-api"
TIMEOUT_SECONDS = 5
REFRESH_EARLY = timedelta(minutes=5)
MAX_BODY_BYTES = 64 * 1024


class OpportunityServiceUnavailable(Exception):
    """The NGO API couldn't be reached, refused us, or answered unexpectedly."""


# --- STS: borrow the NGO caller role, cached ---

_lock = threading.Lock()
_cached = {"credentials": None, "expires": None}
_sts = None


def _sts_client():
    global _sts
    if _sts is None:
        _sts = boto3.client(
            "sts", region_name=REGION,
            endpoint_url=f"https://sts.{REGION}.amazonaws.com",
        )
    return _sts


def _credentials(role_arn: str) -> Credentials:
    now = datetime.now(timezone.utc)
    with _lock:
        if _cached["credentials"] and _cached["expires"] - REFRESH_EARLY > now:
            return _cached["credentials"]
        try:
            resp = _sts_client().assume_role(
                RoleArn=role_arn, RoleSessionName=SESSION_NAME, DurationSeconds=3600,
            )
        except (ClientError, BotoCoreError) as error:
            raise OpportunityServiceUnavailable(
                f"AssumeRole failed: {type(error).__name__}"
            ) from None
        c = resp["Credentials"]
        _cached["credentials"] = Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
        _cached["expires"] = c["Expiration"]
        return _cached["credentials"]


# --- HTTP: SigV4-signed GET ---

def _send(url: str, headers: dict) -> tuple[int, bytes]:
    """Plain HTTPS GET. Returns (status, body). Replaced by a fake in tests."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as error:  # 4xx/5xx still have a body
        return error.code, error.read(MAX_BODY_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise OpportunityServiceUnavailable(f"network error: {type(error).__name__}") from None


def _signed_get(url: str, credentials: Credentials) -> tuple[int, bytes]:
    request = AWSRequest(method="GET", url=url, headers={"Accept": "application/json"})
    SigV4Auth(credentials, "execute-api", REGION).add_auth(request)
    return _send(url, dict(request.headers.items()))


# --- public interface ---

def _valid(opp, opportunity_id: str) -> bool:
    return (
        isinstance(opp, dict)
        and opp.get("opportunityId") == opportunity_id
        and isinstance(opp.get("ngoId"), str) and opp["ngoId"]
        and isinstance(opp.get("status"), str)
        and isinstance(opp.get("startAt"), str)
        and isinstance(opp.get("title"), (str, type(None)))
        and isinstance(opp.get("contactEmail"), (str, type(None)))
    )


def get_opportunity(opportunity_id: str) -> dict | None:
    """Return the opportunity dict, or None if the NGO backend says it doesn't exist."""
    base = os.environ.get("NGO_INTERNAL_API_URL", "").rstrip("/")
    role_arn = os.environ.get("NGO_CALLER_ROLE_ARN", "")
    if not base or not role_arn:
        raise OpportunityServiceUnavailable("NGO_INTERNAL_API_URL / NGO_CALLER_ROLE_ARN not set")

    url = f"{base}/internal/opportunities/{urllib.parse.quote(opportunity_id, safe='')}"
    status, body = _signed_get(url, _credentials(role_arn))

    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None

    if status == 404:
        # Malek's backend answers {"detail": ...}. API Gateway's own 404
        # ({"message": "Not Found"}) means the route is missing: a config
        # problem, not a missing opportunity, so don't report "not found".
        if isinstance(data, dict) and "detail" in data:
            return None
        raise OpportunityServiceUnavailable("404 from API Gateway (route missing?)")
    if status != 200:
        # 403 = IAM refused the signed call; 5xx = NGO backend trouble.
        raise OpportunityServiceUnavailable(f"NGO API returned {status}")
    if not _valid(data, opportunity_id):
        raise OpportunityServiceUnavailable("NGO API returned an unexpected body")
    return data
