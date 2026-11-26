"""vl-post-confirmation — Cognito post-confirmation trigger (NGO account).

Runs once, right after a user confirms their email at sign-up, and adds them
to the group matching their custom:accountType attribute.

Safety rules:
- Only "volunteer" and "ngo" map to a group. "admin" (or anything else) gets
  NO group, so the user can sign in but every protected route rejects them.
  Admins are only ever added by hand.
- Only acts on sign-up confirmation. The same trigger also fires when a user
  confirms a forgotten-password reset; that must never (re)assign a group.
- Joining "ngo" grants nothing by itself: the NGO backend still requires an
  admin-approved registration before posting.
"""

import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

GROUPS = {"volunteer": "volunteer", "ngo": "ngo"}

_cognito = None


def cognito():
    global _cognito
    if _cognito is None:
        _cognito = boto3.client("cognito-idp")
    return _cognito


def lambda_handler(event, context):
    if event.get("triggerSource") != "PostConfirmation_ConfirmSignUp":
        return event

    attrs = event.get("request", {}).get("userAttributes", {})
    account_type = str(attrs.get("custom:accountType", "")).strip().lower()
    group = GROUPS.get(account_type)

    if group is None:
        # log the user id, not the email
        logger.warning("no group assigned: sub=%s accountType=%r", attrs.get("sub"), account_type)
        return event

    # Adding a user to a group they're already in is a no-op, so retries are safe.
    cognito().admin_add_user_to_group(
        UserPoolId=event["userPoolId"],
        Username=event["userName"],
        GroupName=group,
    )
    logger.info("added sub=%s to group %s", attrs.get("sub"), group)
    return event  # Cognito requires the event back
