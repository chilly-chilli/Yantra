"""Budgeted OpenAI-compatible embeddings; vectors are rebuildable derived data."""
from __future__ import annotations

import math
import json
import httpx

from ..configuration import environment_values
from .schemas import digest


INDEX_VERSION = 1


def vector_text(item):
    # Administrative flags/priority changes do not regenerate semantic vectors.
    return json.dumps({k:item.get(k) for k in ("kind","content","period","topics","assumptions","invalidation_conditions","judgment")},ensure_ascii=False,sort_keys=True)


class Embedder:
    def __init__(self, profile, budget):
        self.profile, self.budget = profile, budget
        self.fingerprint = digest({k: profile.get(k) for k in ("provider", "model_id", "base_url", "dimensions")})

    def embed(self, texts):
        profile, budget = self.profile, self.budget
        budget.check()
        if not profile.get("pricing"):
            raise ValueError("embedding pricing unknown; degrade without treating cost as zero")
        if budget.state["budget"].get("model_calls", 0) >= budget.state["limits"]["max_models"]:
            raise ValueError("embedding request budget exhausted")
        credentials = environment_values({"api_key": profile["api_key_env"]})
        receipt = budget.reserve("\n".join(texts), "embedding", 0, pricing=profile["pricing"])
        receipt.update(method="embedding", model=profile["model_id"], fingerprint=self.fingerprint)
        budget.state["budget"]["model_calls"] = budget.state["budget"].get("model_calls", 0) + 1
        budget.save()
        payload = {"model": profile["model_id"].removeprefix("openai/"), "input": texts, "encoding_format": "float"}
        if profile.get("dimensions"):
            payload["dimensions"] = profile["dimensions"]
        usage = None
        try:
            with httpx.Client(timeout=max(.01, min(45, budget.remaining()))) as client:
                response = client.post(profile["base_url"].rstrip("/") + "/embeddings", json=payload,
                                       headers={"Authorization": "Bearer " + credentials["api_key"]})
                response.raise_for_status()
                data = response.json()
            values = sorted(data["data"], key=lambda x: x["index"])
            if [x["index"] for x in values] != list(range(len(texts))):
                raise ValueError("embedding response indices mismatch")
            vectors = [x["embedding"] for x in values]
            if not vectors or not vectors[0] or any(len(v) != len(vectors[0]) or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in v) or not any(v) for v in vectors):
                raise ValueError("embedding dimensions/values invalid")
            if profile.get("dimensions") and len(vectors[0]) != profile["dimensions"]:
                raise ValueError("embedding configured dimension mismatch")
            if data.get("usage", {}).get("prompt_tokens") is not None:
                usage = {"input_tokens": data["usage"]["prompt_tokens"], "output_tokens": 0}
            return vectors
        finally:
            budget.settle(receipt, usage)


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("incompatible embedding dimensions")
    return sum(x*y for x, y in zip(a, b)) / (sum(x*x for x in a)**.5 * sum(x*x for x in b)**.5)
