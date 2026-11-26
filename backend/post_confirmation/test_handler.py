"""Run with:  cd backend/post_confirmation && python -m pytest"""

import pytest

import handler


class FakeCognito:
    def __init__(self):
        self.calls = []

    def admin_add_user_to_group(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def fake(monkeypatch):
    f = FakeCognito()
    monkeypatch.setattr(handler, "cognito", lambda: f)
    return f


def event(account_type=None, trigger="PostConfirmation_ConfirmSignUp"):
    attrs = {"sub": "abc-123", "email": "v@example.com"}
    if account_type is not None:
        attrs["custom:accountType"] = account_type
    return {
        "triggerSource": trigger,
        "userPoolId": "us-east-1_test",
        "userName": "abc-123",
        "request": {"userAttributes": attrs},
        "response": {},
    }


@pytest.mark.parametrize("value,group", [
    ("volunteer", "volunteer"),
    ("ngo", "ngo"),
    (" NGO ", "ngo"),
])
def test_assigns_allowed_groups(fake, value, group):
    e = event(value)
    assert handler.lambda_handler(e, None) is e
    assert fake.calls == [{"UserPoolId": "us-east-1_test", "Username": "abc-123", "GroupName": group}]


@pytest.mark.parametrize("value", ["admin", "Admin", "superuser", "", None])
def test_never_assigns_admin_or_unknown(fake, value):
    handler.lambda_handler(event(value), None)
    assert fake.calls == []


def test_ignores_forgot_password_confirmation(fake):
    handler.lambda_handler(event("volunteer", trigger="PostConfirmation_ConfirmForgotPassword"), None)
    assert fake.calls == []
