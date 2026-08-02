"""Local, provider-free fallbacks for endpoints the active MCP model does not
serve natively: embeddings (deterministic hashing vectors) and moderations (a
transparent keyword heuristic). Both are clearly non-semantic fallbacks.

Ported from hyperagent-openai-gateway fallbacks.py.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any, Union

# ------------------------------- embeddings -------------------------------- #
def hashing_embedding(text: str, dim: int = 1536) -> list[float]:
    """Deterministic bag-of-words hashing embedding, L2-normalized.

    Not semantically meaningful like a trained model, but stable and useful for
    caching/dedup/roundtrip tests. Same text -> same vector.
    """
    vec = [0.0] * dim
    for tok in re.findall(r"\w+", (text or "").lower()):
        h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0 if ((h >> 8) & 1) else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def normalize_inputs(value: Union[str, list]) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v if isinstance(v, str) else str(v) for v in value]
    return [str(value)]


def embeddings_response(model: str, inputs: list[str], dim: int) -> dict[str, Any]:
    data = [{"object": "embedding", "index": i, "embedding": hashing_embedding(t, dim)}
            for i, t in enumerate(inputs)]
    total = sum(len(re.findall(r"\w+", t or "")) for t in inputs)
    return {
        "object": "list",
        "data": data,
        "model": model,
        "usage": {"prompt_tokens": total, "total_tokens": total},
    }


# ------------------------------- moderation -------------------------------- #
_MOD_KEYWORDS: dict[str, list[str]] = {
    "hate": ["hate", "racist", "bigot"],
    "harassment": ["harass", "bully", "threaten you"],
    "self-harm": ["suicide", "self-harm", "kill myself", "cut myself"],
    "sexual": ["explicit sexual", "porn", "child sexual"],
    "sexual/minors": ["child sexual", "minor sexual"],
    "violence": ["kill", "murder", "bomb", "shoot", "attack"],
    "violence/graphic": ["gore", "dismember"],
    "illicit": ["how to make a bomb", "buy drugs", "counterfeit"],
}
_ALL_CATEGORIES = list(_MOD_KEYWORDS.keys())


def moderate_text(text: str) -> dict[str, Any]:
    low = (text or "").lower()
    categories = {c: False for c in _ALL_CATEGORIES}
    scores = {c: 0.0 for c in _ALL_CATEGORIES}
    for cat, kws in _MOD_KEYWORDS.items():
        for kw in kws:
            if kw in low:
                categories[cat] = True
                scores[cat] = max(scores[cat], 0.9)
    flagged = any(categories.values())
    return {"flagged": flagged, "categories": categories, "category_scores": scores}


def moderations_response(model: str, inputs: list[str]) -> dict[str, Any]:
    return {
        "id": "modr-" + hashlib.sha1(("".join(inputs)).encode()).hexdigest()[:20],
        "model": model or "ide-bridge-moderation-heuristic",
        "results": [moderate_text(t) for t in inputs],
    }
