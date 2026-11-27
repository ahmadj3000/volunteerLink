"""HTTP routes for matching. Owned by Tarek.

GET /me/recommendations?limit=10        browser, Cognito access token, volunteer group
PUT /internal/opportunities/{id}        NGO backend only (IAM/SigV4, caller-ARN check)

Both are plain `def` routes: FastAPI runs them in a worker thread, so the
blocking boto3/Bedrock calls don't stall other requests.
"""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from applications import require_volunteer
from internal import require_ngo_backend
from matching import service

router = APIRouter()

ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


@router.get("/me/recommendations")
def recommendations(
    limit: int = Query(10, ge=1, le=50),
    user: dict = Depends(require_volunteer),
):
    # Whose recommendations: always the verified token's sub. There is no
    # volunteerId parameter, so nobody can ask for someone else's.
    return service.recommend(user["userId"], limit)


# --- opportunity sync (NGO backend -> Volunteer read copy) ---

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class OpportunitySync(BaseModel):
    """The fields the NGO backend sends after every create/edit/close.
    Same names and limits as vl-opportunities (Malek's schema)."""
    model_config = ConfigDict(extra="forbid")

    ngoId: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    title: Text
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    location: Text
    skills: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]] = Field(max_length=30)
    startAt: AwareDatetime
    endAt: AwareDatetime
    status: Literal["open", "closed", "cancelled"]
    updatedAt: AwareDatetime


@router.put("/internal/opportunities/{opportunity_id}",
            dependencies=[Depends(require_ngo_backend)])
def sync_opportunity(opportunity_id: str, body: OpportunitySync, background: BackgroundTasks):
    if not ID.match(opportunity_id):
        raise HTTPException(400, "Invalid opportunity ID.")
    result, item = service.upsert_opportunity(opportunity_id, body.model_dump())
    if item is not None and "embedding" not in item:
        # Embed after the response is sent: Bedrock being slow or down never
        # fails the sync. Recommendations also fill missing vectors lazily.
        background.add_task(service.embed_opportunity_later, item)
    return {"opportunityId": opportunity_id, "result": result}
