"""Matching service + routes. DynamoDB and Bedrock are faked in memory.

Run with:  cd backend/volunteer && python -m pytest tests/test_matching.py
"""

import os

os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")

import copy
import hashlib
import math
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import auth
from main import app
from matching import embeddings, service, store

client = TestClient(app)
CALLER = "arn:aws:sts::170813308606:assumed-role/vl-volunteer-internal-api-caller/vl-ngo-api"
INTERNAL = {"x-vl-caller-arn": CALLER}
DIMS = 256


def future(days=7):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def past(days=1):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# --- fakes ---

def word_vector(text):
    """Deterministic stand-in for Titan: bag of words hashed into 256 slots,
    normalized. Texts sharing words point the same way."""
    v = [0.0] * DIMS
    for word in text.lower().replace(",", " ").replace(":", " ").split():
        v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMS] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class FakeStore:
    def __init__(self):
        self.profiles, self.vectors, self.index = {}, {}, {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "Op")

    def get_profile(self, vid):
        self._check()
        return copy.deepcopy(self.profiles.get(vid))

    def get_profiles(self, ids):
        self._check()
        return {i: copy.deepcopy(self.profiles[i]) for i in ids if i in self.profiles}

    def get_volunteer_embedding(self, vid):
        return copy.deepcopy(self.vectors.get(vid))

    def get_volunteer_embeddings(self, ids):
        return {i: copy.deepcopy(self.vectors[i]) for i in ids if i in self.vectors}

    def put_volunteer_embedding(self, vid, record):
        self.vectors[vid] = {"volunteerId": vid, **record}

    def get_opportunity(self, oid):
        self._check()
        return copy.deepcopy(self.index.get(oid))

    def put_opportunity_if_newer(self, item):
        old = self.index.get(item["opportunityId"])
        if old and not old["updatedAt"] < item["updatedAt"]:
            return False
        self.index[item["opportunityId"]] = copy.deepcopy(item)
        return True

    def attach_opportunity_embedding(self, oid, updated_at, record):
        row = self.index.get(oid)
        if not row or row["updatedAt"] != updated_at:
            return False
        row.update(record)
        return True

    def open_opportunities(self):
        self._check()
        return [copy.deepcopy(o) for o in self.index.values() if o.get("status") == "open"]


@pytest.fixture
def db(monkeypatch):
    fake = FakeStore()
    for name in ("get_profile", "get_profiles", "get_volunteer_embedding", "get_volunteer_embeddings",
                 "put_volunteer_embedding", "get_opportunity", "put_opportunity_if_newer",
                 "attach_opportunity_embedding", "open_opportunities"):
        monkeypatch.setattr(store, name, getattr(fake, name))
    return fake


class FakeTitan:
    def __init__(self):
        self.calls = 0
        self.down = False

    def embed(self, text):
        self.calls += 1
        if self.down:
            raise embeddings.EmbeddingUnavailable("ThrottlingException")
        return word_vector(text)


@pytest.fixture
def titan(monkeypatch):
    monkeypatch.setenv("EMBED_DIMENSIONS", str(DIMS))
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    fake = FakeTitan()
    monkeypatch.setattr(embeddings, "embed", fake.embed)
    return fake


@pytest.fixture
def as_user():
    def login(sub="vol-1", groups=("volunteer",)):
        app.dependency_overrides[auth.current_user] = lambda: {"userId": sub, "groups": list(groups)}
    yield login
    app.dependency_overrides.clear()


@pytest.fixture
def ngo_backend(monkeypatch):
    monkeypatch.setenv("NGO_BACKEND_CALLER_ARN", CALLER)


def add_opportunity(db, oid, title, skills, status="open", start=None, description=None):
    db.index[oid] = {
        "opportunityId": oid, "ngoId": "ngo-1", "title": title,
        "description": description or f"Help with {title.lower()}.",
        "location": "Beirut", "skills": skills, "startAt": start or future(),
        "endAt": future(30), "status": status, "updatedAt": "2026-11-20T10:00:00.000000+00:00",
    }


