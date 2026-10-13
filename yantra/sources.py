"""v0.3 discovery/fetch contract; no subscriptions, polling or plugin platform."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .materials import now, cutoff_time


@dataclass
class DiscoveryRequest:
    question: str
    target: str
    cutoff: str | None
    purpose: str
    max_results: int
    budget: object  # Existing check/timeout context, never a fresh budget.


@dataclass
class Candidate:
    adapter_id: str
    source_id: str
    url: str | None = None
    external_id: str | None = None
    locator: str | None = None
    canonical_url: str | None = None
    title: str | None = None
    published_at: str | None = None
    publication_basis: str | None = None
    content_type: str | None = None
    snippet: str | None = None


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]
    errors: list[str] = field(default_factory=list)
    cursor: str | None = None
    checkpoint: dict | None = None


@dataclass
class FetchResult:
    candidate: Candidate
    content: bytes | None
    suffix: str | None
    acquired_at: str
    status: str
    failure_reason: str | None = None


class SourceAdapter(Protocol):
    id: str

    def discover(self, request: DiscoveryRequest, cursor: str | None = None) -> DiscoveryResult: ...
    def fetch(self, candidate: Candidate, budget: object) -> FetchResult: ...


class WebSearchAdapter:
    id = "web_search"

    def discover(self, request, cursor=None):
        from ddgs import DDGS
        errors, hits = [], []
        for backend in ("auto", "bing"):
            request.budget.check()
            try:
                hits = list(DDGS(timeout=max(1, int(min(15, request.budget.timeout())))).text(request.question, max_results=request.max_results, backend=backend))
                if hits:
                    break
            except Exception as exc:
                errors.append(type(exc).__name__)
        return DiscoveryResult([Candidate(self.id, "public_web", url=h["href"], title=h.get("title"), snippet=h.get("body")) for h in hits if h.get("href")], errors)

    def fetch(self, candidate, budget):
        if not candidate.url:
            return FetchResult(candidate, None, None, now(), "failed", "URL missing")
        raw = budget.fetch_raw(candidate.url)
        candidate.canonical_url = raw["final_url"]
        candidate.content_type = raw["mime"] or None
        return FetchResult(candidate, raw["content"], raw["suffix"], now(), "fetched")


class SourcePipeline:
    def __init__(self, materials, adapter=None, checkpoint_store=None, cutoff=None):
        self.materials, self.adapter = materials, adapter or WebSearchAdapter()
        self.checkpoint_store, self.cutoff = checkpoint_store, cutoff

    def acquire(self, candidate):
        self.materials.check()
        if candidate.published_at and self.cutoff and cutoff_time(candidate.published_at) > cutoff_time(self.cutoff):
            return {"url": candidate.url, "status": "excluded", "failure_reason": "publication after cutoff"}
        result = self.adapter.fetch(candidate, self.materials)
        from dataclasses import asdict
        receipt = asdict(candidate) | {"source_channel_id":candidate.source_id,"status": result.status, "failure_reason": result.failure_reason, "acquired_at": result.acquired_at}
        if result.status != "fetched" or result.content is None:
            return receipt
        if len(result.content) > 40_000_000:
            raise ValueError("material exceeds 40 MB limit")
        source = self.materials.add(result.content, result.suffix or ".txt", candidate.locator or candidate.canonical_url or candidate.url or candidate.external_id or self.adapter.id,
                                    url=candidate.url, disclosure_date=candidate.published_at, channel=receipt)
        return receipt | {"source_id": source["id"], "raw_content_ref": source["file"], "source_hash": source["source_hash"], "parsed": bool(source["chunks"])}

    def discover_and_fetch(self, query, purpose, max_results):
        request = DiscoveryRequest(query, getattr(self.materials,"target_id",None) or self.materials.load_manifest()["company"], self.cutoff, purpose, max_results, self.materials)
        import hashlib
        import json
        checkpoint_target=request.target+":"+hashlib.sha256(json.dumps([query,purpose,self.cutoff],ensure_ascii=False).encode()).hexdigest()
        cursor = None
        if self.checkpoint_store:
            with self.checkpoint_store.connect() as db:
                row = db.execute("SELECT cursor FROM checkpoints WHERE adapter=? AND target=?", (self.adapter.id, checkpoint_target)).fetchone()
                cursor = row[0] if row else None
        found = self.adapter.discover(request, cursor)
        entries = []
        for candidate in found.candidates:
            self.materials.check()
            try:
                item = self.acquire(candidate)
            except Exception as exc:
                from .budget import BudgetExceeded
                if isinstance(exc, BudgetExceeded):
                    raise
                item = {"url": candidate.url, "title": candidate.title, "adapter_id": candidate.adapter_id, "external_id": candidate.external_id,
                        "status": "failed", "failure_reason": type(exc).__name__, "error": type(exc).__name__}
            entries.append(item | {"purpose": purpose})
        if self.checkpoint_store:
            with self.checkpoint_store.connect() as db:
                db.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)", (self.adapter.id, checkpoint_target, found.cursor, json.dumps(found.checkpoint)))
        return {"query": query, "purpose": purpose, "acquired_at": now(), "results": entries, "errors": found.errors,
                "adapter_id": self.adapter.id, "cursor": found.cursor, "checkpoint": found.checkpoint, "service_cost_cny": 0}
