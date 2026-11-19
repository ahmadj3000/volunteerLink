"""vl-profile — volunteer profile API (Volunteer account).

Routes (HTTP API, payload v2, JWT-authorized by the shared Cognito pool):
  GET /me/profile  -> return the caller's profile
  PUT /me/profile  -> create or replace the caller's profile fields

The caller's identity always comes from the verified token ("sub" claim),
never from the request body, so a user can only touch their own profile.
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AVAILABILITY = {"weekdays", "weekends", "evenings", "emergency"}
LIMITS = {"fullName": 100, "location": 60, "bio": 1000, "skill": 40, "skills": 30}

_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
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
        # lowercased + deduped so the matching step compares like with like
        "skills": _string_list(
            body, "skills", lambda s: " ".join(s.lower().split()),
            max_items=LIMITS["skills"], max_len=LIMITS["skill"],
        ),
        "availability": _string_list(
            body, "availability", lambda s: s.strip().lower(), allowed=AVAILABILITY,
        ),
    }
    return {k: v for k, v in profile.items() if v is not None}


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise ValidationError("body is not valid JSON")


def get_profile(sub):
    item = table().get_item(Key={"volunteerId": sub}).get("Item")
    if not item:
        return _response(404, {"message": "profile not found"})
    return _response(200, item)


def put_profile(sub, event):
    profile = validate_profile(_parse_body(event))
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
        Key={"volunteerId": sub},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )
    return _response(200, result["Attributes"])


def lambda_handler(event, context):
    route = event.get("routeKey")
    try:
        sub = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
    except (KeyError, TypeError):
        return _response(401, {"message": "unauthorized"})

    try:
        if route == "GET /me/profile":
            return get_profile(sub)
        if route == "PUT /me/profile":
            return put_profile(sub, event)
        return _response(404, {"message": "route not found"})
    except ValidationError as e:
        return _response(400, {"message": str(e)})
    except Exception:
        # log the stack trace, never leak internals to the client
        logger.exception("unhandled error on %s", route)
        return _response(500, {"message": "internal error"})
