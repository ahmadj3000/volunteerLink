from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

import boto3
from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from auth import current_user

router = APIRouter()

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)
]
Skill = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]


class OpportunityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: ShortText
    description: Description
    location: ShortText
    skills: list[Skill] = Field(max_length=30)
    startAt: AwareDatetime
    endAt: AwareDatetime
    capacity: int = Field(strict=True, ge=1, le=10000)
    isCrisis: bool = Field(default=False, strict=True)

    @field_validator("skills")
    @classmethod
    def normalize_skills(cls, values):
        return list(dict.fromkeys(value.lower() for value in values))

    @model_validator(mode="after")
    def validate_dates(self):
        if self.startAt <= datetime.now(timezone.utc):
            raise ValueError("Start time must be in the future.")
        if self.endAt <= self.startAt:
            raise ValueError("End time must be after start time.")
        return self


def approved_ngo(user: dict = Depends(current_user)):
    if "ngo" not in user["groups"]:
        raise HTTPException(403, "The ngo role is required.")

    table = boto3.resource("dynamodb", region_name="us-east-1").Table("vl-ngos")
    item = table.get_item(
        Key={"ngoId": user["userId"]},
        ConsistentRead=True,
    ).get("Item")

    if not item or item.get("status") != "approved":
        raise HTTPException(403, "Your organization must be approved before posting.")

    return user


@router.post("/ngo/opportunities", status_code=201)
def create_opportunity(
    body: OpportunityCreate,
    user: dict = Depends(approved_ngo),
):
    timestamp = datetime.now(timezone.utc).isoformat()
    item = {
        **body.model_dump(exclude={"startAt", "endAt"}),
        "startAt": body.startAt.astimezone(timezone.utc).isoformat(),
        "endAt": body.endAt.astimezone(timezone.utc).isoformat(),
        "opportunityId": str(uuid4()),
        "ngoId": user["userId"],
        "status": "open",
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "vl-opportunities"
    )
    table.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(opportunityId)",
    )
    return {"opportunity": item}


@router.get("/opportunities")
def list_opportunities(
    cursor: str | None = None,
    user: dict = Depends(current_user),
):
    from uuid import UUID
    from boto3.dynamodb.conditions import Attr

    scan_options = {
        "Limit": 25,
        "FilterExpression": Attr("status").eq("open"),
        "ProjectionExpression": (
            "opportunityId, title, description, #location, skills, "
            "startAt, endAt, #capacity, isCrisis"
        ),
        "ExpressionAttributeNames": {
            "#location": "location",
            "#capacity": "capacity",
        },
        "ConsistentRead": True,
    }

    if cursor is not None:
        try:
            UUID(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid pagination cursor.")
        scan_options["ExclusiveStartKey"] = {"opportunityId": cursor}

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "vl-opportunities"
    )
    response = table.scan(**scan_options)

    return {
        "items": response.get("Items", []),
        "nextCursor": response.get("LastEvaluatedKey", {}).get("opportunityId"),
    }


class OpportunityClose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: str = Field(min_length=1, max_length=80)


@router.get("/ngo/opportunities")
def list_own_opportunities(
    cursor: str | None = None,
    user: dict = Depends(approved_ngo),
):
    from uuid import UUID
    from boto3.dynamodb.conditions import Attr

    fields = [
        "opportunityId", "title", "description", "location", "skills",
        "startAt", "endAt", "capacity", "isCrisis", "status", "updatedAt",
    ]
    names = {f"#f{i}": field for i, field in enumerate(fields)}
    options = {
        "Limit": 25,
        "FilterExpression": Attr("ngoId").eq(user["userId"]),
        "ProjectionExpression": ", ".join(names),
        "ExpressionAttributeNames": names,
        "ConsistentRead": True,
    }
    if cursor is not None:
        try:
            UUID(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid pagination cursor.")
        options["ExclusiveStartKey"] = {"opportunityId": cursor}

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "vl-opportunities"
    )
    response = table.scan(**options)
    return {
        "items": response.get("Items", []),
        "nextCursor": response.get("LastEvaluatedKey", {}).get("opportunityId"),
    }


@router.patch("/ngo/opportunities/{opportunity_id}/close")
def close_opportunity(
    opportunity_id: str,
    body: OpportunityClose,
    user: dict = Depends(approved_ngo),
):
    from uuid import UUID
    from botocore.exceptions import ClientError

    try:
        UUID(opportunity_id)
    except ValueError:
        raise HTTPException(400, "Invalid opportunity ID.")

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "vl-opportunities"
    )
    try:
        table.update_item(
            Key={"opportunityId": opportunity_id},
            UpdateExpression="SET #status = :closed, #updated = :now",
            ConditionExpression=(
                "attribute_exists(opportunityId) AND ngoId = :owner "
                "AND #status = :open AND #updated = :expected"
            ),
            ExpressionAttributeNames={
                "#status": "status",
                "#updated": "updatedAt",
            },
            ExpressionAttributeValues={
                ":owner": user["userId"],
                ":open": "open",
                ":closed": "closed",
                ":expected": body.expectedUpdatedAt,
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(
                409,
                "Cannot close this post. It may have changed, be closed, "
                "or belong to another NGO. Refresh the list.",
            )
        raise

    return {"message": "Opportunity closed."}


class OpportunityEdit(OpportunityCreate):
    expectedUpdatedAt: str = Field(min_length=1, max_length=80)


@router.patch("/ngo/opportunities/{opportunity_id}")
def edit_opportunity(
    opportunity_id: str,
    body: OpportunityEdit,
    user: dict = Depends(approved_ngo),
):
    from uuid import UUID
    from botocore.exceptions import ClientError

    try:
        UUID(opportunity_id)
    except ValueError:
        raise HTTPException(400, "Invalid opportunity ID.")

    changes = {
        **body.model_dump(
            exclude={"startAt", "endAt", "expectedUpdatedAt"}
        ),
        "startAt": body.startAt.astimezone(timezone.utc).isoformat(),
        "endAt": body.endAt.astimezone(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    names = {
        f"#f{i}": field for i, field in enumerate(changes)
    }
    values = {
        f":v{i}": value for i, value in enumerate(changes.values())
    }
    names.update({"#status": "status", "#updated": "updatedAt"})
    values.update({
        ":owner": user["userId"],
        ":open": "open",
        ":expected": body.expectedUpdatedAt,
    })

    table = boto3.resource("dynamodb", region_name="us-east-1").Table(
        "vl-opportunities"
    )
    try:
        table.update_item(
            Key={"opportunityId": opportunity_id},
            UpdateExpression="SET " + ", ".join(
                f"#f{i} = :v{i}" for i in range(len(changes))
            ),
            ConditionExpression=(
                "attribute_exists(opportunityId) AND ngoId = :owner "
                "AND #status = :open AND #updated = :expected"
            ),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(
                409,
                "Cannot edit this post. It may have changed, be closed, "
                "or belong to another NGO. Refresh the list.",
            )
        raise

    return {
        "opportunity": {
            **changes,
            "opportunityId": opportunity_id,
            "status": "open",
        }
    }
