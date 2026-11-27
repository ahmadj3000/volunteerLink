"""AI matching for VolunteerLink (Titan embeddings + cosine ranking). Owned by Tarek.

    from matching import rank_volunteers, router

similarity.py  pure math (cosine, ranking, vector packing), no AWS
embeddings.py  the text we embed, hashing, Bedrock Titan calls
store.py       DynamoDB reads/writes (Volunteer account only)
service.py     rank_volunteers, recommendations, opportunity index sync
routes.py      GET /me/recommendations, PUT /internal/opportunities/{id}
"""

from matching.service import rank_volunteers, refresh_volunteer_embedding

__all__ = ["rank_volunteers", "refresh_volunteer_embedding", "router"]


def __getattr__(name):
    # Import the routes lazily so `from matching import rank_volunteers`
    # works without pulling in FastAPI/auth (handy in scripts and tests).
    if name == "router":
        from matching.routes import router
        return router
    raise AttributeError(name)
