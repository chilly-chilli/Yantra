"""Validated messages exchanged by the controller, workers, and tools."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["finance", "thesis", "valuation", "verification"]
Purpose = Literal["collect", "supplement", "repair", "analyze", "propose", "calculate", "audit", ""]
RunStatus = Literal["running", "waiting", "completed", "partial", "failed", "cancelled"]
ValuationMethod = Literal["dcf", "pe", "pb", "ev_ebitda", "ddm"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, allow_inf_nan=False)


class Limits(Contract):
    total_tools: int = Field(default=20, ge=0)
    task_tools: int = Field(default=6, ge=0)
    retries: int = Field(default=2, ge=0)
    repair_rounds: int = Field(default=2, ge=0)
    max_models: int = Field(default=60, ge=1)
    max_main_loops: int = Field(default=30, ge=1)
    task_seconds: float = Field(default=120, gt=0)
    run_seconds: float = Field(default=900, gt=0)
    max_cost_usd: float = Field(default=5, ge=0)
    model_cost_usd: float = Field(default=0.01, ge=0)
    max_cost_cny: float = Field(default=49, gt=0, lt=50)
    close_seconds: float = Field(default=30, ge=1)


class Research(Contract):
    company: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    cutoff: str = Field(min_length=1)
    reference_price: float | None = Field(default=None, gt=0)
    price_time: str | None = None
    price_source: str | None = None
    timezone: str = "Asia/Shanghai"
    market: Literal["CN", "HK"] | None = None
    security_code: str | None = None
    exchange: str | None = None
    trading_currency: str | None = None
    confirmation_mode: Literal["autonomous", "manual"] = "manual"
    company_id: str | None = None
    share_class: str | None = None
    questions: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class Assumptions(Contract):
    model: ValuationMethod = "dcf"  # Missing identity in historical snapshots means legacy DCF.
    version: int = Field(default=1, ge=1)
    confirmed: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)
    basis: dict[str, Any] = Field(default_factory=dict)
    authorization_source: Literal["none", "user", "controller"] = "none"


class Task(Contract):
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    target: Role
    company: str = Field(min_length=1)
    horizon: str = Field(min_length=1)
    cutoff: str = Field(min_length=1)
    question: str = Field(min_length=1)
    input_refs: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    assumptions: Assumptions
    authorized_tools: list[str]
    completion_conditions: list[str]
    tool_limit: int = Field(ge=0)
    time_limit_seconds: float = Field(gt=0)
    cost_limit_usd: float = Field(ge=0)
    cost_limit_cny: float | None = Field(default=None, ge=0)
    purpose: Purpose = ""
    parent_task_id: str | None = None
    depth: int = Field(default=2, ge=2, le=3)
    issuer: str = "main"
    dynamic_role: str | None = None
    shared_refs: list[str] = Field(default_factory=list)
    framework_version: str | None = None
    standard_version: int = Field(default=1, ge=1)


class Result(Contract):
    task_id: str = Field(min_length=1)
    status: Literal["complete", "partial", "blocked"]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    assumptions_version: int = Field(ge=1)
    calculation_refs: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    suggested_tasks: list[dict[str, Any]] = Field(default_factory=list)
    actual_calls: int = Field(ge=0)


class MainAction(Contract):
    action: Literal["delegate_task", "ask_user", "finalize", "approve_transfer", "revise_framework", "revise_standard"]
    target: Role | None = None
    purpose: Purpose = ""
    question: str = ""
    rationale: str = ""
    candidate: dict[str, Any] = Field(default_factory=dict)
    transfer_id: str | None = None
    framework_text: str | None = None
    standard_changes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_target(self) -> MainAction:
        if self.action == "delegate_task" and self.target is None:
            raise ValueError("delegate_task requires a target")
        if self.action != "delegate_task" and self.target is not None:
            raise ValueError("only delegate_task may select a target")
        if self.action == "ask_user" and not self.question.strip():
            raise ValueError("ask_user requires a question")
        return self


class WorkerAction(Contract):
    action: Literal["call_tool", "return"]
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Result | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def valid_worker_action(self) -> WorkerAction:
        if self.action == "call_tool" and (not self.tool or self.result is not None):
            raise ValueError("call_tool requires a tool and no result")
        if self.action == "return" and (self.result is None or self.tool):
            raise ValueError("return requires a result and no tool")
        return self


class Issue(Contract):
    id: str = Field(min_length=1)
    severity: Literal["critical", "major", "minor"]
    target: Role
    requirement: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ToolResponse(Contract):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    calculation_refs: list[str] = Field(default_factory=list)


class FinancialRecord(Contract):
    field: str = Field(min_length=1)
    value: float
    period: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    consolidated: bool
    source_ref: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    raw_value: str | None = None
    disclosure_date: str | None = None
    restatement: str | None = None
    cash_flow_basis: str | None = None
    security_code: str | None = None
    share_scope: str | None = None
    disclosure_source_ref: str | None = None
    disclosure_quote: str | None = None
    model_value: float | None = None
    normalization_note: str | None = None
    share_class: str | None = None
    accounting_scope: Literal["consolidated", "attributable", "parent"] | None = None
    business_scope: str | None = None
    information_kind: Literal["fact", "management_statement", "assumption", "prediction", "judgment"] | None = None


class EvidenceRecord(Contract):
    statement: str = Field(min_length=1)
    kind: Literal["fact", "management_statement", "assumption", "prediction", "judgment"]
    source_ref: str
    source_hash: str
    quote: str = Field(min_length=1)
    disclosure_date: str | None = None
    period: str | None = None
    speaker: str | None = None
    question: str
    disclosure_source_ref: str | None = None
    disclosure_quote: str | None = None


class Extraction(Contract):
    financials: list[FinancialRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class SearchArguments(Contract):
    query: str = Field(min_length=1)
    purpose: Literal["disclosure", "counter_evidence", "changes", "expectations", "gap", "identity"]
    max_results: int = Field(default=3, ge=1, le=6)


class FetchArguments(Contract):
    url: str


class ChildArguments(Contract):
    role: str = Field(min_length=1)
    question: str = Field(min_length=1)
    tools: list[str]
    shared_refs: list[str] = Field(default_factory=list)
    completion_conditions: list[str] = Field(min_length=1)


class CollaborationArguments(Contract):
    operation: Literal["read", "message", "request_transfer"]
    target_task_id: str
    message: str = ""
    input_refs: list[str] = Field(default_factory=list)
    completion_conditions: list[str] = Field(default_factory=list)
    tool_budget: int = Field(default=0, ge=0)


class Framework(Contract):
    id: str
    name: str
    scope: str = Field(min_length=1)
    questions: list[str] = Field(min_length=1)
    evidence_needs: list[str] = Field(min_length=1)
    risks: list[str] = Field(min_length=1)
    disconfirming: list[str] = Field(min_length=1)
    tracking: list[str] = Field(min_length=1)


class Pricing(Contract):
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)
    currency: str = "CNY"
    cny_per_currency: float = Field(default=1, gt=0)
    source: str = Field(min_length=1)
    as_of: str = Field(min_length=1)
    fx_source: str | None = None
    fx_as_of: str | None = None

    @model_validator(mode="after")
    def fx_basis(self):
        if self.currency != "CNY" and not (self.fx_source and self.fx_as_of):
            raise ValueError("non-CNY pricing requires dated FX source")
        if self.currency == "CNY" and self.cny_per_currency != 1:
            raise ValueError("CNY pricing cannot apply an exchange rate")
        return self


class SecurityIdentity(Contract):
    legal_name: str
    market: Literal["CN", "HK"]
    exchange: str
    security_code: str
    trading_currency: str
    share_class: str
    source_ref: str
    quote: str


class IdentityResolution(Contract):
    candidates: list[SecurityIdentity]
    gaps: list[str] = Field(default_factory=list)


class FrameworkChoice(Contract):
    identifier: Literal["value", "quality", "change"]
    reason: str = Field(min_length=1)


class ThesisData(Contract):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    disconfirming: list[str] = Field(default_factory=list)
    tracking: list[str] = Field(default_factory=list)
    expectations: dict[str, Any] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    summary: str | None = None


class ThesisResult(Result):
    data: ThesisData


class ThesisAction(WorkerAction):
    result: ThesisResult | None = None


class ValuationProposal(Contract):
    model: Literal["dcf", "pe", "pb", "ev_ebitda", "ddm", "not_applicable"]
    parameters: dict[str, Any] = Field(default_factory=dict)
    basis: dict[str, Any] = Field(default_factory=dict)
    scope: str
    gaps: list[str] = Field(default_factory=list)


class ProposalResult(Result):
    data: ValuationProposal


class ProposalAction(WorkerAction):
    result: ProposalResult | None = None


class FrameworkReview(Contract):
    passed: bool
    reasons: list[str]


class ReadArguments(Contract):
    company: str
    material_ids: list[str] | None = None
    cutoff: str | None = None
    query: str | None = None
    anchor_refs: list[str] | None = None
    max_chunks: int = Field(default=12, ge=1, le=30)


class ConversionArguments(Contract):
    value: float
    from_unit: str
    to_unit: str
    currency: str


class MetricsArguments(Contract):
    records: list[FinancialRecord]


class ValuationArguments(Contract):
    model: ValuationMethod = "dcf"
    parameters: dict[str, Any]
    assumptions_version: int = Field(ge=1)
    basis: dict[str, Any] = Field(default_factory=dict)


class CheckArguments(Contract):
    results: list[dict[str, Any]] | dict[str, Any]
    assumptions: Assumptions
    research: Research | None = None
    report: dict[str, Any] | None = None
    user_overrides: list[str] = Field(default_factory=list)


class MultipleObservation(Contract):
    multiple_type: Literal["pe", "pb", "ev_ebitda"]
    value: float = Field(gt=0)
    security_code: str = Field(min_length=1)
    share_class: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    period: str = Field(min_length=1)
    earnings_basis: Literal["FY", "TTM", "forward", "point_in_time"]
    accounting_scope: Literal["consolidated", "attributable"]
    per_share_basis: Literal["basic EPS", "diluted EPS", "ending book value per share"] | None = None
    as_of: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)
    price_record: FinancialRecord | None = None
    fundamental_record: FinancialRecord | None = None


class MarketBenchmark(Contract):
    kind: Literal["current_price", "reference_price", "consensus_target", "earnings_expectation"]
    record: FinancialRecord
    as_of: str = Field(min_length=1)
    earnings_basis: Literal["FY", "TTM", "forward", "point_in_time"]
    provider: str = Field(min_length=1)
    sample_size: int | None = Field(default=None, ge=1)
    own_value: float | None = None
    own_basis: str | None = None


class MethodInputs(Contract):
    security_code: str = Field(min_length=1)
    share_class: str = Field(min_length=1)
    currency: Literal["CNY", "HKD", "USD"]
    period: str = Field(min_length=1)
    earnings_basis: Literal["FY", "TTM", "forward", "point_in_time"]
    metric: FinancialRecord
    multiples: list[MultipleObservation] = Field(default_factory=list)
    comparable_rule: str | None = None
    net_debt: FinancialRecord | None = None
    minority_interest: FinancialRecord | None = None
    shares: FinancialRecord | None = None
    cost_of_equity: float | None = Field(default=None, gt=0, lt=1)
    terminal_growth: float | None = Field(default=None, ge=0, lt=1)
    benchmark: MarketBenchmark | None = None


class MethodOutput(Contract):
    model: ValuationMethod
    method_status: Literal["calculated", "enterprise_only"]
    currency: str
    unit: str = "million"
    per_share_unit: str
    per_share_value: float | None = None
    enterprise_value: float | None = None
    equity_value: float | None = None
    value_range: list[float] = Field(default_factory=list)
    range_rule: str
    sensitivity: list[dict[str, Any]] = Field(default_factory=list)
    market_comparison: dict[str, Any]
    gaps: list[str] = Field(default_factory=list)
