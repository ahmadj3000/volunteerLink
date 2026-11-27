"""Pure math and text tests for matching. No AWS.

Run with:  cd backend/volunteer && python -m pytest tests/test_matching_math.py
"""

import io
import json
import math

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from matching import embeddings, similarity


# --- cosine ---

def test_cosine_identical_is_one():
    assert similarity.cosine([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert similarity.cosine([1, 0], [0, 1]) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one():
    assert similarity.cosine([1, 2], [-1, -2]) == pytest.approx(-1.0)


def test_cosine_ignores_length():
    assert similarity.cosine([1, 1], [5, 5]) == pytest.approx(1.0)


def test_cosine_known_value():
    # (1*2 + 0*1) / (1 * sqrt(5))
    assert similarity.cosine([1, 0], [2, 1]) == pytest.approx(2 / math.sqrt(5))


def test_cosine_equals_dot_for_normalized_vectors():
    a = [0.6, 0.8]
    b = [0.8, 0.6]
    assert similarity.cosine(a, b) == pytest.approx(sum(x * y for x, y in zip(a, b)))


def test_cosine_zero_vector_is_zero():
    assert similarity.cosine([0, 0], [1, 1]) == 0.0


@pytest.mark.parametrize("a,b", [([], []), ([1, 2], [1, 2, 3])])
def test_cosine_rejects_bad_shapes(a, b):
    with pytest.raises(ValueError):
        similarity.cosine(a, b)


# --- rank ---

def test_rank_highest_first():
    out = similarity.rank([1, 0], [("low", [0, 1]), ("high", [1, 0]), ("mid", [1, 1])])
    assert [i for i, _ in out] == ["high", "mid", "low"]
    assert out[0][1] == pytest.approx(1.0)


def test_rank_ties_broken_by_id_every_time():
    candidates = [("c", [1, 0]), ("a", [2, 0]), ("b", [3, 0])]
    assert [i for i, _ in similarity.rank([1, 0], candidates)] == ["a", "b", "c"]
    assert [i for i, _ in similarity.rank([1, 0], list(reversed(candidates)))] == ["a", "b", "c"]


def test_rank_missing_vectors_last_in_given_order_never_dropped():
    out = similarity.rank([1, 0], [("z-none", None), ("hit", [1, 0]), ("a-none", None)])
    assert out == [("hit", 1.0), ("z-none", None), ("a-none", None)]


def test_rank_wrong_size_vector_is_unscored_not_crash():
    assert similarity.rank([1, 0], [("x", [1, 0, 0])]) == [("x", None)]


def test_rank_without_target_returns_everyone_unscored():
    assert similarity.rank(None, [("a", [1]), ("b", None)]) == [("a", None), ("b", None)]


def test_rank_empty():
    assert similarity.rank([1, 0], []) == []


# --- storage packing ---

def test_pack_roundtrip_is_float32_close():
    v = [0.123456789, -0.5, 1.0]
    out = similarity.unpack(similarity.pack(v))
    assert out == pytest.approx(v, abs=1e-6)
    assert len(similarity.pack([0.0] * 512)) == 2048  # 2 KB per 512-d vector


def test_unpack_accepts_boto3_binary_wrapper():
    class Binary:
        def __init__(self, value):
            self.value = value
    assert similarity.unpack(Binary(similarity.pack([1.0, 2.0]))) == [1.0, 2.0]


@pytest.mark.parametrize("blob", [b"", b"abc", None])
def test_unpack_bad_blob_is_none(blob):
    assert similarity.unpack(blob) is None


# --- the text we embed ---

def test_profile_text_uses_profile_fields_but_not_the_name():
    text = embeddings.profile_text({
        "fullName": "Rania Haddad", "skills": ["first aid", "driving"],
        "availability": ["weekends"], "location": "Beirut", "bio": "Nurse.",
    })
    assert text == "Skills: first aid, driving\nAvailability: weekends\nLocation: Beirut\nAbout: Nurse."
    assert "Rania" not in text


def test_profile_text_skips_missing_fields():
    assert embeddings.profile_text({"fullName": "Only Name"}) == ""
    assert embeddings.profile_text({"skills": ["cooking"]}) == "Skills: cooking"


def test_opportunity_text():
    text = embeddings.opportunity_text({
        "title": "Shelter kitchen", "skills": ["cooking"], "location": "Tripoli",
        "description": "Cook hot meals.", "capacity": 5,
    })
    assert text == "Title: Shelter kitchen\nSkills: cooking\nLocation: Tripoli\nDescription: Cook hot meals."


def test_hash_changes_with_text():
    assert embeddings.text_hash("a") == embeddings.text_hash("a")
    assert embeddings.text_hash("a") != embeddings.text_hash("b")


# --- when to re-embed ---

@pytest.fixture
def record(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v2:0")
    monkeypatch.setenv("EMBED_DIMENSIONS", "256")
    return {
        "embedding": similarity.pack([0.0625] * 256), "modelId": "amazon.titan-embed-text-v2:0",
        "dimensions": 256, "sourceTextHash": "h1", "embeddedAt": "2026-11-27T00:00:00+00:00",
    }


def test_current_record_is_reused(record):
    assert embeddings.is_current(record, "h1")
    assert len(embeddings.vector_of(record, "h1")) == 256


def test_text_change_means_re_embed(record):
    assert not embeddings.is_current(record, "h2")
    assert embeddings.vector_of(record, "h2") is None


def test_model_change_means_re_embed(record, monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v3:0")
    assert not embeddings.is_current(record, "h1")


def test_size_change_means_re_embed(record, monkeypatch):
    monkeypatch.setenv("EMBED_DIMENSIONS", "512")
    assert not embeddings.is_current(record, "h1")


def test_no_record_is_not_current():
    assert not embeddings.is_current(None, "h1")
    assert not embeddings.is_current({}, "h1")


def test_bad_dimension_setting_rejected(monkeypatch):
    monkeypatch.setenv("EMBED_DIMENSIONS", "300")
    with pytest.raises(ValueError):
        embeddings.dimensions()


# --- Bedrock call (client faked) ---

class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"body": io.BytesIO(json.dumps(self.response).encode())}


@pytest.fixture
def bedrock(monkeypatch):
    monkeypatch.setenv("EMBED_DIMENSIONS", "256")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)

    def install(**kwargs):
        fake = FakeBedrock(**kwargs)
        monkeypatch.setattr(embeddings, "_client", fake)
        return fake
    return install


def test_embed_asks_for_normalized_vectors_of_the_configured_size(bedrock):
    fake = bedrock(response={"embedding": [0.1] * 256})
    assert len(embeddings.embed("hello")) == 256
    call = fake.calls[0]
    assert call["modelId"] == "amazon.titan-embed-text-v2:0"
    assert json.loads(call["body"]) == {"inputText": "hello", "dimensions": 256, "normalize": True}


def test_new_record_has_metadata(bedrock):
    bedrock(response={"embedding": [0.1] * 256})
    _, rec = embeddings.new_record("hello")
    assert rec["modelId"] == "amazon.titan-embed-text-v2:0"
    assert rec["dimensions"] == 256
    assert rec["sourceTextHash"] == embeddings.text_hash("hello")
    assert rec["embeddedAt"] and len(rec["embedding"]) == 1024


@pytest.mark.parametrize("error", [
    ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "InvokeModel"),
    ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "InvokeModel"),
    ReadTimeoutError(endpoint_url="https://bedrock-runtime"),
])
def test_bedrock_errors_become_embedding_unavailable(bedrock, error):
    bedrock(error=error)
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed("hello")


@pytest.mark.parametrize("response", [{"embedding": [0.1] * 10}, {"nope": 1}, {"embedding": ["x"] * 256}])
def test_unexpected_bedrock_answer_is_unavailable(bedrock, response):
    bedrock(response=response)
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed("hello")


def test_empty_text_is_not_sent(bedrock):
    fake = bedrock(response={"embedding": [0.1] * 256})
    with pytest.raises(embeddings.EmbeddingUnavailable):
        embeddings.embed("")
    assert fake.calls == []
