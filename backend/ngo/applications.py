"""NGO applicant review routes. Owned by Ahmad, in Malek's NGO backend.

GET  /ngo/opportunities/{opportunityId}/applicants?cursor=
POST /ngo/applications/{applicationId}/decision   {"decision": "accepted"|"rejected"}

Contract: docs/api-applications.md.

Order of checks on every request:
1. Signed-in, in the ngo group, registration approved   (Malek's approved_ngo)
2. The opportunity exists in vl-opportunities and its ngoId is the caller
   (ownership comes from our own table, never from the browser)
3. Only then ask the Volunteer account (cross-account, IAM-signed) for the
   applicants, or to record the decision. The decision request carries the
   verified ngoId and the opportunity's capacity from our table, and the
   Volunteer side re-checks ngoId against the stored application.
"""

import re

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict
from typing import Literal

import volunteer_client
from opportunities import approved_ngo

router = APIRouter()

UUID_ISH = r"[A-Za-z0-9-]{1,64}"
OPPORTUNITY_ID = re.compile(rf"^{UUID_ISH}$")
APPLICATION_ID = re.compile(rf"^({UUID_ISH})_({UUID_ISH})$")


def opportunities_table():
    return boto3.resource("dynamodb", region_name="us-east-1").Table("vl-opportunities")


def owned_opportunity(opportunity_id: str, user: dict) -> dict:
    if not OPPORTUNITY_ID.match(opportunity_id):
        raise HTTPException(400, "Invalid opportunity ID.")
    item = opportunities_table().get_item(
        Key={"opportunityId": opportunity_id}, ConsistentRead=True,
    ).get("Item")
    if not item:
        raise HTTPException(404, "Opportunity not found.")
    if item.get("ngoId") != user["userId"]:
        raise HTTPException(403, "You can only manage your own opportunities.")
    return item


def unavailable(error: Exception):
    print(f"Volunteer API unavailable: {error}", flush=True)
    return HTTPException(503, "Applications service unavailable.")


@router.get("/ngo/opportunities/{opportunity_id}/applicants")
async def applicants(
    opportunity_id: str,
    cursor: str | None = Query(default=None, max_length=2048),
    user: dict = Depends(approved_ngo),
):
    opp = await run_in_threadpool(owned_opportunity, opportunity_id, user)
    try:
        data = await run_in_threadpool(volunteer_client.list_applications, opportunity_id, cursor)
    except volunteer_client.VolunteerServiceUnavailable as error:
        raise unavailable(error) from None
    return {
        "opportunityId": opportunity_id,
        "capacity": int(opp["capacity"]),
        "acceptedCount": data.get("acceptedCount", 0),
        "items": data["items"],
        "nextCursor": data.get("nextCursor"),
    }


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected"]


@router.post("/ngo/applications/{application_id}/decision")
async def decision(
    application_id: str,
    body: DecisionBody,
    user: dict = Depends(approved_ngo),
):
    match = APPLICATION_ID.match(application_id)
    if not match:
        raise HTTPException(400, "Invalid application ID.")
    opp = await run_in_threadpool(owned_opportunity, match.group(1), user)

    try:
        status, data = await run_in_threadpool(
            volunteer_client.decide, application_id, body.decision,
            user["userId"], int(opp["capacity"]), user["userId"],
        )
    except volunteer_client.VolunteerServiceUnavailable as error:
        raise unavailable(error) from None

    if status == 200 and isinstance(data.get("application"), dict):
        return {"application": data["application"]}
    if status in (400, 403, 404, 409):
        raise HTTPException(status, data.get("detail", "Request failed."))
    raise unavailable(RuntimeError(f"unexpected decision response {status}"))
