import os
from decimal import Decimal

os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_test")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client")

import pytest
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)

OPPORTUNITY_ID = "11111111-1111-4111-8111-111111111111"
CALLER = (
    "arn:aws:sts::281780646139:"
    "assumed-role/vl-ngo-internal-api-caller/vl-volunteer-api"
)


@pytest.fixture
def database(monkeypatch):
    opportunity = {
        "opportunityId": OPPORTUNITY_ID,
        "ngoId": "ngo-owner",
        "title": "Community support",
        "status": "open",
        "capacity": Decimal("10"),
        "startAt": "2026-12-05T08:00:00+00:00",
        "endAt": "2026-12-05T12:00:00+00:00",
    }
    state = {
        "opportunity": opportunity,
        "organization": {"contactEmail": "ngo@example.com"},
        "reads": [],
    }

    class FakeTable:
        def __init__(self, name):
            self.name = name

        def get_item(self, Key, ConsistentRead=False):
            assert ConsistentRead is True
            state["reads"].append((self.name, Key))
            if self.name == "vl-opportunities":
                assert Key == {"opportunityId": OPPORTUNITY_ID}
                item = state["opportunity"]
            else:
                assert self.name == "vl-ngos"
                assert Key == {"ngoId": "ngo-owner"}
                item = state["organization"]
            return {"Item": item} if item is not None else {}

    class FakeResource:
        def Table(self, name):
            return FakeTable(name)

    monkeypatch.setattr(
        main.boto3, "resource", lambda *args, **kwargs: FakeResource()
    )
    return state


@pytest.mark.parametrize("caller", [
    None,
    "untrusted",
    CALLER + "-different-session",
])
def test_untrusted_caller_denied_before_database(database, caller):
    headers = {} if caller is None else {"x-vl-caller-arn": caller}
    response = client.get(
        f"/internal/opportunities/{OPPORTUNITY_ID}",
        headers=headers,
    )
    assert response.status_code == 403
    assert database["reads"] == []


def test_duplicate_caller_headers_denied(database):
    response = client.get(
        f"/internal/opportunities/{OPPORTUNITY_ID}",
        headers=[
            ("x-vl-caller-arn", CALLER),
            ("x-vl-caller-arn", CALLER),
        ],
    )
    assert response.status_code == 403
    assert database["reads"] == []


@pytest.mark.parametrize("status", ["open", "closed", "draft"])
def test_trusted_caller_gets_opportunity_in_any_status(database, status):
    database["opportunity"]["status"] = status
    response = client.get(
        f"/internal/opportunities/{OPPORTUNITY_ID}",
        headers={"x-vl-caller-arn": CALLER},
    )
    assert response.status_code == 200
    assert response.json() == {
        **database["opportunity"],
        "capacity": 10,
        "contactEmail": "ngo@example.com",
    }
    assert len(database["reads"]) == 2


def test_missing_opportunity_returns_exact_404(database):
    database["opportunity"] = None
    response = client.get(
        f"/internal/opportunities/{OPPORTUNITY_ID}",
        headers={"x-vl-caller-arn": CALLER},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Opportunity not found."}
    assert len(database["reads"]) == 1


@pytest.mark.parametrize("organization", [{}, None])
def test_old_or_missing_organization_returns_null_email(database, organization):
    database["organization"] = organization
    response = client.get(
        f"/internal/opportunities/{OPPORTUNITY_ID}",
        headers={"x-vl-caller-arn": CALLER},
    )
    assert response.status_code == 200
    assert response.json()["contactEmail"] is None


def test_invalid_id_returns_exact_404_without_database_read(database):
    response = client.get(
        "/internal/opportunities/not-a-uuid",
        headers={"x-vl-caller-arn": CALLER},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Opportunity not found."}
    assert database["reads"] == []