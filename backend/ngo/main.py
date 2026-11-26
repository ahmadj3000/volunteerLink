from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints

from auth import current_user

app = FastAPI(title="VolunteerLink NGO API")

INTERNAL_CALLER_ARN = (
    "arn:aws:sts::281780646139:"
    "assumed-role/vl-ngo-internal-api-caller/vl-volunteer-api"
)


def require_group(group: str):
    def check(user: dict = Depends(current_user)):
        if group not in user["groups"]:
            raise HTTPException(403, f"The {group} role is required.")
        return user

    return check


def ngos():
    return boto3.resource("dynamodb", region_name="us-east-1").Table("vl-ngos")


def now():
    return datetime.now(timezone.utc).isoformat()


@app.exception_handler(ClientError)
async def aws_error(request, error):
    code = error.response["Error"]["Code"]
    print(f"DynamoDB operation failed: {code}", flush=True)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable. Check backend AWS access."},
    )


@app.exception_handler(BotoCoreError)
async def connection_error(request, error):
    print(f"AWS connection failed: {type(error).__name__}", flush=True)
    return JSONResponse(
        status_code=503,
        content={"detail": "AWS connection unavailable. Check backend credentials."},
    )


Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)
]
Location = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)
]


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    description: Description
    location: Location
    contactEmail: EmailStr


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected"]


@app.get("/health")
def health():
    return {"status": "ok", "service": "vl-ngo-api"}


@app.get("/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/ngo/access")
def ngo_access(user: dict = Depends(require_group("ngo"))):
    return {"message": "NGO role verified.", **user}


@app.get("/admin/access")
def admin_access(user: dict = Depends(require_group("admin"))):
    return {"message": "Admin role verified.", **user}


@app.get("/ngo/registration")
def my_registration(user: dict = Depends(require_group("ngo"))):
    response = ngos().get_item(
        Key={"ngoId": user["userId"]},
        ConsistentRead=True,
    )
    return {"ngo": response.get("Item")}


@app.post("/ngo/registration", status_code=201)
def register(body: Registration, user: dict = Depends(require_group("ngo"))):
    timestamp = now()
    item = {
        **body.model_dump(),
        "ngoId": user["userId"],
        "status": "pending",
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }

    try:
        ngos().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(ngoId)",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(
                409, "Your organization is already registered."
            ) from None
        raise

    return {"ngo": item}


@app.get("/admin/ngos")
def list_ngos(
    cursor: str | None = Query(default=None, max_length=36),
    user: dict = Depends(require_group("admin")),
):
    options = {"Limit": 25, "ConsistentRead": True}

    if cursor:
        try:
            UUID(cursor)
        except ValueError:
            raise HTTPException(400, "Invalid page cursor.") from None
        options["ExclusiveStartKey"] = {"ngoId": cursor}

    response = ngos().scan(**options)
    return {
        "items": response.get("Items", []),
        "nextCursor": response.get("LastEvaluatedKey", {}).get("ngoId"),
    }


@app.patch("/admin/ngos/{ngo_id}")
def review(
    ngo_id: UUID,
    body: Decision,
    user: dict = Depends(require_group("admin")),
):
    if str(ngo_id) == user["userId"]:
        raise HTTPException(403, "You cannot review your own organization.")

    try:
        response = ngos().update_item(
            Key={"ngoId": str(ngo_id)},
            UpdateExpression=(
                "SET #s = :decision, updatedAt = :time, "
                "reviewedAt = :time, reviewedBy = :reviewer"
            ),
            ConditionExpression="attribute_exists(ngoId) AND #s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":decision": body.status,
                ":pending": "pending",
                ":time": now(),
                ":reviewer": user["userId"],
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(
                409,
                "This registration no longer exists or has already been reviewed.",
            ) from None
        raise

    return {"ngo": response["Attributes"]}


@app.get("/internal/opportunities/{opportunity_id}")
def internal_opportunity(opportunity_id: str, request: Request):
    # Trust this header only because API Gateway overwrites it on the
    # IAM integration and removes it on every public integration.
    callers = request.headers.getlist("x-vl-caller-arn")
    if callers != [INTERNAL_CALLER_ARN]:
        raise HTTPException(403, "Internal access denied.")

    try:
        UUID(opportunity_id)
    except ValueError:
        raise HTTPException(404, "Opportunity not found.") from None

    table = boto3.resource(
        "dynamodb", region_name="us-east-1"
    ).Table("vl-opportunities")

    opportunity = table.get_item(
        Key={"opportunityId": opportunity_id},
        ConsistentRead=True,
    ).get("Item")

    if not opportunity:
        raise HTTPException(404, "Opportunity not found.")

    organization = ngos().get_item(
        Key={"ngoId": opportunity["ngoId"]},
        ConsistentRead=True,
    ).get("Item") or {}

    return {
        "opportunityId": opportunity["opportunityId"],
        "ngoId": opportunity["ngoId"],
        "title": opportunity["title"],
        "status": opportunity["status"],
        "capacity": int(opportunity["capacity"]),
        "startAt": opportunity["startAt"],
        "endAt": opportunity["endAt"],
        "contactEmail": organization.get("contactEmail"),
    }


from opportunities import router as opportunities_router

app.include_router(opportunities_router)

from applications import router as applications_router

app.include_router(applications_router)