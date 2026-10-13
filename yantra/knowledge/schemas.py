"""Knowledge contracts; verification and judgment are separate dimensions."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import Field

from ..schemas import Contract


def normalized(value):
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in sorted(value.items()) if v is not None}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(normalized(value), ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


class EvidenceRef(Contract):
    source_id: str
    source_hash: str
    anchor_ref: str
    locator: dict[str, Any]


class KnowledgeItem(Contract):
    company_id: str
    kind: Literal["source", "fact", "thesis", "question", "study"]
    content: dict[str, Any]
    security_id: str | None = None
    period: str | None = None
    disclosed_at: str | None = None
    disclosure_basis: str | None = None
    research_cutoff: str | None = None
    formed_at: str | None = None
    topics: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    opposing_evidence: list[EvidenceRef] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    invalidation_conditions: list[str] = Field(default_factory=list)
    judgment: Literal["open", "supported", "weakened", "refuted", "unresolved", "closed"] = "open"
    usage: Literal["shared", "session"] = "shared"
    retention: dict[Literal["R1", "R2", "R3", "R4"], str] = Field(default_factory=dict)
    relations: dict[str, list[str]] = Field(default_factory=dict)
    manual: bool = False
    reason: str = "automatic archive"


class KnowledgeQuery(Contract):
    query: str = ""
    company_ids: list[str] | None = None
    security_id: str | None = None
    period: str | None = None
    kinds: list[str] | None = None
    topics: list[str] | None = None
    cutoff: str | None = None
    limit: int = Field(default=12, ge=1, le=100)
    context_bytes: int = Field(default=16000, ge=1, le=100000)
    summary_only: bool = False


class ProposalArguments(Contract):
    item: KnowledgeItem
    entry_id: str | None = None
    base_version: int = Field(ge=0)


class ReadKnowledge(Contract):
    entry_id: str
    revision_id: str
    adopt: bool = False
    max_content_bytes: int = Field(default=64000,ge=1,le=100000)


class KnowledgeReview(Contract):
    proposal_id: str
    content_hash: str
    passed: bool
    reasons: list[str] = Field(min_length=1)


class ReviewBatch(Contract):
    reviews: list[KnowledgeReview]


class Comparison(Contract):
    entry_id: str
    revision_id: str
    relation: Literal["supports", "weakens", "refutes", "scope_changed", "assumptions_changed", "insufficient"]
    reason: str
    metric_scope: str
    evidence: list[EvidenceRef] = Field(default_factory=list)
    follow_up: str


class ComparisonBatch(Contract):
    comparisons: list[Comparison]
