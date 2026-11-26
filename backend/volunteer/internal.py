"""Internal routes for the NGO backend only (never the browser). Owned by Ahmad.

GET  /internal/opportunities/{opportunityId}/applications?cursor=
     Applicants for one opportunity, newest first, with profile fields,
     plus how many slots are already filled.
POST /internal/applications/{applicationId}/decision
     {"decision": "accepted"|"rejected", "ngoId", "capacity", "decidedBy"}

Who may call: only the NGO backend. API Gateway exposes these paths only on
AWS_IAM routes and overwrites the x-vl-caller-arn header with the verified
SigV4 caller ($context.identity.userArn). This router accepts the request only
if that header equals NGO_BACKEND_CALLER_ARN:
    arn:aws:sts::170813308606:assumed-role/vl-volunteer-internal-api-caller/vl-ngo-api
The NGO backend has already checked that the signed-in NGO owns the
opportunity; ngoId is checked again here against the stored application.

Accepting is ONE DynamoDB transaction: the application flips pending -> accepted
AND the opportunity's acceptedCount goes up by 1, only while it's below
capacity. Both happen or neither, so simultaneous accepts can't overbook.
"""

import base64
import hmac
import json
import os
import re

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Request

import applications as apps

CALLER_HEADER = "x-vl-caller-arn"
DECISIONS = {"accepted", "rejected"}
ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
APPLICATION_ID = re.compile(r"^[A-Za-z0-9-]{1,64}_[A-Za-z0-9-]{1,64}$")
PROFILE_FIELDS = ("fullName", "location", "skills", "availability")


def require_ngo_backend(request: Request):
    expected = os.environ.get("NGO_BACKEND_CALLER_ARN", "")
    actual = request.headers.get(CALLER_HEADER, "")
    if not expected:
        print("internal route refused: NGO_BACKEND_CALLER_ARN not set", flush=True)
        raise HTTPException(403, "Forbidden.")
    if not hmac.compare_digest(actual.encode(), expected.encode()):
        print(f"internal route refused: caller {actual or '<none>'!r}", flush=True)
        raise HTTPException(403, "Forbidden.")


router = APIRouter(prefix="/internal", dependencies=[Depends(require_ngo_backend)])


def slots_table():
    return apps._table("SLOTS_TABLE", "vl-opportunity-slots")


def accepted_count(opportunity_id: str) -> int:
    item = slots_table().get_item(Key={"opportunityId": opportunity_id}).get("Item")
    return int(item.get("acceptedCount", 0)) if item else 0


# --- applicants list ---

CURSOR_KEYS = {"applicationId", "opportunityId", "appliedAt"}


def decode_cursor(cursor: str, opportunity_id: str) -> dict:
    bad = HTTPException(400, "Invalid cursor.")
    if len(cursor) > 2048:
        raise bad
    try:
        key = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError:
        raise bad from None
    if (
        not isinstance(key, dict) or set(key) != CURSOR_KEYS
        or not all(isinstance(v, str) for v in key.values())
        or key["opportunityId"] != opportunity_id
    ):
        raise bad
    return key


def load_profiles(volunteer_ids: list[str]) -> dict:
    """BatchGetItem the profiles (one page is at most PAGE_SIZE ids)."""
    if not volunteer_ids:
        return {}
    table_name = apps.volunteers_table().name
    request = {table_name: {
        "Keys": [{"volunteerId": v} for v in dict.fromkeys(volunteer_ids)],
        "ProjectionExpression": "volunteerId, fullName, #loc, skills, availability",
        "ExpressionAttributeNames": {"#loc": "location"},
    }}
    found = {}
    for _ in range(3):  # DynamoDB may return some keys as "unprocessed"; retry those
        resp = apps.volunteers_table().meta.client.batch_get_item(RequestItems=request)
        for item in resp.get("Responses", {}).get(table_name, []):
            found[item["volunteerId"]] = item
        request = resp.get("UnprocessedKeys") or {}
        if not request:
            break
    return found


