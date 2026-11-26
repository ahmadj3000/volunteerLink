"""Tests for the cross-account NGO API client. STS and HTTP are faked.

Run with:  cd backend/volunteer && python -m pytest
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

import ngo_client

API = "https://ozb9ner0t1.execute-api.us-east-1.amazonaws.com"
ROLE = "arn:aws:iam::281780646139:role/vl-ngo-internal-api-caller"
OPP = {
    "opportunityId": "opp-1", "ngoId": "ngo-9", "title": "Food distribution",
    "status": "open", "capacity": 3, "startAt": "2026-12-05T08:00:00+00:00",
    "endAt": "2026-12-05T14:00:00+00:00", "contactEmail": "ngo@example.org",
}


class FakeSTS:
    def __init__(self, lifetime=timedelta(hours=1), fail=False):
        self.calls = []
        self.lifetime = lifetime
        self.fail = fail

    def assume_role(self, RoleArn, RoleSessionName, DurationSeconds):
        self.calls.append((RoleArn, RoleSessionName, DurationSeconds))
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "AssumeRole")
        n = len(self.calls)
        return {"Credentials": {
            "AccessKeyId": f"ASIATEST{n}", "SecretAccessKey": "secret", "SessionToken": f"token{n}",
            "Expiration": datetime.now(timezone.utc) + self.lifetime,
        }}


class FakeHTTP:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = json.dumps(OPP).encode() if body is None else body
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, headers))
        if isinstance(self.status, Exception):
            raise self.status
        return self.status, self.body


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("NGO_INTERNAL_API_URL", API + "/")
    monkeypatch.setenv("NGO_CALLER_ROLE_ARN", ROLE)
    monkeypatch.setitem(ngo_client._cached, "credentials", None)
    monkeypatch.setitem(ngo_client._cached, "expires", None)
    sts, http = FakeSTS(), FakeHTTP()
    monkeypatch.setattr(ngo_client, "_sts_client", lambda: sts)
    monkeypatch.setattr(ngo_client, "_send", http)
    return sts, http


def test_happy_path_returns_opportunity(setup):
    sts, http = setup
    assert ngo_client.get_opportunity("opp-1") == OPP
    assert sts.calls == [(ROLE, "vl-volunteer-api", 3600)]
    url, headers = http.requests[0]
    assert url == f"{API}/internal/opportunities/opp-1"


def test_request_is_sigv4_signed_for_execute_api(setup):
    _, http = setup
    ngo_client.get_opportunity("opp-1")
    _, headers = http.requests[0]
    auth = headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=ASIATEST1/")
    assert "/us-east-1/execute-api/aws4_request" in auth
    assert headers["X-Amz-Security-Token"] == "token1"   # temporary STS creds
    assert "X-Amz-Date" in headers


def test_credentials_are_cached(setup):
    sts, _ = setup
    ngo_client.get_opportunity("opp-1")
    ngo_client.get_opportunity("opp-1")
    assert len(sts.calls) == 1


def test_credentials_refresh_before_expiry(setup, monkeypatch):
    sts, http = setup
    sts.lifetime = timedelta(minutes=4)          # inside the 5-minute refresh window
    ngo_client.get_opportunity("opp-1")
    ngo_client.get_opportunity("opp-1")
    assert len(sts.calls) == 2
    assert http.requests[1][1]["X-Amz-Security-Token"] == "token2"


def test_ngo_backend_404_means_not_found(setup):
    _, http = setup
    http.status, http.body = 404, b'{"detail": "Opportunity not found."}'
    assert ngo_client.get_opportunity("opp-1") is None


@pytest.mark.parametrize("body", [
    b'{"message":"Not Found"}',   # API Gateway: no such route
    b'{"detail":"Not Found"}',    # FastAPI default: handler not deployed yet
    b"",
])
def test_other_404s_are_config_errors_not_not_found(setup, body):
    _, http = setup
    http.status, http.body = 404, body
    with pytest.raises(ngo_client.OpportunityServiceUnavailable):
        ngo_client.get_opportunity("opp-1")


@pytest.mark.parametrize("status,body", [
    (403, b'{"message":"Forbidden"}'),     # IAM refused the signature/permission
    (500, b'{"detail":"boom"}'),
    (502, b""),
    (200, b"not json"),
    (200, json.dumps({**OPP, "opportunityId": "other"}).encode()),  # wrong record
    (200, json.dumps({**OPP, "ngoId": ""}).encode()),
    (200, json.dumps({k: v for k, v in OPP.items() if k != "startAt"}).encode()),
    (200, b"[]"),
])
def test_unexpected_responses_are_unavailable(setup, status, body):
    _, http = setup
    http.status, http.body = status, body
    with pytest.raises(ngo_client.OpportunityServiceUnavailable):
        ngo_client.get_opportunity("opp-1")


def test_contact_email_may_be_null(setup):
    _, http = setup
    http.body = json.dumps({**OPP, "contactEmail": None}).encode()
    assert ngo_client.get_opportunity("opp-1")["contactEmail"] is None


def test_network_error_is_unavailable(setup, monkeypatch):
    def boom(url, headers):
        raise ngo_client.OpportunityServiceUnavailable("network error: TimeoutError")
    monkeypatch.setattr(ngo_client, "_send", boom)
    with pytest.raises(ngo_client.OpportunityServiceUnavailable):
        ngo_client.get_opportunity("opp-1")


def test_assume_role_denied_is_unavailable(setup):
    sts, http = setup
    sts.fail = True
    with pytest.raises(ngo_client.OpportunityServiceUnavailable):
        ngo_client.get_opportunity("opp-1")
    assert http.requests == []                   # never calls the API unsigned


@pytest.mark.parametrize("missing", ["NGO_INTERNAL_API_URL", "NGO_CALLER_ROLE_ARN"])
def test_unconfigured_is_unavailable(setup, monkeypatch, missing):
    sts, http = setup
    monkeypatch.delenv(missing)
    with pytest.raises(ngo_client.OpportunityServiceUnavailable):
        ngo_client.get_opportunity("opp-1")
    assert sts.calls == [] and http.requests == []


def test_real_send_maps_connection_failure(monkeypatch):
    # The real _send (not the fake) turns network failures into Unavailable.
    with pytest.raises(ngo_client.OpportunityServiceUnavailable):
        ngo_client._send("https://127.0.0.1:9/nothing-listens-here", {})



# --- volunteer email lookup ---

class FakeCognito:
    def __init__(self, users=None, fail=False):
        self.users = users if users is not None else []
        self.fail = fail
        self.calls = []

    def list_users(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "ListUsers")
        return {"Users": self.users}


def user(email="v@example.org", verified="true"):
    return {"Attributes": [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": verified}]}


@pytest.fixture
def cognito_env(monkeypatch):
    monkeypatch.setenv("NGO_CALLER_ROLE_ARN", ROLE)
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_pool")
    monkeypatch.setattr(ngo_client, "_sts_client", lambda: FakeSTS())
    ngo_client._cached.update(credentials=None, expires=None)
    def install(fake):
        monkeypatch.setattr(ngo_client, "_cognito_client", lambda creds: fake)
        return fake
    return install


def test_email_found(cognito_env):
    fake = cognito_env(FakeCognito([user()]))
    assert ngo_client.get_volunteer_email("sub-1") == "v@example.org"
    assert fake.calls[0]["Filter"] == 'sub = "sub-1"'
    assert fake.calls[0]["UserPoolId"] == "us-east-1_pool"


@pytest.mark.parametrize("users", [[], [user(verified="false")]])
def test_no_usable_email(cognito_env, users):
    cognito_env(FakeCognito(users))
    assert ngo_client.get_volunteer_email("sub-1") is None


def test_lookup_failure_returns_none(cognito_env):
    cognito_env(FakeCognito(fail=True))
    assert ngo_client.get_volunteer_email("sub-1") is None


def test_quote_in_sub_is_refused(cognito_env):
    fake = cognito_env(FakeCognito([user()]))
    assert ngo_client.get_volunteer_email('x" or 1') is None
    assert fake.calls == []