def sync_body(**overrides):
    body = {
        "ngoId": "ngo-1", "title": "Shelter kitchen", "description": "Cook hot meals for families.",
        "location": "Tripoli", "skills": ["Cooking", "cooking"], "startAt": future(),
        "endAt": future(8), "status": "open", "updatedAt": "2026-11-27T10:00:00+00:00",
    }
    body.update(overrides)
    return body


# --- rank_volunteers (Ahmad's applicants code calls this) ---

@pytest.fixture
def applicants(db, titan):
    add_opportunity(db, "opp-1", "First aid tent", ["first aid", "nursing"])
    db.profiles["medic"] = {"volunteerId": "medic", "skills": ["first aid", "nursing"]}
    db.profiles["driver"] = {"volunteerId": "driver", "skills": ["driving"]}
    db.profiles["mixed"] = {"volunteerId": "mixed", "skills": ["first aid", "driving"]}
    return db


def test_rank_orders_best_match_first(applicants):
    out = service.rank_volunteers("opp-1", ["driver", "mixed", "medic"])
    assert [r["volunteerId"] for r in out] == ["medic", "mixed", "driver"]
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True)
    assert all(isinstance(s, float) for s in scores)


def test_rank_volunteer_without_profile_goes_last_with_null_score(applicants):
    out = service.rank_volunteers("opp-1", ["ghost", "driver", "medic"])
    assert out[-1] == {"volunteerId": "ghost", "score": None}
    assert len(out) == 3  # never dropped


def test_rank_profile_with_no_text_is_unscored(applicants):
    applicants.profiles["name-only"] = {"volunteerId": "name-only", "fullName": "N"}
    out = service.rank_volunteers("opp-1", ["name-only", "medic"])
    assert out == [{"volunteerId": "medic", "score": out[0]["score"]},
                   {"volunteerId": "name-only", "score": None}]


def test_rank_ties_are_ordered_by_id(applicants):
    applicants.profiles["b-twin"] = {"volunteerId": "b-twin", "skills": ["driving"]}
    applicants.profiles["a-twin"] = {"volunteerId": "a-twin", "skills": ["driving"]}
    out = service.rank_volunteers("opp-1", ["b-twin", "a-twin"])
    assert [r["volunteerId"] for r in out] == ["a-twin", "b-twin"]
    assert out[0]["score"] == out[1]["score"]


def test_rank_collapses_duplicate_ids(applicants):
    out = service.rank_volunteers("opp-1", ["medic", "medic", "driver"])
    assert [r["volunteerId"] for r in out] == ["medic", "driver"]


def test_rank_empty_list():
    assert service.rank_volunteers("opp-1", []) == []


def test_rank_unknown_opportunity_returns_everyone_unscored(applicants):
    out = service.rank_volunteers("nope", ["medic", "driver"])
    assert out == [{"volunteerId": "medic", "score": None}, {"volunteerId": "driver", "score": None}]


def test_rank_bedrock_down_falls_back_to_unranked_not_error(applicants, titan):
    titan.down = True
    out = service.rank_volunteers("opp-1", ["driver", "medic"])
    assert out == [{"volunteerId": "driver", "score": None}, {"volunteerId": "medic", "score": None}]


def test_rank_dynamodb_down_falls_back_to_unranked_not_error(applicants):
    applicants.fail = True
    out = service.rank_volunteers("opp-1", ["driver", "medic"])
    assert [r["score"] for r in out] == [None, None]


def test_vectors_are_cached_and_only_remade_when_text_changes(applicants, titan):
    service.rank_volunteers("opp-1", ["medic", "driver"])
    first = titan.calls
    assert first == 3  # 1 opportunity + 2 volunteers
    service.rank_volunteers("opp-1", ["medic", "driver"])
    assert titan.calls == first  # nothing changed -> no Bedrock calls
    applicants.profiles["driver"]["skills"] = ["driving", "first aid"]
    service.rank_volunteers("opp-1", ["medic", "driver"])
    assert titan.calls == first + 1  # only the changed profile


def test_lazy_embedding_is_capped_per_request(applicants, titan, monkeypatch):
    monkeypatch.setattr(service, "MAX_LAZY_EMBEDS", 2)
    out = service.rank_volunteers("opp-1", ["medic", "driver", "mixed"])
    assert titan.calls == 2  # opportunity + first volunteer
    assert [r["score"] is None for r in out] == [False, True, True]


