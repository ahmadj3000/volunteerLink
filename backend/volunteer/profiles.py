"""Volunteer profile routes, migrated from the vl-profile Lambda.

GET /me/profile  -> the caller's profile (404 if none yet)
PUT /me/profile  -> create or replace the caller's profile fields

Validation, normalization, timestamps and ownership are unchanged from
backend/profile/handler.py. Only the Lambda event/response handling was
replaced by ordinary FastAPI routes. The caller's identity comes from the
verified Cognito token ("sub"), never from the body.
"""

import os
from datetime import datetime, timezone

import boto3
from fastapi import APIRouter, Depends, HTTPException, Request

from auth import current_user

router = APIRouter()

AVAILABILITY = {"weekdays", "weekends", "evenings", "emergency"}
LIMITS = {"fullName": 100, "location": 60, "bio": 1000, "skill": 40, "skills": 30}

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name="us-east-1").Table(
            os.environ.get("VOLUNTEERS_TABLE", "vl-volunteers")
        )
    return _table


class ValidationError(Exception):
    pass


def _text(body, key, required=False):
    val = body.get(key)
    if val is None:
        if required:
            raise ValidationError(f"'{key}' is required")
        return None
    if not isinstance(val, str) or not val.strip():
        raise ValidationError(f"'{key}' must be a non-empty string")
    val = val.strip()
    if len(val) > LIMITS[key]:
        raise ValidationError(f"'{key}' is longer than {LIMITS[key]} characters")
    return val


def _string_list(body, key, normalize, allowed=None, max_items=30, max_len=40):
    val = body.get(key)
    if val is None:
        return None
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        raise ValidationError(f"'{key}' must be a list of strings")
    out = []
    for v in val:
        v = normalize(v)
        if not v:
            continue
        if len(v) > max_len:
            raise ValidationError(f"each item in '{key}' must be at most {max_len} characters")
        if allowed and v not in allowed:
            raise ValidationError(f"'{key}' items must be one of {sorted(allowed)}")
        if v not in out:
            out.append(v)
    if len(out) > max_items:
        raise ValidationError(f"'{key}' can have at most {max_items} items")
    return out


def validate_profile(body):
    """Return a clean dict of allowed fields, or raise ValidationError."""
    if not isinstance(body, dict):
        raise ValidationError("body must be a JSON object")

    unknown = set(body) - {"fullName", "skills", "location", "availability", "bio"}
    if unknown:
        raise ValidationError(f"unknown fields: {sorted(unknown)}")

    profile = {
        "fullName": _text(body, "fullName", required=True),
        "location": _text(body, "location"),
        "bio": _text(body, "bio"),
        # lowercased + deduped so matching compares like with like
        "skills": _string_list(
            body, "skills", lambda s: " ".join(s.lower().split()),
            max_items=LIMITS["skills"], max_len=LIMITS["skill"],
        ),
        "availability": _string_list(
            body, "availability", lambda s: s.strip().lower(), allowed=AVAILABILITY,
        ),
    }
    return {k: v for k, v in profile.items() if v is not None}


@router.get("/me/profile")
def get_profile(user: dict = Depends(current_user)):
    item = table().get_item(Key={"volunteerId": user["userId"]}).get("Item")
    if not item:
        raise HTTPException(404, "profile not found")
    return item


@router.put("/me/profile")
async def put_profile(request: Request, user: dict = Depends(current_user)):
    # Raw JSON + the original validator (not a Pydantic model) so behaviour
    # matches the Lambda exactly, including the error messages.
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "body is not valid JSON") from None
    try:
        profile = validate_profile(body)
    except ValidationError as e:
        raise HTTPException(400, str(e)) from None

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # SET the provided fields, REMOVE optional ones that were left out,
    # keep createdAt from the first write.
    names, values, sets = {}, {":now": now}, []
    for i, (k, v) in enumerate(profile.items()):
        names[f"#f{i}"] = k
        values[f":v{i}"] = v
        sets.append(f"#f{i} = :v{i}")
    sets += ["updatedAt = :now", "createdAt = if_not_exists(createdAt, :now)"]

    removed = [k for k in ("location", "bio", "skills", "availability") if k not in profile]
    for j, k in enumerate(removed):
        names[f"#r{j}"] = k
    expr = "SET " + ", ".join(sets)
    if removed:
        expr += " REMOVE " + ", ".join(f"#r{j}" for j in range(len(removed)))

    result = table().update_item(
        Key={"volunteerId": user["userId"]},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return result["Attributes"]
