"""Calls the Volunteer account's internal API. Owned by Ahmad.

The NGO account never reads Volunteer tables directly. Applicants and
decisions go through the Volunteer backend's internal routes:

    GET  /internal/opportunities/{opportunityId}/applications?cursor=
    POST /internal/applications/{applicationId}/decision

How the call is authorized (cross-account, no stored keys):
1. This container's task role (vl-ngo-task-role) asks STS to assume
   vl-volunteer-internal-api-caller in the Volunteer account. The 1-hour
   credentials are cached and refreshed 5 minutes early.
2. Each request is SigV4-signed for service "execute-api".
3. The Volunteer API exposes these paths only on AWS_IAM routes and passes
   the verified caller to the backend, which accepts only this session:
   arn:aws:sts::170813308606:assumed-role/vl-volunteer-internal-api-caller/vl-ngo-api

Config (ECS task definition environment):
    VOLUNTEER_INTERNAL_API_URL   https://57bc1lggo1.execute-api.us-east-1.amazonaws.com
    VOLUNTEER_CALLER_ROLE_ARN    arn:aws:iam::170813308606:role/vl-volunteer-internal-api-caller
Missing config, STS failure or network failure -> VolunteerServiceUnavailable (503).
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
SESSION_NAME = "vl-ngo-api"
TIMEOUT_SECONDS = 5
REFRESH_EARLY = timedelta(minutes=5)
MAX_BODY_BYTES = 256 * 1024


class VolunteerServiceUnavailable(Exception):
    """The Volunteer API couldn't be reached, refused us, or answered unexpectedly."""


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
            raise VolunteerServiceUnavailable(f"AssumeRole failed: {type(error).__name__}") from None
        c = resp["Credentials"]
        _cached["credentials"] = Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
        _cached["expires"] = c["Expiration"]
        return _cached["credentials"]


def _send(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    """Plain HTTPS request. Returns (status, body). Replaced by a fake in tests."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, resp.read(MAX_BODY_BYTES)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_BODY_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise VolunteerServiceUnavailable(f"network error: {type(error).__name__}") from None


def _call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    base = os.environ.get("VOLUNTEER_INTERNAL_API_URL", "").rstrip("/")
    role_arn = os.environ.get("VOLUNTEER_CALLER_ROLE_ARN", "")
    if not base or not role_arn:
        raise VolunteerServiceUnavailable("VOLUNTEER_INTERNAL_API_URL / VOLUNTEER_CALLER_ROLE_ARN not set")

    url = base + path
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(_credentials(role_arn), "execute-api", REGION).add_auth(request)
    status, raw = _send(method, url, dict(request.headers.items()), body)

    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise VolunteerServiceUnavailable(f"Volunteer API returned {status} with a non-JSON body")
    # Errors from the Volunteer backend carry {"detail"}; API Gateway's own
    # errors ({"message"}: missing route, IAM denied, throttled) are config or
    # capacity problems, not answers about the application.
    if status != 200 and "detail" not in data:
        raise VolunteerServiceUnavailable(f"Volunteer API gateway returned {status}")
    if status >= 500:
        raise VolunteerServiceUnavailable(f"Volunteer API returned {status}")
    return status, data


def list_applications(opportunity_id: str, cursor: str | None = None) -> dict:
    path = f"/internal/opportunities/{urllib.parse.quote(opportunity_id, safe='')}/applications"
    if cursor:
        path += "?" + urllib.parse.urlencode({"cursor": cursor})
    status, data = _call("GET", path)
    if status != 200 or not isinstance(data.get("items"), list):
        raise VolunteerServiceUnavailable(f"unexpected applicants response ({status})")
    return data


def decide(application_id: str, decision: str, ngo_id: str, capacity: int, decided_by: str) -> tuple[int, dict]:
    """Returns (status, body). 200/400/403/404/409 come back as-is for the caller to map."""
    path = f"/internal/applications/{urllib.parse.quote(application_id, safe='')}/decision"
    return _call("POST", path, {
        "decision": decision, "ngoId": ngo_id, "capacity": capacity, "decidedBy": decided_by,
    })
