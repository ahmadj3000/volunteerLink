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