@router.get("/opportunities/{opportunity_id}/applications")
def list_applicants(opportunity_id: str, cursor: str | None = None):
    if not ID.match(opportunity_id):
        raise HTTPException(400, "Invalid opportunity ID.")
    params = {
        "IndexName": "byOpportunity",
        "KeyConditionExpression": "opportunityId = :o",
        "ExpressionAttributeValues": {":o": opportunity_id},
        "ScanIndexForward": False,
        "Limit": apps.PAGE_SIZE,
    }
    if cursor:
        params["ExclusiveStartKey"] = decode_cursor(cursor, opportunity_id)
    result = apps.applications_table().query(**params)
    items = result.get("Items", [])
    profiles = load_profiles([i["volunteerId"] for i in items])

    out = []
    for item in items:
        row = apps.to_public(item)
        profile = profiles.get(item["volunteerId"], {})
        row.update({f: profile.get(f) for f in PROFILE_FIELDS})
        row["score"] = None  # filled in once Tarek's ranking is integrated
        out.append(row)
    return {
        "opportunityId": opportunity_id,
        "acceptedCount": accepted_count(opportunity_id),
        "items": out,
        "nextCursor": apps.encode_cursor(result.get("LastEvaluatedKey")),
    }


# --- decision ---

def validate_decision(body) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object.")
    unknown = set(body) - {"decision", "ngoId", "capacity", "decidedBy"}
    if unknown:
        raise HTTPException(400, f"Unknown fields: {sorted(unknown)}")
    if body.get("decision") not in DECISIONS:
        raise HTTPException(400, "'decision' must be 'accepted' or 'rejected'.")
    for key in ("ngoId", "decidedBy"):
        value = body.get(key)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise HTTPException(400, f"'{key}' is required.")
    capacity = body.get("capacity")
    if type(capacity) is not int or not 1 <= capacity <= 10000:
        raise HTTPException(400, "'capacity' must be an integer from 1 to 10000.")
    return body


def explain_failure(application_id: str, ngo_id: str):
    """The conditional write failed; reload to say why."""
    item = apps.applications_table().get_item(
        Key={"applicationId": application_id}, ConsistentRead=True,
    ).get("Item")
    if not item:
        raise HTTPException(404, "Application not found.")
    if item.get("ngoId") != ngo_id:
        raise HTTPException(403, "This application belongs to another NGO.")
    raise HTTPException(409, f"Application is already {item.get('status')}.")


@router.post("/applications/{application_id}/decision")
async def decide(application_id: str, request: Request):
    if not APPLICATION_ID.match(application_id):
        raise HTTPException(400, "Invalid application ID.")
    try:
        body = validate_decision(await request.json())
    except ValueError:
        raise HTTPException(400, "Body is not valid JSON.") from None

    opportunity_id = application_id.split("_", 1)[0]
    now = apps.now_iso()
    app_update = {
        "TableName": apps.applications_table().name,
        "Key": {"applicationId": application_id},
        "UpdateExpression": "SET #s = :new, decidedAt = :now, decidedBy = :by",
        # only a pending application of THIS NGO can be decided, exactly once
        "ConditionExpression": "attribute_exists(applicationId) AND #s = :pending AND ngoId = :ngo",
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {
            ":new": body["decision"], ":now": now, ":by": body["decidedBy"],
            ":pending": "pending", ":ngo": body["ngoId"],
        },
    }
    # The boto3 *resource's* client serializes plain Python values itself, so
    # Keys and ExpressionAttributeValues below are plain values ("x", 3), NOT
    # DynamoDB JSON ({"S": "x"}). Passing {"N": "3"} here is sent as a map and
    # DynamoDB rejects the expression (ValidationException -> 503).
    client = apps.applications_table().meta.client

    if body["decision"] == "rejected":
        try:
            client.transact_write_items(TransactItems=[{"Update": app_update}])
        except ClientError as error:
            reasons = [r.get("Code") for r in error.response.get("CancellationReasons", [])]
            if reasons[:1] == ["ConditionalCheckFailed"]:
                explain_failure(application_id, body["ngoId"])
            raise
    else:
        slot_update = {
            "TableName": slots_table().name,
            "Key": {"opportunityId": opportunity_id},
            "UpdateExpression": "ADD acceptedCount :one",
            "ConditionExpression": "attribute_not_exists(acceptedCount) OR acceptedCount < :cap",
            "ExpressionAttributeValues": {":one": 1, ":cap": body["capacity"]},
        }
        try:
            client.transact_write_items(TransactItems=[{"Update": app_update}, {"Update": slot_update}])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            reasons = [r.get("Code") for r in error.response.get("CancellationReasons", [])]
            if len(reasons) > 0 and reasons[0] == "ConditionalCheckFailed":
                explain_failure(application_id, body["ngoId"])
            if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed":
                raise HTTPException(409, "All slots for this opportunity are filled.") from None
            raise

    item = apps.applications_table().get_item(
        Key={"applicationId": application_id}, ConsistentRead=True,
    ).get("Item")
    return {"application": apps.to_public(item)}
