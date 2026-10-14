"""Bounded, opt-in live case generation; default prints an unpaid evaluation plan."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pydantic import BaseModel, ConfigDict, Field
from tests_eval_support import FixedSources, material_text

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "tests/eval/datasets/behavior.json"


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    approved: bool
    model_profile: str = Field(min_length=1)
    max_models: int = Field(ge=1)
    max_cost_cny: float = Field(gt=0, lt=50)
    run_seconds: float = Field(gt=30, le=7200)
    cases: list[str] = Field(min_length=1)


def approved_cases(path, dataset):
    approval = Approval.model_validate(json.loads(path.read_text()))
    available = {case["eval_case_id"]: case for case in dataset["eval_cases"]}
    if not approval.approved or len(approval.cases) != len(set(approval.cases)) or not set(approval.cases) <= available.keys():
        raise ValueError("Explicit approval and unique known case IDs are required.")
    if approval.max_models < len(approval.cases):
        raise ValueError("Total model-call budget is smaller than the number of cases.")
    return approval, [available[name] for name in approval.cases]


def send(client, session, text):
    response = client.post("/run_sse", json={"appName": "yantra", "userId": "eval", "sessionId": session,
        "newMessage": {"role": "user", "parts": [{"text": text}]}})
    response.raise_for_status()
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    snapshot = next(json.loads(part["text"]) for event in reversed(events)
        for part in event.get("content", {}).get("parts", []) if part.get("text"))
    return snapshot, events


def read_trace(client, session):
    events, after = [], 0
    while True:
        response = client.get(f"/apps/yantra/users/eval/sessions/{session}/research/trace", params={"limit": 500, "after": after})
        response.raise_for_status()
        page = response.json()
        events.extend(page["events"])
        after = page["cursor"]
        if len(page["events"]) < 500:
            return {**page, "events": events}


def generate(args, approval, cases):
    # No cloud judge, MCP, embeddings, OCR model or user knowledge in this corpus.
    from dotenv import load_dotenv
    from fastapi.testclient import TestClient
    from yantra.api import create_app
    from yantra.configuration import ConfigStore, empty_config, environment_values, redact
    from yantra.models import ModelCatalog
    from yantra.research import RawEngine
    from yantra.engine import Engine
    from unittest.mock import patch
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    profile = ModelCatalog(ConfigStore(args.config)).resolve("llm", approval.model_profile)
    if profile is None:
        raise ValueError("Approval must name an existing registered LLM profile, not an environment fallback.")
    values = environment_values({"key": profile["api_key_env"]})
    if not profile.get("pricing"):
        raise ValueError("Selected existing model needs sourced pricing; no fallback judge or model.")
    if args.output.exists():
        raise ValueError("Choose a new output directory; evaluations do not overwrite prior evidence.")
    args.output.mkdir(parents=True)
    settings = ConfigStore(args.output / "config.json")
    config = empty_config()
    config["models"][approval.model_profile] = {key: value for key, value in profile.items() if key != "name"}
    config["selected"]["llm"] = approval.model_profile
    settings.update(lambda saved: saved.update(config))
    storage = args.output / "runs"
    # The isolated fictional evaluator explicitly refuses autonomous selection;
    # it never copies or changes the operator's persistent preferences.
    from yantra.knowledge.preferences import Preferences
    from yantra.knowledge.store import KnowledgeStore
    Preferences(KnowledgeStore(storage / "knowledge")).setup({"horizon": "12个月", "framework": "auto", "confirmation_mode": "manual",
        "authorization": {"status": "refused", "scope": []}})
    os.environ["YANTRA_RUNS_DIR"] = str(storage.resolve())
    total_deadline = time.time() + approval.run_seconds
    traces, spent_calls, spent_cost = [], 0, 0.0

    def load_eval_run(company="EVAL_SYNTHETIC", run_id=None):
        if run_id is None:
            raise ValueError("Evaluation requires a prepared bounded run.")
        loaded = Engine.load(storage, run_id)
        loaded.tools.adapter.source_adapter = FixedSources()
        return loaded

    try:
        for case in cases:
            remaining = total_deadline - time.time()
            if remaining <= 30:
                raise ValueError("Total evaluation wall-clock budget exhausted.")
            material = args.output / (case["eval_case_id"] + ".txt")
            material.write_text(material_text(case["context"]["material"]))
            limits = {"max_models": approval.max_models // len(cases),
                "max_cost_cny": approval.max_cost_cny / len(cases), "run_seconds": remaining,
                "task_seconds": min(120, remaining), "total_tools": 24, "task_tools": 8, "max_main_loops": 20}
            engine = RawEngine.create_raw(storage, {"company": "EVAL_SYNTHETIC", "market": "CN", "security_code": "600000",
                "horizon": "12个月", "cutoff": "2026-10-13", "confirmation_mode": "manual",
                "questions": [case["context"]["question"]]}, [material], settings=settings, limits=limits)
            with TestClient(create_app()) as client:
                session = client.post("/apps/yantra/users/eval/sessions", json={"state": {"run_id": engine.state["run_id"]}}).json()["id"]
            from yantra.adk_runtime import TraceStore
            from types import SimpleNamespace
            TraceStore(engine.directory).bind(SimpleNamespace(app_name="yantra", user_id="eval", id=session))
            turns, responses, observations = [], [], []
            for index, message in enumerate(["继续", *case["context"]["turns"]]):
                # Fresh API and SQLite service on every turn exercises persisted restart.
                # Existing SourceAdapter contract supplies fixed discovery. The
                # API, Runner, tools and controller remain the real implementations.
                with patch("yantra.engine.create_adk_engine", new=load_eval_run), TestClient(create_app()) as client:
                    snapshot, events = send(client, session, message)
                    observations.append(read_trace(client, session))
                turns.append({"turn_index": index, "events": events})
                responses.append({"response": {"role": "model", "parts": [{"text": json.dumps(snapshot, ensure_ascii=False)}]}})
            state = json.loads((engine.directory / "state.json").read_text())
            spent_calls += state["budget"]["model_calls"]
            spent_cost += state["budget"].get("model_cost_cny", 0)
            if spent_calls > approval.max_models or spent_cost > approval.max_cost_cny:
                raise AssertionError("Authoritative evaluation budget exceeded.")
            traces.append({**case, "agent_data": {"turns": turns}, "responses": responses,
                "context": {**case["context"], "research_trace": observations, "controller": {
                    "status": state["status"], "budget": state["budget"], "deadline": state["deadline"],
                    "issues": state["issues"], "report": state.get("report"), "results": state["results"],
                    "model_ledger": state["model_ledger"]}}})
    finally:
        runs = [json.loads(path.read_text()) for path in storage.glob("run-*/state.json")]
        spent_calls = sum(state["budget"]["model_calls"] for state in runs)
        spent_cost = sum(state["budget"].get("model_cost_cny", 0) for state in runs)
        (args.output / "traces.json").write_text(json.dumps(redact({"eval_cases": traces}, values.values()), ensure_ascii=False, indent=2) + "\n")
        (args.output / "generation.json").write_text(json.dumps({"time": datetime.now(timezone.utc).isoformat(),
            "scope": "live model generation; ungraded", "expected_cases": approval.cases,
            "generated_cases": [case["eval_case_id"] for case in traces], "model_calls": spent_calls,
            "model_cost_cny": spent_cost, "run_ids": [state["run_id"] for state in runs],
            "judge": "not executed", "approval": approval.model_dump()}, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    dataset = json.loads(DATASET.read_text())
    if not args.live:
        print(json.dumps({"status": "not_executed", "cases": [case["eval_case_id"] for case in dataset["eval_cases"]],
            "reason": "Live generation requires explicit existing model, sourced pricing and total approval budgets; judge needs separate approval."}))
        return
    if not args.approval or not args.config or not args.output:
        parser.error("--live requires --approval, --config and a new --output directory")
    approval, cases = approved_cases(args.approval, dataset)
    generate(args, approval, cases)


if __name__ == "__main__":
    main()
