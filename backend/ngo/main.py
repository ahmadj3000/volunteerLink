from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from auth import current_user

app = FastAPI(title="VolunteerLink NGO API")


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
            raise HTTPException(409, "Your organization is already registered.") from None
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
                409, "This registration no longer exists or has already been reviewed."
            ) from None
        raise

    return {"ngo": response["Attributes"]}


from opportunities import router as opportunities_router
app.include_router(opportunities_router)
