"""Calls the NGO account's internal API. Owned by Ahmad.

The Volunteer account never reads NGO tables directly. To learn about an
opportunity it asks the NGO backend:

    GET /internal/opportunities/{opportunityId}
    -> 200 {opportunityId, ngoId, title, status, capacity, startAt, endAt, contactEmail}
    -> 404 {"detail": "Opportunity not found."}

STEP 3b: interface only. The real call (assume vl-ngo-internal-api-caller
via STS, then SigV4-sign the request) is added in step 3c. Until then this
raises OpportunityServiceUnavailable, so /applications returns 503 instead
of guessing. Tests replace get_opportunity with a fake.
"""


class OpportunityServiceUnavailable(Exception):
    """The NGO API couldn't be reached or answered unexpectedly."""


def get_opportunity(opportunity_id: str) -> dict | None:
    """Return the opportunity dict, or None if the NGO API says 404."""
    raise OpportunityServiceUnavailable("NGO internal API client not configured yet (step 3c).")
