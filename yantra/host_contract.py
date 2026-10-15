"""Versioned host inputs, shared by the CLI, MCP and schema discovery."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .schemas import AutonomyAuthorization, Contract, Limits, Pricing, Research


class EmptyInput(Contract):
    pass


class DoctorInput(Contract):
    command_tool: Literal["available", "denied", "missing", "unknown"] = "unknown"
    mcp_available: bool = False
    materials: list[str] = Field(default_factory=list)


class SubmitInput(Contract):
    idempotency_key: str = Field(min_length=1, max_length=128)
    company: str = Field(min_length=1)
    mode: Literal["real-model", "offline-demo"] = "real-model"
    materials: list[str] = Field(default_factory=list)
    demo_materials_dir: str | None = None
    horizon: str | None = None
    cutoff: str | None = None
    market: Literal["CN", "HK"] | None = None
    security_code: str | None = None
    exchange: str | None = None
    trading_currency: str | None = None
    company_id: str | None = None
    share_class: str | None = None
    questions: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    reference_price: float | None = Field(default=None, gt=0)
    price_time: str | None = None
    price_source: str | None = None
    confirmation_mode: Literal["autonomous", "manual"] | None = None
    authorization: AutonomyAuthorization | None = None
    limits: dict = Field(default_factory=dict)
    pricing: Pricing | None = None
    framework: str | None = None
    framework_file: str | None = None
    llm: str | None = None
    ocr: str | None = None
    embedding: str | None = None
    parent_run_id: str | None = None

    @model_validator(mode="after")
    def business_inputs(self):
        Limits.model_validate(self.limits)
        if self.limits.get("run_seconds", 7200) > 7200:
            raise ValueError("wall clock limit cannot exceed 7200 seconds")
        if self.mode == "offline-demo" and (not self.demo_materials_dir or self.materials):
            raise ValueError("demo requires a fictional manifest directory and no raw materials")
        if self.mode == "real-model" and self.demo_materials_dir:
            raise ValueError("formal research does not accept demo materials")
        # Reuse the business contract rather than maintaining a second validator.
        Research.model_validate({k: v for k, v in self.model_dump().items()
            if k in Research.model_fields and v is not None} | {
                "horizon": self.horizon or "12个月", "cutoff": self.cutoff or "2025-12-31"})
        return self


class RunInput(Contract):
    run_id: str = Field(pattern=r"^run-[a-z0-9-]+$")


class ListInput(Contract):
    after: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ReadInput(RunInput):
    artifact: str = "report.md"
    version: int | None = Field(default=None, ge=1, description="Immutable output version, distinct from assumptions_version")
    offset: int = Field(default=0, ge=0)
    length: int = Field(default=16384, ge=1, le=65536)


class ChangeInput(RunInput):
    operation_id: str = Field(min_length=1, max_length=128)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class RespondInput(ChangeInput):
    decision: Literal["confirm", "modify", "reject", "select_identity"]
    parameters: dict | None = None
    selector: str | None = None
    pending_id: str | None = None

    @model_validator(mode="after")
    def response_inputs(self):
        if (self.decision == "modify") != (self.parameters is not None):
            raise ValueError("only modify requires parameters")
        if (self.decision == "select_identity") != (self.selector is not None):
            raise ValueError("only identity selection requires selector")
        return self


class ResumeInput(ChangeInput):
    continue_research: bool = False


INPUTS = {"schema": EmptyInput, "doctor": DoctorInput, "submit": SubmitInput,
          "list": ListInput, "status": RunInput, "read": ReadInput,
          "respond": RespondInput, "cancel": RunInput, "resume": ResumeInput}


def schema():
    return {"interface_version": "1", "envelope_schema_version": "1",
            "operations": {name: model.model_json_schema() for name, model in INPUTS.items()},
            "trust": "trusted local clients sharing one explicitly configured workspace; no user/session authentication",
            "versions": "revision is a state CAS token; output version is immutable; assumptions_version is the calculation authorization version"}
