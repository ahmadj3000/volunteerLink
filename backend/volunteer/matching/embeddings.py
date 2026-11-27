"""Titan Text Embeddings via Amazon Bedrock. Owned by Tarek.

An "embedding" is a list of numbers that describes the meaning of a text.
Texts with similar meaning get vectors that point in similar directions, so
"first aid, ambulance driver" ends up close to "paramedic support at an
emergency shelter" even though they share no words.

Config (ECS task definition environment):
    BEDROCK_MODEL_ID   amazon.titan-embed-text-v2:0   (default)
    EMBED_DIMENSIONS   512                             (256, 512 or 1024)

Every stored vector carries modelId, dimensions, sourceTextHash and embeddedAt.
A vector is re-made only when the text's hash, the model or the size changes,
so saving an unchanged profile costs nothing.

Bedrock problems never surface to users: embed() raises EmbeddingUnavailable
and every caller falls back to "no vector" (unranked results).
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from matching import similarity

DEFAULT_MODEL_ID = "amazon.titan-embed-text-v2:0"
ALLOWED_DIMENSIONS = (256, 512, 1024)
MAX_INPUT_CHARS = 20000  # Titan v2 accepts up to 50k chars / 8k tokens; our texts are far smaller

RECORD_FIELDS = ("embedding", "modelId", "dimensions", "sourceTextHash", "embeddedAt")


class EmbeddingUnavailable(Exception):
    """Bedrock couldn't be reached, refused us, or answered unexpectedly."""


def model_id():
    return os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)


def dimensions():
    value = int(os.environ.get("EMBED_DIMENSIONS", "512"))
    if value not in ALLOWED_DIMENSIONS:
        raise ValueError(f"EMBED_DIMENSIONS must be one of {ALLOWED_DIMENSIONS}")
    return value


# --- the text we embed ---
# Both sides use the same labels ("Skills:", "Location:") so the two kinds of
# text land in comparable parts of the vector space. The volunteer's name is
# left out on purpose: it says nothing about fit and could only add bias.

def _lines(pairs):
    out = []
    for label, value in pairs:
        if isinstance(value, (list, tuple)):
            value = ", ".join(v for v in value if v)
        if value:
            out.append(f"{label}: {str(value).strip()}")
    return "\n".join(out)


def profile_text(profile):
    """From the vl-volunteers fields written by profiles.py."""
    return _lines([
        ("Skills", profile.get("skills")),
        ("Availability", profile.get("availability")),
        ("Location", profile.get("location")),
        ("About", profile.get("bio")),
    ])


def opportunity_text(opportunity):
    """From the vl-opportunities fields (Malek's schema)."""
    return _lines([
        ("Title", opportunity.get("title")),
        ("Skills", opportunity.get("skills")),
        ("Location", opportunity.get("location")),
        ("Description", opportunity.get("description")),
    ])


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_current(record, source_hash):
    """True if the stored vector was made from this exact text with today's model and size."""
    if not record or not record.get("embedding"):
        return False
    try:
        same_size = int(record.get("dimensions", 0)) == dimensions()
    except (TypeError, ValueError):
        return False
    return (
        record.get("sourceTextHash") == source_hash
        and record.get("modelId") == model_id()
        and same_size
    )


def vector_of(record, source_hash):
    """The stored vector if it is current, else None."""
    if not is_current(record, source_hash):
        return None
    vector = similarity.unpack(record["embedding"])
    if vector is None or len(vector) != dimensions():
        return None
    return vector


# --- Bedrock ---

_client = None


def _bedrock():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-runtime",
            region_name="us-east-1",
            # Short timeouts: a slow Bedrock must not hold up a page load.
            config=Config(connect_timeout=2, read_timeout=5,
                          retries={"max_attempts": 2, "mode": "standard"}),
        )
    return _client


def embed(text):
    """Return a normalized vector for the text, or raise EmbeddingUnavailable."""
    if not text:
        raise EmbeddingUnavailable("nothing to embed")
    body = json.dumps({
        "inputText": text[:MAX_INPUT_CHARS],
        "dimensions": dimensions(),
        "normalize": True,
    })
    try:
        response = _bedrock().invoke_model(
            modelId=model_id(), body=body,
            contentType="application/json", accept="application/json",
        )
        vector = json.loads(response["body"].read())["embedding"]
    except (BotoCoreError, ClientError, KeyError, ValueError, TypeError) as error:
        raise EmbeddingUnavailable(type(error).__name__) from error
    if (not isinstance(vector, list) or len(vector) != dimensions()
            or not all(isinstance(x, (int, float)) for x in vector)):
        raise EmbeddingUnavailable("unexpected embedding shape")
    return [float(x) for x in vector]


def new_record(text):
    """Embed the text and return (vector, record-to-store). Raises EmbeddingUnavailable."""
    vector = embed(text)
    record = {
        "embedding": similarity.pack(vector),
        "modelId": model_id(),
        "dimensions": dimensions(),
        "sourceTextHash": text_hash(text),
        "embeddedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return vector, record
