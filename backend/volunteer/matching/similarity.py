"""Pure math for matching: no AWS, no I/O. Owned by Tarek.

Vectors are lists of floats. Titan is asked for *normalized* vectors (length 1),
so cosine similarity equals the plain dot product. We still divide by the
lengths here, so the math stays correct even if a vector isn't normalized.
"""

import math
import struct


def cosine(a, b):
    """Cosine similarity in [-1, 1]. 1 = same direction, 0 = unrelated."""
    if not a or len(a) != len(b):
        raise ValueError("vectors must be non-empty and the same length")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0  # an all-zero vector carries no direction
    return dot / (norm_a * norm_b)


def rank(target, candidates):
    """Order candidates by similarity to the target vector.

    candidates: list of (id, vector or None), in the caller's order.
    Returns [(id, score or None)]:
      - candidates with a vector first, highest score first; equal scores are
        ordered by id so the result is the same every time;
      - candidates without a vector last, in their original order, score None.
        They are never dropped.
    If target is None, nobody can be scored: everyone is returned unscored.
    """
    scored, unscored = [], []
    for cid, vector in candidates:
        if target is not None and vector is not None and len(vector) == len(target):
            scored.append((cid, round(cosine(target, vector), 4)))
        else:
            unscored.append((cid, None))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored + unscored


# DynamoDB storage: 512 float32 values = 2 KB as Binary, versus ~10 KB as a
# list of Numbers. Little-endian float32 so the layout never depends on the CPU.

def pack(vector):
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob):
    raw = getattr(blob, "value", blob)  # boto3 returns a Binary wrapper
    if not raw or len(raw) % 4:
        return None
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))
