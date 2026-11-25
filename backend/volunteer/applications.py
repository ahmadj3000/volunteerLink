"""Volunteer application routes. Owned by Ahmad.

POST /applications       apply to an open opportunity (volunteer group)
GET  /me/applications    the caller's applications, newest first

Contract: docs/api-applications.md.

Key ideas
- applicationId = "<opportunityId>_<volunteerId>". The save is conditional
  on that row not existing, so DynamoDB itself rejects duplicate applications.
- The volunteer's identity always comes from the verified token, never the body.
- Opportunity facts (owner ngoId, title, start, NGO contact email) come from
  the NGO account's internal API, never from the browser, and are copied onto
  the application so later reads don't need to call the NGO account.
- Saving the row is the only write. The table's stream turns the new row into
  the "application created" event (step 4), so there is no second PutEvents
  call that could fail on its own.
"""

import base64
import json
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request

import ngo_client
from auth import current_user

router = APIRouter()

MESSAGE_MAX = 500
PAGE_SIZE = 20
# UUID-style IDs only. Also guarantees no "_" so the applicationId split is unambiguous.
OPPORTUNITY_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

_tables = {}


def _table(env_var, default):
    name = os.environ.get(env_var, default)
    if name not in _tables:
        _tables[name] = boto3.resource("dynamodb", region_name="us-east-1").Table(name)
    return _tables[name]


def applications_table():
    return _table("APPLICATIONS_TABLE", "vl-applications")


def volunteers_table():
    return _table("VOLUNTEERS_TABLE", "vl-volunteers")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def require_volunteer(user: dict = Depends(current_user)):
    if "volunteer" not in user["groups"]:
        raise HTTPException(403, "Only volunteers can do this.")
    return user


# What the browser sees. Internal fields (ngoContactEmail) stay server-side.
PUBLIC_FIELDS = (
    "applicationId", "opportunityId", "volunteerId", "ngoId",
    "opportunityTitle", "opportunityStartAt", "message",
    "status", "appliedAt", "decidedAt",
)


def to_public(item: dict) -> dict:
    return {k: item.get(k) for k in PUBLIC_FIELDS}


def validate_apply(body) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object.")
    unknown = set(body) - {"opportunityId", "message"}
    if unknown:
        raise HTTPException(400, f"Unknown fields: {sorted(unknown)}")

    opp_id = body.get("opportunityId")
    if not isinstance(opp_id, str) or not OPPORTUNITY_ID.match(opp_id):
        raise HTTPException(400, "'opportunityId' must be a valid opportunity ID.")

    message = body.get("message")
    if message is not None:
        if not isinstance(message, str):
            raise HTTPException(400, "'message' must be a string.")
        message = message.strip() or None
        if message and len(message) > MESSAGE_MAX:
            raise HTTPException(400, f"'message' must be at most {MESSAGE_MAX} characters.")
    return {"opportunityId": opp_id, "message": message}


@router.post("/applications", status_code=201)
async def apply(request: Request, user: dict = Depends(require_volunteer)):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Body is not valid JSON.") from None
    req = validate_apply(body)
    volunteer_id = user["userId"]

    # 1. Must have a profile first (the NGO reviews it).
    if not volunteers_table().get_item(Key={"volunteerId": volunteer_id}).get("Item"):
        raise HTTPException(409, "Create your profile before applying.")

    # 2. Ask the NGO account about the opportunity.
    try:
        opp = ngo_client.get_opportunity(req["opportunityId"])
    except ngo_client.OpportunityServiceUnavailable as error:
        print(f"NGO API unavailable: {error}", flush=True)
        raise HTTPException(503, "Opportunity service unavailable.") from None
    if opp is None:
        raise HTTPException(404, "Opportunity not found.")
    if opp.get("status") != "open":
        raise HTTPException(409, "This opportunity is not open for applications.")
    start = opp.get("startAt")
    try:
        started = datetime.fromisoformat(start) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        print(f"Opportunity {req['opportunityId']} has bad startAt: {start!r}", flush=True)
        raise HTTPException(503, "Opportunity service returned bad data.") from None
    if started:
        raise HTTPException(409, "This opportunity has already started.")

    # 3. Save once. The condition makes a second apply fail instead of overwrite.
    item = {
        "applicationId": f"{req['opportunityId']}_{volunteer_id}",
        "opportunityId": req["opportunityId"],
        "volunteerId": volunteer_id,
        "ngoId": opp["ngoId"],
        "opportunityTitle": opp.get("title"),
        "opportunityStartAt": start,
        "status": "pending",
        "appliedAt": now_iso(),
    }
    if req["message"]:
        item["message"] = req["message"]
    if opp.get("contactEmail"):
        item["ngoContactEmail"] = opp["contactEmail"]  # for the workflow's NGO email

    try:
        applications_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(applicationId)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "You already applied to this opportunity.") from None
        raise
    return {"application": to_public(item)}


# --- pagination: LastEvaluatedKey <-> opaque cursor string ---

CURSOR_KEYS = {"applicationId", "volunteerId", "appliedAt"}


def encode_cursor(key: dict | None) -> str | None:
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


def decode_cursor(cursor: str, volunteer_id: str) -> dict:
    bad = HTTPException(400, "Invalid cursor.")
    try:
        key = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError:
        raise bad from None
    if (
        not isinstance(key, dict)
        or set(key) != CURSOR_KEYS
        or not all(isinstance(v, str) for v in key.values())
        or key["volunteerId"] != volunteer_id  # can't page through someone else's list
    ):
        raise bad
    return key


@router.get("/me/applications")
def my_applications(cursor: str | None = None, user: dict = Depends(require_volunteer)):
    params = {
        "IndexName": "byVolunteer",
        "KeyConditionExpression": "volunteerId = :v",
        "ExpressionAttributeValues": {":v": user["userId"]},
        "ScanIndexForward": False,  # newest first
        "Limit": PAGE_SIZE,
    }
    if cursor:
        if len(cursor) > 2048:
            raise HTTPException(400, "Invalid cursor.")
        params["ExclusiveStartKey"] = decode_cursor(cursor, user["userId"])

    result = applications_table().query(**params)
    return {
        "items": [to_public(i) for i in result.get("Items", [])],
        "nextCursor": encode_cursor(result.get("LastEvaluatedKey")),
    }
