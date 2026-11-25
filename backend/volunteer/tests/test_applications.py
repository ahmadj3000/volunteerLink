"""Run with:  cd backend/volunteer && python -m pytest

DynamoDB and the NGO API are replaced with in-memory fakes.
"""

import os

os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")

from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import applications
import auth
import ngo_client
from main import app

client = TestClient(app)
FUTURE = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
PAST = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")


def opportunity(**overrides):
    opp = {
        "opportunityId": "opp-1", "ngoId": "ngo-9", "title": "Food distribution",
        "status": "open", "capacity": 3, "startAt": FUTURE, "endAt": FUTURE,
        "contactEmail": "ngo@example.org",
    }
    opp.update(overrides)
    return opp


class FakeVolunteers:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get(Key["volunteerId"])
        return {"Item": item} if item else {}


class FakeApplications:
    def __init__(self):
        self.items = {}
        self.page_size_seen = None

    def put_item(self, Item, ConditionExpression):
        assert ConditionExpression == "attribute_not_exists(applicationId)"
        if Item["applicationId"] in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[Item["applicationId"]] = dict(Item)

    def query(self, IndexName, KeyConditionExpression, ExpressionAttributeValues,
              ScanIndexForward, Limit, ExclusiveStartKey=None):
        assert IndexName == "byVolunteer"
        mine = [i for i in self.items.values()
                if i["volunteerId"] == ExpressionAttributeValues[":v"]]
        mine.sort(key=lambda i: (i["appliedAt"], i["applicationId"]), reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            ids = [i["applicationId"] for i in mine]
            mine = mine[ids.index(ExclusiveStartKey["applicationId"]) + 1:]
        page = mine[:Limit]
        result = {"Items": page}
        if len(mine) > Limit:
            last = page[-1]
            result["LastEvaluatedKey"] = {
                k: last[k] for k in ("applicationId", "volunteerId", "appliedAt")
            }
        return result


@pytest.fixture
def env(monkeypatch):
    vols, apps = FakeVolunteers(), FakeApplications()
    opps = {"opp-1": opportunity()}
    monkeypatch.setattr(applications, "volunteers_table", lambda: vols)
    monkeypatch.setattr(applications, "applications_table", lambda: apps)
    monkeypatch.setattr(ngo_client, "get_opportunity", lambda oid: opps.get(oid))
    vols.items["vol-1"] = {"volunteerId": "vol-1", "fullName": "Sara"}
    return {"vols": vols, "apps": apps, "opps": opps}


@pytest.fixture
def as_user():
    def login(sub="vol-1", groups=("volunteer",)):
        app.dependency_overrides[auth.current_user] = lambda: {"userId": sub, "groups": list(groups)}
    yield login
    app.dependency_overrides.clear()


def apply(body=None):
    return client.post("/applications", json=body or {"opportunityId": "opp-1"})


# --- apply ---

def test_apply_creates_pending_application(env, as_user):
    as_user()
    r = apply({"opportunityId": "opp-1", "message": "  I have first aid  "})
    assert r.status_code == 201
    a = r.json()["application"]
    assert a["applicationId"] == "opp-1_vol-1"
    assert a["status"] == "pending" and a["decidedAt"] is None
    assert a["ngoId"] == "ngo-9"                      # from the NGO API, not the browser
    assert a["opportunityTitle"] == "Food distribution"
    assert a["message"] == "I have first aid"
    assert "ngoContactEmail" not in a                 # internal only
    assert env["apps"].items["opp-1_vol-1"]["ngoContactEmail"] == "ngo@example.org"


def test_apply_twice_is_409(env, as_user):
    as_user()
    assert apply().status_code == 201
    r = apply()
    assert r.status_code == 409 and "already applied" in r.json()["detail"]
    assert len(env["apps"].items) == 1


def test_two_volunteers_same_opportunity(env, as_user):
    env["vols"].items["vol-2"] = {"volunteerId": "vol-2", "fullName": "Omar"}
    as_user("vol-1")
    assert apply().status_code == 201
    as_user("vol-2")
    assert apply().status_code == 201
    assert set(env["apps"].items) == {"opp-1_vol-1", "opp-1_vol-2"}


def test_no_token_401(env):
    assert apply().status_code == 401
    assert client.get("/me/applications").status_code == 401


@pytest.mark.parametrize("groups", [("ngo",), ("admin",), ()])
def test_non_volunteers_403(env, as_user, groups):
    as_user(groups=groups)
    assert apply().status_code == 403
    assert client.get("/me/applications").status_code == 403


def test_no_profile_409(env, as_user):
    as_user("vol-without-profile")
    r = apply()
    assert r.status_code == 409 and "profile" in r.json()["detail"]


def test_unknown_opportunity_404(env, as_user):
    as_user()
    assert apply({"opportunityId": "nope"}).status_code == 404


@pytest.mark.parametrize("status", ["closed", "draft"])
def test_not_open_409(env, as_user, status):
    env["opps"]["opp-1"]["status"] = status
    as_user()
    assert apply().status_code == 409


def test_already_started_409(env, as_user):
    env["opps"]["opp-1"]["startAt"] = PAST
    as_user()
    assert apply().status_code == 409


def test_ngo_api_down_503(env, as_user, monkeypatch):
    def down(oid):
        raise ngo_client.OpportunityServiceUnavailable("timeout")
    monkeypatch.setattr(ngo_client, "get_opportunity", down)
    as_user()
    assert apply().status_code == 503
    assert env["apps"].items == {}


def test_real_client_unconfigured_503(env, as_user, monkeypatch):
    # Without NGO_INTERNAL_API_URL / NGO_CALLER_ROLE_ARN the real client refuses.
    monkeypatch.undo()
    monkeypatch.setattr(applications, "volunteers_table", lambda: env["vols"])
    monkeypatch.setattr(applications, "applications_table", lambda: env["apps"])
    as_user()
    assert apply().status_code == 503


@pytest.mark.parametrize("body", [
    {},                                                   # missing opportunityId
    {"opportunityId": "opp-1", "volunteerId": "victim"},  # can't pick who applies
    {"opportunityId": "opp-1", "status": "accepted"},     # can't pick status
    {"opportunityId": "opp-1", "ngoId": "other"},         # can't pick owner
    {"opportunityId": "a_b"},                             # "_" would break the ID
    {"opportunityId": 5},
    {"opportunityId": "opp-1", "message": "x" * 501},
    {"opportunityId": "opp-1", "message": 3},
])
def test_bad_bodies_400(env, as_user, body):
    as_user()
    assert client.post("/applications", json=body).status_code == 400


def test_bad_json_400(env, as_user):
    as_user()
    r = client.post("/applications", content="{nope", headers={"content-type": "application/json"})
    assert r.status_code == 400


# --- my applications ---

def test_my_applications_only_mine_newest_first(env, as_user):
    env["opps"]["opp-2"] = opportunity(opportunityId="opp-2", title="Shelter")
    env["vols"].items["vol-2"] = {"volunteerId": "vol-2", "fullName": "Omar"}
    as_user("vol-1")
    apply({"opportunityId": "opp-1"})
    env["apps"].items["opp-1_vol-1"]["appliedAt"] = "2026-11-20T10:00:00+00:00"
    apply({"opportunityId": "opp-2"})
    as_user("vol-2")
    apply({"opportunityId": "opp-1"})

    as_user("vol-1")
    body = client.get("/me/applications").json()
    assert [i["opportunityId"] for i in body["items"]] == ["opp-2", "opp-1"]
    assert body["nextCursor"] is None
    assert all("ngoContactEmail" not in i for i in body["items"])


def test_my_applications_paginates(env, as_user, monkeypatch):
    monkeypatch.setattr(applications, "PAGE_SIZE", 2)
    for n in range(5):
        env["opps"][f"opp-{n}"] = opportunity(opportunityId=f"opp-{n}")
    as_user()
    for n in range(5):
        apply({"opportunityId": f"opp-{n}"})
        env["apps"].items[f"opp-{n}_vol-1"]["appliedAt"] = f"2026-11-2{n}T00:00:00+00:00"

    seen, cursor = [], None
    while True:
        r = client.get("/me/applications", params={"cursor": cursor} if cursor else None).json()
        seen += [i["opportunityId"] for i in r["items"]]
        cursor = r["nextCursor"]
        if not cursor:
            break
    assert seen == ["opp-4", "opp-3", "opp-2", "opp-1", "opp-0"]


def test_cannot_use_someone_elses_cursor(env, as_user):
    other = applications.encode_cursor(
        {"applicationId": "opp-1_vol-2", "volunteerId": "vol-2", "appliedAt": "2026-11-20T00:00:00+00:00"}
    )
    as_user("vol-1")
    assert client.get("/me/applications", params={"cursor": other}).status_code == 400


@pytest.mark.parametrize("cursor", ["garbage!!", "e30=", "x" * 3000])
def test_bad_cursor_400(env, as_user, cursor):
    as_user()
    assert client.get("/me/applications", params={"cursor": cursor}).status_code == 400
