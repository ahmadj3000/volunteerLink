import json

import pytest
from botocore.exceptions import ClientError

import handler as h


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class FakeDynamo:
    def __init__(self, item, decided=False):
        self.item = item
        self.decided = decided
        self.updates = []

    def get_item(self, **kw):
        return {"Item": {k: {"S": v} for k, v in self.item.items()}} if self.item else {}

    def update_item(self, **kw):
        if self.decided:
            raise client_error("ConditionalCheckFailedException")
        self.updates.append(kw)


class FakeSes:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_email(self, **kw):
        if self.fail:
            raise client_error("Throttling")
        self.sent.append(kw)


class FakeSfn:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send_task_success(self, **kw):
        if self.error:
            raise client_error(self.error)
        self.calls.append(kw)


APP = {
    "applicationId": "opp-1_vol-1", "status": "pending", "opportunityTitle": "Food Drive",
    "opportunityStartAt": "2026-12-01T09:00:00+00:00",
    "ngoContactEmail": "ngo@example.com", "volunteerEmail": "vol@example.com",
}


@pytest.fixture
def fakes(monkeypatch):
    def install(item=APP, decided=False, ses_fail=False, sfn_error=None):
        f = {"dynamodb": FakeDynamo(dict(item) if item else None, decided),
             "sesv2": FakeSes(ses_fail), "stepfunctions": FakeSfn(sfn_error)}
        monkeypatch.setattr(h, "client", lambda name: f[name])
        return f
    return install


def run(*jobs):
    records = [{"messageId": f"m{i}", "body": json.dumps(j)} for i, j in enumerate(jobs)]
    return h.handler({"Records": records}, None)


def test_new_applicant_parks_token_and_emails_ngo(fakes):
    f = fakes()
    out = run({"type": "ngo_new_applicant", "applicationId": "opp-1_vol-1", "taskToken": "tok"})
    assert out == {"batchItemFailures": []}
    assert f["dynamodb"].updates[0]["ExpressionAttributeValues"][":t"] == {"S": "tok"}
    assert f["sesv2"].sent[0]["Destination"] == {"ToAddresses": ["ngo@example.com"]}
    assert f["stepfunctions"].calls == []


def test_already_decided_returns_token_and_skips_email(fakes):
    f = fakes(item={**APP, "status": "accepted"}, decided=True)
    run({"type": "ngo_reminder", "applicationId": "opp-1_vol-1", "taskToken": "tok"})
    assert f["stepfunctions"].calls == [{"taskToken": "tok", "output": json.dumps({"status": "accepted"})}]
    assert f["sesv2"].sent == []


def test_dead_token_is_ignored(fakes):
    f = fakes(item={**APP, "status": "accepted"}, decided=True, sfn_error="TaskTimedOut")
    assert run({"type": "ngo_reminder", "applicationId": "opp-1_vol-1", "taskToken": "t"}) == {"batchItemFailures": []}
    assert f["sesv2"].sent == []


def test_decision_emails_volunteer(fakes):
    f = fakes(item={**APP, "status": "rejected"})
    run({"type": "decision", "applicationId": "opp-1_vol-1"})
    sent = f["sesv2"].sent[0]
    assert sent["Destination"] == {"ToAddresses": ["vol@example.com"]}
    assert "didn't select you" in sent["Content"]["Simple"]["Body"]["Text"]["Data"]


def test_missing_recipient_is_skipped_not_retried(fakes):
    f = fakes(item={k: v for k, v in APP.items() if k != "volunteerEmail"})
    assert run({"type": "expired", "applicationId": "opp-1_vol-1"}) == {"batchItemFailures": []}
    assert f["sesv2"].sent == []


def test_bad_jobs_are_skipped(fakes):
    fakes()
    assert run({"type": "nope", "applicationId": "x"}, {"type": "ngo_new_applicant", "applicationId": "x"}) \
        == {"batchItemFailures": []}


def test_ses_failure_retries_only_that_message(fakes):
    fakes(ses_fail=True)
    out = run({"type": "decision", "applicationId": "opp-1_vol-1"})
    assert out == {"batchItemFailures": [{"itemIdentifier": "m0"}]}


def test_invalid_json_goes_to_retry(fakes):
    fakes()
    out = h.handler({"Records": [{"messageId": "m0", "body": "not json"}]}, None)
    assert out == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
