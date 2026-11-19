"""Run with:  python -m pytest backend/profile"""

import json

import pytest

import handler


class FakeTable:
    def __init__(self):
        self.items = {}
        self.last_update = None

    def get_item(self, Key):
        item = self.items.get(Key["volunteerId"])
        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ReturnValues):
        self.last_update = UpdateExpression
        item = self.items.setdefault(Key["volunteerId"], {"volunteerId": Key["volunteerId"]})
        names, vals = ExpressionAttributeNames, ExpressionAttributeValues
        for k, v in names.items():
            if k.startswith("#f"):
                item[v] = vals[":v" + k[2:]]
            elif k.startswith("#r"):
                item.pop(v, None)
        item["updatedAt"] = vals[":now"]
        item.setdefault("createdAt", vals[":now"])
        return {"Attributes": item}


@pytest.fixture
def fake(monkeypatch):
    t = FakeTable()
    monkeypatch.setattr(handler, "table", lambda: t)
    return t


def event(route, sub="user-123", body=None):
    e = {"routeKey": route, "requestContext": {"authorizer": {"jwt": {"claims": {"sub": sub}}}}}
    if body is not None:
        e["body"] = json.dumps(body)
    return e


def test_validate_normalizes_skills():
    p = handler.validate_profile({"fullName": " Ahmad ", "skills": ["First  Aid", "first aid", "Python"]})
    assert p == {"fullName": "Ahmad", "skills": ["first aid", "python"]}


@pytest.mark.parametrize("body", [
    {},                                       # missing fullName
    {"fullName": "A", "role": "admin"},       # unknown field (no privilege smuggling)
    {"fullName": "A", "availability": ["sometimes"]},
    {"fullName": "A", "skills": "python"},    # not a list
    {"fullName": "x" * 101},
])
def test_validate_rejects(body):
    with pytest.raises(handler.ValidationError):
        handler.validate_profile(body)


def test_get_missing_profile_404(fake):
    assert handler.lambda_handler(event("GET /me/profile"), None)["statusCode"] == 404


def test_put_then_get_roundtrip(fake):
    r = handler.lambda_handler(event("PUT /me/profile", body={"fullName": "Ahmad", "skills": ["Logistics"]}), None)
    assert r["statusCode"] == 200
    r = handler.lambda_handler(event("GET /me/profile"), None)
    body = json.loads(r["body"])
    assert body["fullName"] == "Ahmad" and body["skills"] == ["logistics"]
    assert "createdAt" in body and "updatedAt" in body


def test_put_removes_omitted_optional_fields(fake):
    handler.lambda_handler(event("PUT /me/profile", body={"fullName": "A", "bio": "hi"}), None)
    handler.lambda_handler(event("PUT /me/profile", body={"fullName": "A"}), None)
    assert "bio" not in fake.items["user-123"]


def test_users_cannot_see_each_other(fake):
    handler.lambda_handler(event("PUT /me/profile", sub="alice", body={"fullName": "Alice"}), None)
    assert handler.lambda_handler(event("GET /me/profile", sub="bob"), None)["statusCode"] == 404


def test_bad_json_400(fake):
    e = event("PUT /me/profile")
    e["body"] = "{not json"
    assert handler.lambda_handler(e, None)["statusCode"] == 400


def test_missing_claims_401(fake):
    assert handler.lambda_handler({"routeKey": "GET /me/profile"}, None)["statusCode"] == 401
