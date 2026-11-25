"""Tests for the NGO applicant review routes and the Volunteer API client.
DynamoDB, STS and HTTP are faked.

Run with:  cd backend/ngo && python -m pytest
"""

import os

os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import applications
import volunteer_client
from main import app
from opportunities import approved_ngo

client = TestClient(app)
OPP_ID = "11111111-1111-4111-8111-111111111111"
VOL_ID = "22222222-2222-4222-8222-222222222222"
APP_ID = f"{OPP_ID}_{VOL_ID}"


class FakeOpportunities:
    def __init__(self):
        self.items = {OPP_ID: {"opportunityId": OPP_ID, "ngoId": "ngo-9", "capacity": 3,
                               "title": "Food", "status": "open"}}

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get(Key["opportunityId"])
        return {"Item": item} if item else {}


class FakeVolunteerAPI:
    def __init__(self):
        self.calls = []
        self.decide_reply = (200, {"application": {"applicationId": APP_ID, "status": "accepted"}})

    def list_applications(self, opportunity_id, cursor=None):
        self.calls.append(("list", opportunity_id, cursor))
        return {"opportunityId": opportunity_id, "acceptedCount": 1,
                "items": [{"applicationId": APP_ID, "fullName": "Sara", "score": None}],
                "nextCursor": None}

    def decide(self, application_id, decision, ngo_id, capacity, decided_by):
        self.calls.append(("decide", application_id, decision, ngo_id, capacity, decided_by))
        return self.decide_reply


@pytest.fixture
def env(monkeypatch):
    opps, vol = FakeOpportunities(), FakeVolunteerAPI()
    monkeypatch.setattr(applications, "opportunities_table", lambda: opps)
    monkeypatch.setattr(volunteer_client, "list_applications", vol.list_applications)
    monkeypatch.setattr(volunteer_client, "decide", vol.decide)
    app.dependency_overrides[approved_ngo] = lambda: {"userId": "ngo-9", "groups": ["ngo"]}
    yield opps, vol
    app.dependency_overrides.clear()


def as_ngo(user_id):
    app.dependency_overrides[approved_ngo] = lambda: {"userId": user_id, "groups": ["ngo"]}


# --- applicants ---

def test_owner_sees_applicants_with_capacity(env):
    _, vol = env
    r = client.get(f"/ngo/opportunities/{OPP_ID}/applicants")
    assert r.status_code == 200
    body = r.json()
    assert body["capacity"] == 3 and body["acceptedCount"] == 1
    assert body["items"][0]["fullName"] == "Sara"
    assert vol.calls == [("list", OPP_ID, None)]


def test_cursor_is_passed_through(env):
    _, vol = env
    client.get(f"/ngo/opportunities/{OPP_ID}/applicants", params={"cursor": "abc"})
    assert vol.calls == [("list", OPP_ID, "abc")]


def test_other_ngo_cannot_see_applicants(env):
    _, vol = env
    as_ngo("ngo-other")
    assert client.get(f"/ngo/opportunities/{OPP_ID}/applicants").status_code == 403
    assert vol.calls == []                       # never asked the Volunteer account


def test_unknown_opportunity_404(env):
    _, vol = env
    assert client.get("/ngo/opportunities/nope/applicants").status_code == 404
    assert vol.calls == []


def test_volunteer_api_down_503(env, monkeypatch):
    def down(*a, **k):
        raise volunteer_client.VolunteerServiceUnavailable("timeout")
    monkeypatch.setattr(volunteer_client, "list_applications", down)
    assert client.get(f"/ngo/opportunities/{OPP_ID}/applicants").status_code == 503


def test_needs_approved_ngo(env):
    app.dependency_overrides.clear()             # real approved_ngo -> no token -> 401
    assert client.get(f"/ngo/opportunities/{OPP_ID}/applicants").status_code == 401
    assert client.post(f"/ngo/applications/{APP_ID}/decision", json={"decision": "accepted"}).status_code == 401


# --- decision ---

def test_owner_decision_sends_verified_ngo_and_capacity(env):
    _, vol = env
    r = client.post(f"/ngo/applications/{APP_ID}/decision", json={"decision": "accepted"})
    assert r.status_code == 200 and r.json()["application"]["status"] == "accepted"
    assert vol.calls == [("decide", APP_ID, "accepted", "ngo-9", 3, "ngo-9")]


def test_other_ngo_cannot_decide(env):
    _, vol = env
    as_ngo("ngo-other")
    assert client.post(f"/ngo/applications/{APP_ID}/decision", json={"decision": "accepted"}).status_code == 403
    assert vol.calls == []


@pytest.mark.parametrize("status", [400, 403, 404, 409])
def test_volunteer_side_errors_pass_through(env, status):
    _, vol = env
    vol.decide_reply = (status, {"detail": "All slots for this opportunity are filled."})
    r = client.post(f"/ngo/applications/{APP_ID}/decision", json={"decision": "accepted"})
    assert r.status_code == status and r.json()["detail"].startswith("All slots")


