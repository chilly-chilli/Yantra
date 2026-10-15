"""One detached local executor; no model or research work in the MCP process."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from .cli_contract import CLIError, exception_error, load_json_file
from .host_service import HostService, canonical, state_revision


def create_engine(service, row):
    from .engine import Engine
    from .host_contract import SubmitInput
    from .schemas import Research
    request = SubmitInput.model_validate_json(row["payload"])
    research = {key: value for key, value in request.model_dump(mode="json").items()
                if key in Research.model_fields and value is not None}
    research.update(horizon=request.horizon or "12个月", cutoff=request.cutoff or (
        "2025-12-31" if request.mode == "offline-demo" else datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat()))
    if request.mode == "offline-demo":
        directory = Path(request.demo_materials_dir)
        if load_json_file(directory / "manifest.json", "demo_materials_dir").get("fictional") is not True:
            raise CLIError("demo_materials_invalid", "离线演示只接受 fictional=true 的合成材料")
        return Engine.create(service.runs, research, materials_dir=directory, limits=request.limits, run_id=row["run_id"])
    from .research import RawEngine
    explicit = {key: value for key, value in {"horizon": request.horizon, "framework": request.framework,
        "questions": request.questions or None, "topics": request.topics or None,
        "confirmation_mode": request.confirmation_mode}.items() if value is not None}
    return RawEngine.create_raw(service.runs, research, request.materials, limits=request.limits,
        pricing=request.pricing.model_dump() if request.pricing else None, framework=request.framework or "auto",
        framework_file=request.framework_file, settings=service.settings, llm_profile=request.llm,
        ocr_profile=request.ocr, embedding_profile=request.embedding,
        knowledge_dir=service.workspace / ".yantra/knowledge", explicit_parameters=explicit,
        authorization_override=request.authorization.model_dump() if request.authorization else None,
        run_id=row["run_id"], started_at=row["created_at"])


async def execute_job(service, row):
    from .adk_app import run_engine
    from .engine import Engine
    directory = service.directory(row["run_id"])
    exists = (directory / "state.json").exists()
    if row["cancel_requested"] and not exists:
        service.finish_job(row["run_id"], "cancelled")
        return
    operation = None
    if row["job"] not in {"initial", "cancel"}:
        operation_id = row["job"].removeprefix("op:")
        with service.database() as db:
            operation = db.execute("SELECT * FROM operations WHERE run_id=? AND operation_id=?", (row["run_id"], operation_id)).fetchone()
        if operation is None:
            raise CLIError("operation_not_found", "持久操作不存在")
        receipt = json.loads(operation["result"])
        if receipt["receipt"] != "accepted":
            service.finish_job(row["run_id"], "idle")
            return
    error = None
    try:
        if service.env_file:
            from dotenv import load_dotenv
            if not service.env_file.is_file():
                raise CLIError("input_file_error", "环境文件不存在；请检查本地路径")
            load_dotenv(service.env_file, override=True)
        if row["job"] == "initial" and exists:
            raise CLIError("recovery_required", "初始执行已创建记录；需明确 resume，不能重放提交")
        engine = Engine.load(service.runs, row["run_id"]) if exists else create_engine(service, row)
        before = json.loads(json.dumps(engine.state["assumptions"]))
        before_revision = state_revision(engine.state)
        from .host_outputs import index
        previous_outputs = [item["version"] for item in index(engine.directory)]
        if not exists:
            engine.state["host_contract_version"] = 1
            engine.state["parent_run_id"] = json.loads(row["payload"]).get("parent_run_id")
            engine._save()
        if service.row(row["run_id"])["cancel_requested"] or row["job"] == "cancel":
            await engine.respond("cancel")
        elif operation:
            payload = json.loads(operation["payload"])
            if exists:
                engine.expected_host_revision = payload["expected_revision"]
            if engine.state.get("in_flight_tool") or engine.state.get("in_flight"):
                raise CLIError("external_outcome_unknown", "中断操作结果未知，禁止隐式重放")
            if payload["action"] == "respond":
                if payload["decision"] == "select_identity":
                    await engine.select_identity(payload["selector"])
                else:
                    await engine.respond(payload["decision"], payload.get("parameters"))
            else:
                if payload["continue_research"]:
                    if not engine.state.get("raw_materials") or engine.state["status"] not in {"partial", "failed"}:
                        raise CLIError("invalid_run_state", "continue_research 仅适用于 partial/failed 原始研究")
                    with engine._lock():
                        engine.run_budget.check()
                        engine.state.setdefault("resume_history", []).append({"status": engine.state["status"], "stop_reasons": engine.state["stop_reasons"]})
                        engine.state.update(status="running", stop_reasons=[])
                        engine._save()
                elif engine.state["status"] in {"partial", "failed", "completed"}:
                    raise CLIError("invalid_run_state", "已结束研究需明确 continue_research；预算耗尽时新建后续研究")
                await run_engine(engine)
        else:
            await run_engine(engine)
        # A cancellation arriving during execution must also settle if a legacy
        # engine reaches a waiting boundary before observing its request file.
        if service.row(row["run_id"])["cancel_requested"] and engine.state["status"] in {"running", "waiting"}:
            await engine.respond("cancel")
        if operation:
            after = engine.state["assumptions"]
            changes = {key: {"before": before["parameters"].get(key), "after": after["parameters"].get(key)}
                       for key in sorted(set(before["parameters"]) | set(after["parameters"]))
                       if before["parameters"].get(key) != after["parameters"].get(key)}
            receipt = {"receipt": "applied", "revision": state_revision(engine.state),
                       "assumptions_version": engine.state["assumptions"]["version"],
                       "previous_assumptions_version": before["version"], "previous_outputs": previous_outputs,
                       "parameter_changes": changes,
                       "outputs": [item["version"] for item in index(engine.directory)]}
    except Exception as exc:
        error = exception_error(exc).error
        if operation:
            receipt = {"receipt": "rejected", "error": error}
            if "before_revision" in locals() and state_revision(service.state(row["run_id"])) != before_revision:
                receipt = {"receipt": "accepted", "application": "outcome_unknown", "error": error,
                           "notice": "错误发生前状态已改变；核对已保存成果，不自动重放"}
    if operation:
        with service.database() as db:
            db.execute("UPDATE operations SET result=? WHERE run_id=? AND operation_id=?", (canonical(receipt), row["run_id"], operation_id))
            db.commit()
    service.finish_job(row["run_id"], "failed" if error else "idle", error)


def main():
    parser = argparse.ArgumentParser()
    for name in ("workspace", "config", "runs-dir", "run-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--env-file")
    args = parser.parse_args()
    service = HostService(args.workspace, config=args.config, runs_dir=args.runs_dir, env_file=args.env_file)
    # An executor cannot coexist with another host executor for this run.
    with service.gate(args.run_id, blocking=True):
        row = service.row(args.run_id)
        service.finish_job(args.run_id, "active")
        try:
            asyncio.run(execute_job(service, row))
        except Exception as exc:
            service.finish_job(args.run_id, "failed", exception_error(exc).error)


if __name__ == "__main__":
    main()
