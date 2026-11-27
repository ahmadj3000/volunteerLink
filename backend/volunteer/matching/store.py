"""DynamoDB access for matching (all Volunteer account). Owned by Tarek.

Tables
  vl-volunteers            Ahmad's profiles. Matching only READS it.
  vl-volunteer-embeddings  PK volunteerId. One profile vector per volunteer.
  vl-opportunity-index     PK opportunityId. The Volunteer account's own read
                           copy of NGO opportunities (+ their vectors). The
                           Volunteer backend never reads NGO tables.

Config: VOLUNTEERS_TABLE, VOLUNTEER_EMBEDDINGS_TABLE, OPPORTUNITY_INDEX_TABLE.
"""

import os

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from matching.embeddings import RECORD_FIELDS

BATCH_LIMIT = 100  # BatchGetItem takes at most 100 keys per call

_tables = {}


def _table(env_var, default):
    name = os.environ.get(env_var, default)
    if name not in _tables:
        _tables[name] = boto3.resource("dynamodb", region_name="us-east-1").Table(name)
    return _tables[name]


def volunteers():
    return _table("VOLUNTEERS_TABLE", "vl-volunteers")


def volunteer_embeddings():
    return _table("VOLUNTEER_EMBEDDINGS_TABLE", "vl-volunteer-embeddings")


def opportunity_index():
    return _table("OPPORTUNITY_INDEX_TABLE", "vl-opportunity-index")


def _batch_get(table, key_name, ids, projection=None, names=None):
    """BatchGetItem in chunks of 100, retrying keys DynamoDB hands back as unprocessed."""
    found = {}
    unique = list(dict.fromkeys(ids))
    for start in range(0, len(unique), BATCH_LIMIT):
        request = {"Keys": [{key_name: i} for i in unique[start:start + BATCH_LIMIT]]}
        if projection:
            request["ProjectionExpression"] = projection
        if names:
            request["ExpressionAttributeNames"] = names
        pending = {table.name: request}
        for _ in range(3):
            response = table.meta.client.batch_get_item(RequestItems=pending)
            for item in response.get("Responses", {}).get(table.name, []):
                found[item[key_name]] = item
            pending = response.get("UnprocessedKeys") or {}
            if not pending:
                break
    return found


# --- volunteers (read only) + their vectors ---

PROFILE_PROJECTION = "volunteerId, skills, availability, #loc, bio"


def get_profile(volunteer_id):
    return volunteers().get_item(Key={"volunteerId": volunteer_id}).get("Item")


def get_profiles(volunteer_ids):
    return _batch_get(volunteers(), "volunteerId", volunteer_ids,
                      PROFILE_PROJECTION, {"#loc": "location"})


def get_volunteer_embedding(volunteer_id):
    return volunteer_embeddings().get_item(Key={"volunteerId": volunteer_id}).get("Item")


def get_volunteer_embeddings(volunteer_ids):
    return _batch_get(volunteer_embeddings(), "volunteerId", volunteer_ids)


def put_volunteer_embedding(volunteer_id, record):
    # Our own table, last writer wins: if a profile changes mid-way, the hash
    # no longer matches on the next read and the vector is simply re-made.
    volunteer_embeddings().put_item(Item={"volunteerId": volunteer_id, **record})


# --- opportunity index ---

def get_opportunity(opportunity_id):
    return opportunity_index().get_item(
        Key={"opportunityId": opportunity_id}, ConsistentRead=True,
    ).get("Item")


def put_opportunity_if_newer(item):
    """Idempotent upsert. Returns True if written, False if we already had
    this version or a newer one (same or older updatedAt does nothing)."""
    try:
        opportunity_index().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(opportunityId) OR updatedAt < :u",
            ExpressionAttributeValues={":u": item["updatedAt"]},
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def attach_opportunity_embedding(opportunity_id, updated_at, record):
    """Store a vector only if the row is still the version it was made from,
    so an old text's vector never lands on a newer row."""
    names = {f"#e{i}": k for i, k in enumerate(RECORD_FIELDS)}
    values = {f":e{i}": record[k] for i, k in enumerate(RECORD_FIELDS)}
    values[":u"] = updated_at
    try:
        opportunity_index().update_item(
            Key={"opportunityId": opportunity_id},
            UpdateExpression="SET " + ", ".join(f"#e{i} = :e{i}" for i in range(len(RECORD_FIELDS))),
            ConditionExpression="updatedAt = :u",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def open_opportunities():
    """Every open opportunity. A full Scan: fine at demo scale (tens to a few
    hundred rows); see the scaling note in docs/matching.md."""
    params = {"FilterExpression": Attr("status").eq("open")}
    items = []
    while True:
        response = opportunity_index().scan(**params)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
