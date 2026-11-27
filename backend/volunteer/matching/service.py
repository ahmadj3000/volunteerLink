"""Matching logic: rank applicants, recommend opportunities, sync the index.
Owned by Tarek.

Public function for Ahmad's applicants code (same container, plain call):

    rank_volunteers(opportunity_id, volunteer_ids)
      -> [{"volunteerId": str, "score": float | None}]
         highest score first; equal scores ordered by volunteerId;
         volunteers without a vector last, in the order given, score None.
         Every id given comes back exactly once (duplicates collapsed).
         Never raises: if anything fails, everyone comes back unscored.
"""

from datetime import datetime, timezone

from matching import embeddings, similarity, store
from matching.embeddings import EmbeddingUnavailable

# At most this many Bedrock calls inside one request, so a cold start (many
# missing vectors) can't make a page slow. The rest are filled next time.
MAX_LAZY_EMBEDS = 10


def _log(message):
    print(f"matching: {message}", flush=True)


# --- vectors, made lazily ---

def volunteer_vector(volunteer_id, profile, stored=None, budget=None):
    """The current vector for this profile, embedding it if needed.
    Returns None if there's no usable text or Bedrock is unavailable."""
    text = embeddings.profile_text(profile or {})
    if not text:
        return None
    source_hash = embeddings.text_hash(text)
    vector = embeddings.vector_of(stored, source_hash)
    if vector is not None:
        return vector
    if budget is not None:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
    try:
        vector, record = embeddings.new_record(text)
    except EmbeddingUnavailable as error:
        _log(f"embedding volunteer failed: {error}")
        return None
    try:
        store.put_volunteer_embedding(volunteer_id, record)
    except Exception as error:  # caching failed; the vector is still good for this request
        _log(f"saving volunteer vector failed: {type(error).__name__}")
    return vector


def opportunity_vector(item, budget=None):
    """The current vector for an index row, embedding it if needed."""
    text = embeddings.opportunity_text(item)
    if not text:
        return None
    vector = embeddings.vector_of(item, embeddings.text_hash(text))
    if vector is not None:
        return vector
    if budget is not None:
        if budget[0] <= 0:
            return None
        budget[0] -= 1
    try:
        vector, record = embeddings.new_record(text)
    except EmbeddingUnavailable as error:
        _log(f"embedding opportunity failed: {error}")
        return None
    try:
        store.attach_opportunity_embedding(item["opportunityId"], item["updatedAt"], record)
    except Exception as error:
        _log(f"saving opportunity vector failed: {type(error).__name__}")
    return vector


def refresh_volunteer_embedding(volunteer_id):
    """Optional hook: run after a profile save (e.g. as a FastAPI BackgroundTask)
    so the vector is ready before the next ranking. Never raises."""
    try:
        profile = store.get_profile(volunteer_id)
        if profile:
            volunteer_vector(volunteer_id, profile, store.get_volunteer_embedding(volunteer_id))
    except Exception as error:
        _log(f"refresh failed: {type(error).__name__}")


# --- 1. ranking applicants (called by Ahmad's applicants code) ---

def rank_volunteers(opportunity_id, volunteer_ids):
    ids = list(dict.fromkeys(volunteer_ids))
    unranked = [{"volunteerId": v, "score": None} for v in ids]
    if not ids:
        return []
    try:
        budget = [MAX_LAZY_EMBEDS]
        opportunity = store.get_opportunity(opportunity_id)
        target = opportunity_vector(opportunity, budget) if opportunity else None
        if target is None:
            return unranked
        profiles = store.get_profiles(ids)
        stored = store.get_volunteer_embeddings(ids)
        candidates = [
            (v, volunteer_vector(v, profiles.get(v), stored.get(v), budget) if v in profiles else None)
            for v in ids
        ]
        return [{"volunteerId": v, "score": s} for v, s in similarity.rank(target, candidates)]
    except Exception as error:
        _log(f"rank_volunteers fell back to unranked: {type(error).__name__}")
        return unranked


# --- 2. recommendations for the signed-in volunteer ---

def _parse_time(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else None


def is_recommendable(item, now):
    """Open and not started yet (applying to a started one is refused anyway)."""
    start = _parse_time(item.get("startAt"))
    return item.get("status") == "open" and start is not None and start > now


def recommend(volunteer_id, limit):
    """Returns {"items": [...], "ranked": bool, "reason": str | None}.
    Store errors propagate (the app turns them into 503); Bedrock errors don't."""
    now = datetime.now(timezone.utc)
    opportunities = [o for o in store.open_opportunities() if is_recommendable(o, now)]

    def row(item, score):
        return {
            "opportunityId": item["opportunityId"],
            "title": item.get("title"),
            "location": item.get("location"),
            "startAt": item.get("startAt"),
            "score": score,
        }

    profile = store.get_profile(volunteer_id)
    target = None
    reason = None
    if not profile:
        reason = "profile_missing"
    else:
        target = volunteer_vector(volunteer_id, profile, store.get_volunteer_embedding(volunteer_id))
        if target is None:
            reason = "matching_unavailable"

    if target is None:
        # Fallback: soonest first, unscored. Still useful, never an error.
        opportunities.sort(key=lambda o: (o["startAt"], o["opportunityId"]))
        return {"items": [row(o, None) for o in opportunities[:limit]],
                "ranked": False, "reason": reason}

    budget = [MAX_LAZY_EMBEDS]
    by_id = {o["opportunityId"]: o for o in opportunities}
    candidates = [(o["opportunityId"], opportunity_vector(o, budget)) for o in opportunities]
    ranked = similarity.rank(target, candidates)
    return {"items": [row(by_id[i], s) for i, s in ranked[:limit]],
            "ranked": True, "reason": None}


# --- 3. keeping the opportunity index in sync ---

def normalize_time(moment):
    """Fixed-width UTC string, so plain string comparison in DynamoDB
    conditions orders versions correctly."""
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def upsert_opportunity(opportunity_id, fields):
    """Save one opportunity sent by the NGO backend. Idempotent:
    same or older updatedAt -> nothing changes. Returns (result, item)."""
    item = {
        "opportunityId": opportunity_id,
        "ngoId": fields["ngoId"],
        "title": fields["title"],
        "description": fields["description"],
        "location": fields["location"],
        "skills": list(dict.fromkeys(s.lower() for s in fields["skills"])),
        "startAt": normalize_time(fields["startAt"]),
        "endAt": normalize_time(fields["endAt"]),
        "status": fields["status"],
        "updatedAt": normalize_time(fields["updatedAt"]),
        "syncedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    existing = store.get_opportunity(opportunity_id)
    if existing and existing.get("updatedAt", "") >= item["updatedAt"]:
        return ("unchanged" if existing["updatedAt"] == item["updatedAt"] else "stale"), None

    # Same text as before (e.g. only the status changed)? Keep the vector.
    source_hash = embeddings.text_hash(embeddings.opportunity_text(item))
    if existing and embeddings.is_current(existing, source_hash):
        item.update({k: existing[k] for k in embeddings.RECORD_FIELDS})

    if not store.put_opportunity_if_newer(item):
        return "unchanged", None  # a newer or equal version won a race
    return ("updated" if existing else "created"), item


def embed_opportunity_later(item):
    """Background step after an upsert: never blocks or fails the sync call."""
    try:
        opportunity_vector(item)
    except Exception as error:
        _log(f"background opportunity embed failed: {type(error).__name__}")
