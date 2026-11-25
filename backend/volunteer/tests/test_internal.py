"""Tests for the NGO-backend-only internal routes. DynamoDB is faked in memory,
including the accept transaction's all-or-nothing behaviour.

Run with:  cd backend/volunteer && python -m pytest
"""

import os

os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")

import copy

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import applications
import internal
from main import app

client = TestClient(app)
CALLER = "arn:aws:sts::170813308606:assumed-role/vl-volunteer-internal-api-caller/vl-ngo-api"
HEADERS = {"x-vl-caller-arn": CALLER}


class Meta:
    def __init__(self, client):
        self.client = client


class FakeTable:
    def __init__(self, name, key, db):
        self.name, self.key, self.items, self.meta = name, key, {}, Meta(db)

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get(Key[self.key])
        return {"Item": copy.deepcopy(item)} if item else {}

    def query(self, IndexName, KeyConditionExpression, ExpressionAttributeValues,
              ScanIndexForward, Limit, ExclusiveStartKey=None):
        assert IndexName == "byOpportunity"
        rows = [i for i in self.items.values()
                if i["opportunityId"] == ExpressionAttributeValues[":o"]]
        rows.sort(key=lambda i: (i["appliedAt"], i["applicationId"]), reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            ids = [i["applicationId"] for i in rows]
            rows = rows[ids.index(ExclusiveStartKey["applicationId"]) + 1:]
        page = rows[:Limit]
        out = {"Items": copy.deepcopy(page)}
        if len(rows) > Limit:
            out["LastEvaluatedKey"] = {k: page[-1][k] for k in ("applicationId", "opportunityId", "appliedAt")}
        return out


class FakeDB:
    """Just enough of the low-level client for internal.py's two write shapes."""

    def __init__(self):
        self.apps = FakeTable("vl-applications", "applicationId", self)
        self.vols = FakeTable("vl-volunteers", "volunteerId", self)
        self.slots = FakeTable("vl-opportunity-slots", "opportunityId", self)
        self.unprocessed_once = False

    def batch_get_item(self, RequestItems):
        (name, req), = RequestItems.items()
        keys = [k["volunteerId"] for k in req["Keys"]]
        if self.unprocessed_once:        # simulate DynamoDB throttling one key
            self.unprocessed_once = False
            return {"Responses": {name: [copy.deepcopy(self.vols.items[k]) for k in keys[1:] if k in self.vols.items]},
                    "UnprocessedKeys": {name: {**req, "Keys": [{"volunteerId": keys[0]}]}}}
        return {"Responses": {name: [copy.deepcopy(self.vols.items[k]) for k in keys if k in self.vols.items]}}

    def transact_write_items(self, TransactItems):
        reasons, staged = [], []
        for t in TransactItems:
            u = t["Update"]
            v = u.get("ExpressionAttributeValues", {})
            if u["TableName"] == "vl-applications":
                key = u["Key"]["applicationId"]["S"]
                item = self.apps.items.get(key)
                ok = item is not None and item["status"] == v[":pending"]["S"] and item["ngoId"] == v[":ngo"]["S"]
                staged.append(("app", key, v))
            else:
                key = u["Key"]["opportunityId"]["S"]
                count = self.slots.items.get(key, {}).get("acceptedCount")
                ok = count is None or count < int(v[":cap"]["N"])
                staged.append(("slot", key, v))
            reasons.append({"Code": "None" if ok else "ConditionalCheckFailed"})
        if any(r["Code"] != "None" for r in reasons):
            raise ClientError({"Error": {"Code": "TransactionCanceledException"},
                               "CancellationReasons": reasons}, "TransactWriteItems")
        for kind, key, v in staged:      # all conditions passed: apply everything
            if kind == "app":
                self.apps.items[key].update(status=v[":new"]["S"], decidedAt=v[":now"]["S"], decidedBy=v[":by"]["S"])
            else:
                slot = self.slots.items.setdefault(key, {"opportunityId": key})
                slot["acceptedCount"] = slot.get("acceptedCount", 0) + 1


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    monkeypatch.setenv("NGO_BACKEND_CALLER_ARN", CALLER)
    monkeypatch.setattr(applications, "applications_table", lambda: d.apps)
    monkeypatch.setattr(applications, "volunteers_table", lambda: d.vols)
    monkeypatch.setattr(internal, "slots_table", lambda: d.slots)
    for n in range(1, 4):
        d.vols.items[f"vol-{n}"] = {"volunteerId": f"vol-{n}", "fullName": f"Vol {n}",
                                    "location": "Beirut", "skills": ["first aid"], "bio": "private-ish"}
        d.apps.items[f"opp-1_vol-{n}"] = {
            "applicationId": f"opp-1_vol-{n}", "opportunityId": "opp-1", "volunteerId": f"vol-{n}",
            "ngoId": "ngo-9", "opportunityTitle": "Food", "opportunityStartAt": "2026-12-05T08:00:00+00:00",
            "status": "pending", "appliedAt": f"2026-11-2{n}T00:00:00+00:00",
            "ngoContactEmail": "ngo@example.org",
        }
    return d


def decide(app_id, decision="accepted", capacity=2, ngo="ngo-9", headers=HEADERS):
    return client.post(f"/internal/applications/{app_id}/decision", headers=headers,
                       json={"decision": decision, "ngoId": ngo, "capacity": capacity, "decidedBy": "ngo-user-1"})


# --- who can call ---

@pytest.mark.parametrize("headers", [
    {},                                                         # no header (e.g. via a public route)
    {"x-vl-caller-arn": "arn:aws:sts::170813308606:assumed-role/other-role/x"},
    {"x-vl-caller-arn": CALLER + "x"},
    {"Authorization": "Bearer some-volunteer-token"},           # a browser token is not enough
])
def test_only_the_ngo_backend_may_call(db, headers):
    assert client.get("/internal/opportunities/opp-1/applications", headers=headers).status_code == 403
    assert decide("opp-1_vol-1", headers=headers).status_code == 403
    assert db.apps.items["opp-1_vol-1"]["status"] == "pending"


def test_unconfigured_expected_caller_refuses_everyone(db, monkeypatch):
    monkeypatch.delenv("NGO_BACKEND_CALLER_ARN")
    assert client.get("/internal/opportunities/opp-1/applications", headers=HEADERS).status_code == 403


# --- applicants list ---

def test_list_applicants_with_profiles_newest_first(db):
    db.slots.items["opp-1"] = {"opportunityId": "opp-1", "acceptedCount": 1}
    r = client.get("/internal/opportunities/opp-1/applications", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["acceptedCount"] == 1
    assert [i["volunteerId"] for i in body["items"]] == ["vol-3", "vol-2", "vol-1"]
    first = body["items"][0]
    assert first["fullName"] == "Vol 3" and first["skills"] == ["first aid"] and first["score"] is None
    assert "bio" not in first and "ngoContactEmail" not in first   # only the agreed fields


def test_list_paginates_and_retries_unprocessed_keys(db, monkeypatch):
    monkeypatch.setattr(applications, "PAGE_SIZE", 2)
    db.unprocessed_once = True
    r1 = client.get("/internal/opportunities/opp-1/applications", headers=HEADERS).json()
    assert [i["fullName"] for i in r1["items"]] == ["Vol 3", "Vol 2"]      # nobody missing
    r2 = client.get("/internal/opportunities/opp-1/applications", headers=HEADERS,
                    params={"cursor": r1["nextCursor"]}).json()
    assert [i["volunteerId"] for i in r2["items"]] == ["vol-1"] and r2["nextCursor"] is None


def test_cursor_from_another_opportunity_is_rejected(db):
    cur = applications.encode_cursor({"applicationId": "opp-2_vol-1", "opportunityId": "opp-2",
                                      "appliedAt": "2026-11-20T00:00:00+00:00"})
    r = client.get("/internal/opportunities/opp-1/applications", headers=HEADERS, params={"cursor": cur})
    assert r.status_code == 400


def test_empty_opportunity(db):
    body = client.get("/internal/opportunities/opp-zzz/applications", headers=HEADERS).json()
    assert body == {"opportunityId": "opp-zzz", "acceptedCount": 0, "items": [], "nextCursor": None}


# --- decisions ---

def test_accept_flips_status_and_counts_slot(db):
    r = decide("opp-1_vol-1")
    assert r.status_code == 200
    a = r.json()["application"]
    assert a["status"] == "accepted" and a["decidedAt"]
    assert db.slots.items["opp-1"]["acceptedCount"] == 1


def test_reject_does_not_use_a_slot(db):
    assert decide("opp-1_vol-1", "rejected").json()["application"]["status"] == "rejected"
    assert "opp-1" not in db.slots.items


def test_capacity_is_a_hard_limit(db):
    assert decide("opp-1_vol-1", capacity=2).status_code == 200
    assert decide("opp-1_vol-2", capacity=2).status_code == 200
    r = decide("opp-1_vol-3", capacity=2)
    assert r.status_code == 409 and "filled" in r.json()["detail"]
    assert db.apps.items["opp-1_vol-3"]["status"] == "pending"      # all-or-nothing
    assert db.slots.items["opp-1"]["acceptedCount"] == 2
    assert decide("opp-1_vol-3", "rejected").status_code == 200     # rejecting still works when full


def test_last_slot_race_only_one_wins(db):
    results = [decide(f"opp-1_vol-{n}", capacity=1).status_code for n in (1, 2)]
    assert sorted(results) == [200, 409]
    assert db.slots.items["opp-1"]["acceptedCount"] == 1


def test_cannot_decide_twice(db):
    assert decide("opp-1_vol-1").status_code == 200
    r = decide("opp-1_vol-1", "rejected")
    assert r.status_code == 409 and "already accepted" in r.json()["detail"]
    assert db.slots.items["opp-1"]["acceptedCount"] == 1            # no double count


def test_other_ngo_cannot_decide(db):
    r = decide("opp-1_vol-1", ngo="ngo-other")
    assert r.status_code == 403
    assert db.apps.items["opp-1_vol-1"]["status"] == "pending" and "opp-1" not in db.slots.items


def test_unknown_application_404(db):
    assert decide("opp-1_vol-404").status_code == 404


@pytest.mark.parametrize("body", [
    {"decision": "maybe", "ngoId": "n", "capacity": 1, "decidedBy": "u"},
    {"decision": "accepted", "ngoId": "", "capacity": 1, "decidedBy": "u"},
    {"decision": "accepted", "ngoId": "n", "capacity": 0, "decidedBy": "u"},
    {"decision": "accepted", "ngoId": "n", "capacity": "3", "decidedBy": "u"},
    {"decision": "accepted", "ngoId": "n", "capacity": True, "decidedBy": "u"},
    {"decision": "accepted", "ngoId": "n", "capacity": 1},
    {"decision": "accepted", "ngoId": "n", "capacity": 1, "decidedBy": "u", "status": "accepted"},
    [],
])
def test_bad_decision_bodies_400(db, body):
    r = client.post("/internal/applications/opp-1_vol-1/decision", headers=HEADERS, json=body)
    assert r.status_code == 400
    assert db.apps.items["opp-1_vol-1"]["status"] == "pending"


@pytest.mark.parametrize("app_id", ["nounderscore", "a_b_c", "bad%20id_x"])
def test_bad_application_ids_400(db, app_id):
    assert decide(app_id).status_code == 400
