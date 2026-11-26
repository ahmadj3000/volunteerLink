"""vl-email-worker — sends VolunteerLink emails from vl-email-queue (Volunteer account).

The application workflow (Step Functions) never sends email itself. It drops a
small job on the SQS queue, and this Lambda turns the job into an email via SES.

Job body (JSON):
    {"type": "<type>", "applicationId": "<oppId>_<volId>", "taskToken": "..."?}

Types
    ngo_new_applicant  -> NGO contact email   (carries a taskToken)
    ngo_reminder       -> NGO contact email   (carries a NEW taskToken)
    decision           -> volunteer email     (accepted / rejected)
    expired            -> volunteer email
    event_reminder     -> volunteer email     (day before the event)

Task tokens (the two ngo_* types)
    The workflow is paused until someone hands this token back. We park it on
    the application row, only while the row is still "pending". When the NGO
    decides, the status change flows through the table stream to the relay,
    which returns the token. If the NGO already decided before we got here, the
    conditional write fails and we return the token right away ourselves.
    The token is server-side only: the API's response whitelist never includes it.

Failures
    Each message is handled on its own. A failure is reported back to SQS as a
    batch item failure, so only that message is retried; after 3 receives SQS
    moves it to vl-email-dlq.
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TABLE = os.environ.get("APPLICATIONS_TABLE", "vl-applications")
SENDER = os.environ.get("SENDER_EMAIL", "ahmadj3100+vl-noreply@gmail.com")
APP_URL = os.environ.get("APP_URL", "")

NGO_TYPES = {"ngo_new_applicant", "ngo_reminder"}
VOLUNTEER_TYPES = {"decision", "expired", "event_reminder"}

_clients = {}


def client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name, region_name="us-east-1")
    return _clients[name]


class SkipJob(Exception):
    """Nothing to send (e.g. no address on file). Logged, not retried."""


# --- email text ---

def _link():
    return f"\n\nOpen VolunteerLink: {APP_URL}" if APP_URL else ""


def build_email(job_type: str, app: dict) -> tuple[str, str]:
    title = app.get("opportunityTitle") or "your opportunity"
    if job_type == "ngo_new_applicant":
        return (
            f"New applicant for {title}",
            f"A volunteer applied to \"{title}\". Review their profile and "
            f"accept or reject them on VolunteerLink.{_link()}",
        )
    if job_type == "ngo_reminder":
        return (
            f"Reminder: an applicant for {title} is waiting",
            f"A volunteer applied to \"{title}\" and is still waiting for your "
            f"decision. If you don't decide soon, the application will expire.{_link()}",
        )
    if job_type == "decision":
        if app.get("status") == "accepted":
            return (
                f"You're in: {title}",
                f"Good news! You've been accepted for \"{title}\". "
                f"It starts at {app.get('opportunityStartAt')}.{_link()}",
            )
        return (
            f"Update on your application: {title}",
            f"Thanks for applying to \"{title}\". The NGO didn't select you this "
            f"time. Keep an eye on your recommendations for other opportunities.{_link()}",
        )
    if job_type == "expired":
        return (
            f"Your application expired: {title}",
            f"The NGO didn't respond to your application for \"{title}\" in time, "
            f"so it has expired. You can apply to other opportunities.{_link()}",
        )
    if job_type == "event_reminder":
        return (
            f"Tomorrow: {title}",
            f"Reminder: \"{title}\" starts at {app.get('opportunityStartAt')}. "
            f"Thanks for volunteering!{_link()}",
        )
    raise ValueError(f"unknown job type {job_type!r}")


# --- steps ---

def load_application(application_id: str) -> dict:
    item = client("dynamodb").get_item(
        TableName=TABLE,
        Key={"applicationId": {"S": application_id}},
        ConsistentRead=True,
    ).get("Item")
    if not item:
        raise SkipJob(f"application {application_id} not found")
    # flatten {"S": "x"} -> "x" (all fields we read are strings)
    return {k: next(iter(v.values())) for k, v in item.items()}


def park_token(application_id: str, token: str) -> bool:
    """Save the token while the application is pending.

    Returns False if the NGO already decided (the token was handed straight back)."""
    try:
        client("dynamodb").update_item(
            TableName=TABLE,
            Key={"applicationId": {"S": application_id}},
            UpdateExpression="SET taskToken = :t",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":t": {"S": token}, ":pending": {"S": "pending"}},
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    # Decided between the workflow sending this job and us parking the token.
    status = load_application(application_id).get("status")
    logger.info("application %s already %s; returning token directly", application_id, status)
    try:
        client("stepfunctions").send_task_success(
            taskToken=token, output=json.dumps({"status": status}),
        )
    except ClientError as error:
        # Token already used or timed out: the workflow has moved on, nothing to do.
        if error.response["Error"]["Code"] not in ("TaskTimedOut", "InvalidToken", "TaskDoesNotExist"):
            raise
        logger.info("token for %s no longer valid (%s)", application_id, error.response["Error"]["Code"])
    return False


def send(to: str, subject: str, body: str):
    client("sesv2").send_email(
        FromEmailAddress=SENDER,
        Destination={"ToAddresses": [to]},
        Content={"Simple": {
            "Subject": {"Data": subject},
            "Body": {"Text": {"Data": body}},
        }},
    )


def handle_job(job: dict):
    job_type = job.get("type")
    application_id = job.get("applicationId")
    if job_type not in NGO_TYPES | VOLUNTEER_TYPES or not isinstance(application_id, str):
        raise SkipJob(f"bad job {job!r}")

    if job_type in NGO_TYPES:
        token = job.get("taskToken")
        if not token:
            raise SkipJob(f"{job_type} without taskToken for {application_id}")
        if not park_token(application_id, token):
            return  # already decided: no point emailing the NGO

    app = load_application(application_id)
    to = app.get("ngoContactEmail") if job_type in NGO_TYPES else app.get("volunteerEmail")
    if not to:
        raise SkipJob(f"no recipient for {job_type} on {application_id}")

    subject, body = build_email(job_type, app)
    send(to, subject, body)
    logger.info("sent %s for %s", job_type, application_id)


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            handle_job(json.loads(record["body"]))
        except SkipJob as reason:
            logger.warning("skipped message %s: %s", message_id, reason)
        except Exception:
            logger.exception("failed message %s; SQS will retry it", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