def test_refresh_hook_never_raises(applicants, titan):
    titan.down = True
    service.refresh_volunteer_embedding("medic")  # no exception
    titan.down = False
    service.refresh_volunteer_embedding("medic")
    assert "medic" in applicants.vectors


# --- GET /me/recommendations ---

@pytest.fixture
def catalog(db, titan):
    add_opportunity(db, "opp-aid", "First aid tent", ["first aid", "nursing"])
    add_opportunity(db, "opp-food", "Shelter kitchen", ["cooking"])
    add_opportunity(db, "opp-drive", "Supply runs", ["driving"])
    add_opportunity(db, "opp-closed", "First aid closed", ["first aid", "nursing"], status="closed")
    add_opportunity(db, "opp-cancel", "First aid cancelled", ["first aid", "nursing"], status="cancelled")
    add_opportunity(db, "opp-started", "First aid started", ["first aid", "nursing"], start=past())
    db.profiles["vol-1"] = {"volunteerId": "vol-1", "fullName": "A", "skills": ["first aid", "nursing"]}
    db.profiles["vol-2"] = {"volunteerId": "vol-2", "fullName": "B", "skills": ["cooking"]}
    return db


def test_recommendations_need_a_token(catalog):
    assert client.get("/me/recommendations").status_code == 401


def test_recommendations_are_for_volunteers_only(catalog, as_user):
    as_user("ngo-1", groups=("ngo",))
    assert client.get("/me/recommendations").status_code == 403