def test_unexpected_decision_reply_503(env):
    _, vol = env
    vol.decide_reply = (200, {"oops": True})
    assert client.post(f"/ngo/applications/{APP_ID}/decision", json={"decision": "accepted"}).status_code == 503


@pytest.mark.parametrize("body", [
    {"decision": "maybe"}, {}, {"decision": "accepted", "capacity": 999},   # can't override capacity
    {"decision": "accepted", "ngoId": "someone-else"},
])
def test_bad_decision_bodies_422(env, body):
    _, vol = env
    assert client.post(f"/ngo/applications/{APP_ID}/decision", json=body).status_code == 422
    assert vol.calls == []


@pytest.mark.parametrize("app_id", ["no-underscore", "a_b_c"])
def test_bad_application_id_400(env, app_id):
    assert client.post(f"/ngo/applications/{app_id}/decision", json={"decision": "accepted"}).status_code == 400


# --- volunteer_client: STS + SigV4 + response mapping ---

class FakeSTS:
    def __init__(self):
        self.calls = 0

    def assume_role(self, RoleArn, RoleSessionName, DurationSeconds):
        self.calls += 1
        assert RoleSessionName == "vl-ngo-api"
        return {"Credentials": {"AccessKeyId": "ASIATEST", "SecretAccessKey": "s", "SessionToken": "tok",
                                "Expiration": datetime.now(timezone.utc) + timedelta(hours=1)}}


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("VOLUNTEER_INTERNAL_API_URL", "https://57bc1lggo1.execute-api.us-east-1.amazonaws.com")
    monkeypatch.setenv("VOLUNTEER_CALLER_ROLE_ARN", "arn:aws:iam::170813308606:role/vl-volunteer-internal-api-caller")
    monkeypatch.setitem(volunteer_client._cached, "credentials", None)
    monkeypatch.setitem(volunteer_client._cached, "expires", None)
    sts = FakeSTS()
    monkeypatch.setattr(volunteer_client, "_sts_client", lambda: sts)
    sent = {"reply": (200, b'{"application": {"status": "accepted"}}'), "requests": []}

    def send(method, url, headers, body):
        sent["requests"].append((method, url, headers, body))
        return sent["reply"]
    monkeypatch.setattr(volunteer_client, "_send", send)
    return sts, sent


def test_client_decide_is_signed_post_with_body(wire):
    sts, sent = wire
    status, data = volunteer_client.decide(APP_ID, "accepted", "ngo-9", 3, "ngo-9")
    assert status == 200 and data["application"]["status"] == "accepted"
    method, url, headers, body = sent["requests"][0]
    assert method == "POST" and url.endswith(f"/internal/applications/{APP_ID}/decision")
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=ASIATEST/")
    assert "/us-east-1/execute-api/aws4_request" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "tok"
    assert json.loads(body) == {"decision": "accepted", "ngoId": "ngo-9", "capacity": 3, "decidedBy": "ngo-9"}


def test_client_caches_credentials(wire):
    sts, sent = wire
    volunteer_client.decide(APP_ID, "accepted", "ngo-9", 3, "ngo-9")
    volunteer_client.decide(APP_ID, "rejected", "ngo-9", 3, "ngo-9")
    assert sts.calls == 1


def test_client_backend_409_is_returned_not_raised(wire):
    _, sent = wire
    sent["reply"] = (409, b'{"detail": "Application is already accepted."}')
    assert volunteer_client.decide(APP_ID, "accepted", "ngo-9", 3, "ngo-9")[0] == 409


@pytest.mark.parametrize("reply", [
    (403, b'{"message": "Forbidden"}'),       # API Gateway: IAM refused
    (404, b'{"message": "Not Found"}'),       # API Gateway: route missing
    (500, b'{"detail": "boom"}'),
    (200, b"not json"),
])
def test_client_gateway_and_server_errors_are_unavailable(wire, reply):
    _, sent = wire
    sent["reply"] = reply
    with pytest.raises(volunteer_client.VolunteerServiceUnavailable):
        volunteer_client.decide(APP_ID, "accepted", "ngo-9", 3, "ngo-9")


def test_client_list_encodes_cursor(wire):
    _, sent = wire
    sent["reply"] = (200, b'{"items": [], "acceptedCount": 0, "nextCursor": null}')
    volunteer_client.list_applications(OPP_ID, "a+b/c=")
    method, url, headers, body = sent["requests"][0]
    assert method == "GET" and body is None
    assert url.endswith(f"/internal/opportunities/{OPP_ID}/applications?cursor=a%2Bb%2Fc%3D")


def test_client_unconfigured_is_unavailable(wire, monkeypatch):
    sts, sent = wire
    monkeypatch.delenv("VOLUNTEER_CALLER_ROLE_ARN")
    with pytest.raises(volunteer_client.VolunteerServiceUnavailable):
        volunteer_client.list_applications(OPP_ID)
    assert sts.calls == 0 and sent["requests"] == []
