"""Run with:  cd backend/volunteer && python -m pytest

Same cases as backend/profile/test_handler.py, now through real HTTP routes.
"""

import os

os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")

import pytest
from fastapi.testclient import TestClient

import auth
import profiles
from main import app


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get(Key["volunteerId"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ReturnValues):
        item = self.items.setdefault(Key["volunteerId"], {"volunteerId": Key["volunteerId"]})
        names, vals = ExpressionAttributeNames, ExpressionAttributeValues
        for k, v in names.items():
            if k.startswith("#f"):
                item[v] = vals[":v" + k[2:]]
            elif k.startswith("#r"):
                item.pop(v, None)
        item["updatedAt"] = vals[":now"]
        item.setdefault("createdAt", vals[":now"])
        return {"Attributes": dict(item)}


@pytest.fixture
def fake(monkeypatch):
    t = FakeTable()
    monkeypatch.setattr(profiles, "table", lambda: t)
    return t


@pytest.fixture
def as_user():
    """Pretend the token was verified for the given sub."""
    def login(sub="user-123", groups=("volunteer",)):
        app.dependency_overrides[auth.current_user] = lambda: {"userId": sub, "groups": list(groups)}
    yield login
    app.dependency_overrides.clear()


client = TestClient(app)


def test_validate_normalizes_skills():
    p = profiles.validate_profile({"fullName": " Ahmad ", "skills": ["First  Aid", "first aid", "Python"]})
    assert p == {"fullName": "Ahmad", "skills": ["first aid", "python"]}


@pytest.mark.parametrize("body", [
    {},                                       # missing fullName
    {"fullName": "A", "role": "admin"},       # unknown field (no privilege smuggling)
    {"fullName": "A", "availability": ["sometimes"]},
    {"fullName": "A", "skills": "python"},    # not a list
    {"fullName": "x" * 101},
    ["not", "an", "object"],
])
def test_put_rejects_bad_bodies(fake, as_user, body):
    as_user()
    assert client.put("/me/profile", json=body).status_code == 400


def test_health_is_public():
    assert client.get("/health").json() == {"status": "ok", "service": "vl-volunteer-api"}


def test_no_token_401(fake):
    assert client.get("/me/profile").status_code == 401
    assert client.put("/me/profile", json={"fullName": "A"}).status_code == 401


def test_garbage_token_401(fake):
    r = client.get("/me/profile", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_get_missing_profile_404(fake, as_user):
    as_user()
    assert client.get("/me/profile").status_code == 404


def test_put_then_get_roundtrip(fake, as_user):
    as_user()
    r = client.put("/me/profile", json={"fullName": "Ahmad", "skills": ["Logistics"]})
    assert r.status_code == 200
    body = client.get("/me/profile").json()
    assert body["fullName"] == "Ahmad" and body["skills"] == ["logistics"]
    assert "createdAt" in body and "updatedAt" in body


def test_put_removes_omitted_optional_fields(fake, as_user):
    as_user()
    client.put("/me/profile", json={"fullName": "A", "bio": "hi"})
    client.put("/me/profile", json={"fullName": "A"})
    assert "bio" not in fake.items["user-123"]


def test_users_cannot_see_each_other(fake, as_user):
    as_user("alice")
    client.put("/me/profile", json={"fullName": "Alice"})
    as_user("bob")
    assert client.get("/me/profile").status_code == 404


def test_bad_json_400(fake, as_user):
    as_user()
    r = client.put("/me/profile", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