def test_recommendations_ranked_and_shaped(catalog, as_user):
    as_user("vol-1")
    body = client.get("/me/recommendations").json()
    assert body["ranked"] is True and body["reason"] is None
    assert body["items"][0]["opportunityId"] == "opp-aid"
    assert set(body["items"][0]) == {"opportunityId", "title", "location", "startAt", "score"}
    scores = [i["score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)


def test_closed_cancelled_and_started_opportunities_are_excluded(catalog, as_user):
    as_user("vol-1")
    ids = {i["opportunityId"] for i in client.get("/me/recommendations").json()["items"]}
    assert ids == {"opp-aid", "opp-food", "opp-drive"}


def test_limit_is_applied_and_bounded(catalog, as_user):
    as_user("vol-1")
    assert len(client.get("/me/recommendations?limit=1").json()["items"]) == 1
    assert client.get("/me/recommendations?limit=0").status_code == 422
    assert client.get("/me/recommendations?limit=51").status_code == 422


def test_volunteer_cannot_get_another_volunteers_recommendations(catalog, as_user):
    as_user("vol-2")
    # A volunteerId in the query is ignored: identity comes only from the token.
    body = client.get("/me/recommendations?volunteerId=vol-1").json()
    assert body["items"][0]["opportunityId"] == "opp-food"  # vol-2's best match, not vol-1's
    assert set(catalog.vectors) == {"vol-2"}  # only the caller's profile was read and embedded


def test_no_profile_gives_unranked_list_not_error(catalog, as_user):
    as_user("new-user")
    body = client.get("/me/recommendations").json()
    assert body["ranked"] is False and body["reason"] == "profile_missing"
    assert all(i["score"] is None for i in body["items"])
    assert len(body["items"]) == 3


def test_bedrock_down_gives_unranked_list_not_error(catalog, as_user, titan):
    titan.down = True
    as_user("vol-1")
    r = client.get("/me/recommendations")
    assert r.status_code == 200
    body = r.json()
    assert body["ranked"] is False and body["reason"] == "matching_unavailable"
    starts = [i["startAt"] for i in body["items"]]
    assert starts == sorted(starts)  # soonest first


def test_closed_rows_are_skipped_even_if_the_store_returns_them(catalog, as_user, monkeypatch):
    # The DynamoDB scan already filters status=open; the service checks again.
    monkeypatch.setattr(store, "open_opportunities", lambda: [copy.deepcopy(o) for o in catalog.index.values()])
    as_user("vol-1")
    ids = {i["opportunityId"] for i in client.get("/me/recommendations").json()["items"]}
    assert ids == {"opp-aid", "opp-food", "opp-drive"}


def test_closing_an_opportunity_via_sync_removes_it_from_recommendations(catalog, as_user, ngo_backend):
    client.put("/internal/opportunities/opp-new",
               json=sync_body(title="First aid nursing", skills=["first aid", "nursing"]), headers=INTERNAL)
    as_user("vol-1")
    ids = [i["opportunityId"] for i in client.get("/me/recommendations").json()["items"]]
    assert "opp-new" in ids
    client.put("/internal/opportunities/opp-new",
               json=sync_body(title="First aid nursing", skills=["first aid", "nursing"],
                              status="closed", updatedAt="2026-11-28T10:00:00+00:00"),
               headers=INTERNAL)
    ids = [i["opportunityId"] for i in client.get("/me/recommendations").json()["items"]]
    assert "opp-new" not in ids


def test_database_down_is_503(catalog, as_user):
    catalog.fail = True
    as_user("vol-1")
    assert client.get("/me/recommendations").status_code == 503


# --- PUT /internal/opportunities/{id} (opportunity sync) ---

def test_sync_refuses_callers_other_than_the_ngo_backend(db, titan, ngo_backend):
    assert client.put("/internal/opportunities/opp-9", json=sync_body()).status_code == 403
    r = client.put("/internal/opportunities/opp-9", json=sync_body(),
                   headers={"x-vl-caller-arn": "arn:aws:sts::1:assumed-role/someone/else"})
    assert r.status_code == 403
    assert db.index == {}


@pytest.mark.parametrize("caller,expected", [
    (CALLER, 200),
    # same role, different session name
    ("arn:aws:sts::170813308606:assumed-role/vl-volunteer-internal-api-caller/someone", 403),
    # different role, same session name
    ("arn:aws:sts::170813308606:assumed-role/AWSReservedSSO_AdministratorAccess_x/vl-ngo-api", 403),
    # right role path in another account
    ("arn:aws:sts::281780646139:assumed-role/vl-volunteer-internal-api-caller/vl-ngo-api", 403),
    (CALLER + " ", 403),
    ("", 403),
])
def test_sync_accepts_only_the_exact_ngo_backend_session(db, titan, ngo_backend, caller, expected):
    r = client.put("/internal/opportunities/opp-9", json=sync_body(),
                   headers={"x-vl-caller-arn": caller})
    assert r.status_code == expected
    assert ("opp-9" in db.index) == (expected == 200)


def test_sync_refused_when_expected_caller_is_not_configured(db, titan, monkeypatch):
    monkeypatch.delenv("NGO_BACKEND_CALLER_ARN", raising=False)
    assert client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL).status_code == 403
    assert db.index == {}


def test_every_internal_route_refuses_a_missing_or_wrong_caller(db, titan, ngo_backend):
    import re as regex
    routes = [(method.upper(), path)
              for path, ops in app.openapi()["paths"].items() if path.startswith("/internal/")
              for method in ops]
    assert ("PUT", "/internal/opportunities/{opportunity_id}") in routes
    for method, path in routes:
        url = regex.sub(r"\{[^}]+\}", "opp-1", path)
        for headers in ({}, {"x-vl-caller-arn": "arn:aws:sts::1:assumed-role/x/vl-ngo-api"}):
            r = client.request(method, url, json={}, headers=headers)
            assert r.status_code == 403, (method, path, headers)
    assert db.index == {}


def test_sync_creates_normalizes_and_embeds_after_responding(db, titan, ngo_backend):
    r = client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    assert r.json() == {"opportunityId": "opp-9", "result": "created"}
    row = db.index["opp-9"]
    assert row["skills"] == ["cooking"]
    assert row["updatedAt"] == "2026-11-27T10:00:00.000000+00:00"
    assert row["modelId"] == "amazon.titan-embed-text-v2:0" and row["dimensions"] == DIMS
    assert titan.calls == 1


def test_sync_same_version_twice_does_nothing(db, titan, ngo_backend):
    client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    before = copy.deepcopy(db.index["opp-9"])
    r = client.put("/internal/opportunities/opp-9", json=sync_body(title="Changed"), headers=INTERNAL)
    assert r.json()["result"] == "unchanged"
    assert db.index["opp-9"] == before


def test_sync_older_version_never_overwrites_newer(db, titan, ngo_backend):
    client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    r = client.put("/internal/opportunities/opp-9",
                   json=sync_body(title="Old title", updatedAt="2026-11-26T10:00:00+00:00"),
                   headers=INTERNAL)
    assert r.json()["result"] == "stale"
    assert db.index["opp-9"]["title"] == "Shelter kitchen"


def test_sync_equal_time_in_another_timezone_is_the_same_version(db, titan, ngo_backend):
    client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    r = client.put("/internal/opportunities/opp-9",
                   json=sync_body(updatedAt="2026-11-27T12:00:00+02:00"), headers=INTERNAL)
    assert r.json()["result"] == "unchanged"


def test_sync_status_change_keeps_vector_without_bedrock(db, titan, ngo_backend):
    client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    r = client.put("/internal/opportunities/opp-9",
                   json=sync_body(status="closed", updatedAt="2026-11-28T10:00:00+00:00"),
                   headers=INTERNAL)
    assert r.json()["result"] == "updated"
    assert db.index["opp-9"]["status"] == "closed" and db.index["opp-9"]["embedding"]
    assert titan.calls == 1


def test_sync_text_change_re_embeds(db, titan, ngo_backend):
    client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    client.put("/internal/opportunities/opp-9",
               json=sync_body(description="Now also first aid.", updatedAt="2026-11-28T10:00:00+00:00"),
               headers=INTERNAL)
    assert titan.calls == 2


def test_sync_succeeds_when_bedrock_is_down(db, titan, ngo_backend):
    titan.down = True
    r = client.put("/internal/opportunities/opp-9", json=sync_body(), headers=INTERNAL)
    assert r.status_code == 200 and r.json()["result"] == "created"
    assert "embedding" not in db.index["opp-9"]  # filled lazily later


@pytest.mark.parametrize("body", [
    sync_body(status="deleted"),
    sync_body(updatedAt="2026-11-27T10:00:00"),          # no timezone
    sync_body(embedding=[0.1, 0.2]),                     # clients can't set vectors
    {k: v for k, v in sync_body().items() if k != "ngoId"},
])
def test_sync_rejects_bad_bodies(db, titan, ngo_backend, body):
    assert client.put("/internal/opportunities/opp-9", json=body, headers=INTERNAL).status_code == 422


def test_sync_rejects_bad_id(db, titan, ngo_backend):
    assert client.put("/internal/opportunities/bad_id", json=sync_body(), headers=INTERNAL).status_code == 400


# --- store.py conditional writes (DynamoDB table faked) ---

class ConditionalTable:
    name = "vl-opportunity-index"

    def __init__(self, fail_condition):
        self.fail_condition, self.calls = fail_condition, []

    def _maybe_fail(self):
        if self.fail_condition:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "Op")

    def put_item(self, **kwargs):
        self.calls.append(kwargs)
        self._maybe_fail()

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        self._maybe_fail()


@pytest.mark.parametrize("fails,expected", [(False, True), (True, False)])
def test_store_upsert_condition(monkeypatch, fails, expected):
    table = ConditionalTable(fails)
    monkeypatch.setattr(store, "opportunity_index", lambda: table)
    assert store.put_opportunity_if_newer({"opportunityId": "o", "updatedAt": "t2"}) is expected
    call = table.calls[0]
    assert call["ConditionExpression"] == "attribute_not_exists(opportunityId) OR updatedAt < :u"
    assert call["ExpressionAttributeValues"] == {":u": "t2"}


def test_store_attach_only_to_the_version_it_was_made_from(monkeypatch):
    table = ConditionalTable(True)
    monkeypatch.setattr(store, "opportunity_index", lambda: table)
    record = {"embedding": b"x" * 4, "modelId": "m", "dimensions": 1,
              "sourceTextHash": "h", "embeddedAt": "now"}
    assert store.attach_opportunity_embedding("o", "t1", record) is False
    assert table.calls[0]["ConditionExpression"] == "updatedAt = :u"
