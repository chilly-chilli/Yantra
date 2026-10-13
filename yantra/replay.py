"""Offline re-audit and deterministic recomputation of immutable raw-run snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .research import RawEngine


async def replay_raw(storage, original):
    if original["status"] in {"running", "waiting"}:
        raise ValueError("活动或等待中的研究不能自动批准复测")
    source = Path(storage).resolve() / original["run_id"]
    index = original["input_snapshot"]["manifest"]
    for entry in index["materials"]:
        content = (source / "materials" / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["source_hash"]:
            raise ValueError("原始快照被修改，拒绝复测")
    state = json.loads(json.dumps(original))
    state.update(run_id="run-" + uuid4().hex[:12], mode="offline-replay", status="running", in_flight=None,
                 replay_source=original["run_id"], replay_scope="固定原始字节、提取成果、框架及假设版本；复算及程序重新核验，不重放远程推理")
    directory = Path(storage).resolve() / state["run_id"]
    directory.mkdir(parents=True)
    shutil.copytree(source / "materials", directory / "materials")
    engine = RawEngine(directory, state)
    differences = []
    for ref, receipt in original["calculations"].items():
        if receipt["kind"] == "dcf":
            recomputed = engine.tools._valuation({"parameters": receipt["inputs"], "assumptions_version": receipt["assumptions_version"]}).data["calculation"]
        elif receipt["kind"] == "raw_financial_metrics":
            rows = [{k: v for k, v in row.items() if k not in {"normalized_value", "normalized_unit"}} for row in receipt["inputs"]]
            recomputed = engine.tools._calculate_metrics({"records": rows}).data["calculation"]
        else:
            recomputed = receipt
        if recomputed != receipt:
            differences.append(ref)
    check = engine.tools._check_consistency({"results": original["artifacts"], "assumptions": original["assumptions"], "research": original["research"], "user_overrides": original["user_overrides"]})
    engine.state["replay_audit"] = check.model_dump()
    engine._save()
    engine._log("offline_replay", {"source_run": original["run_id"], "calculation_differences": differences,
                                 "scope": state["replay_scope"], "live_retrieval": False, "remote_model_calls": 0})
    if not check.data["passed"]:
        engine.state["issues"] = list(engine.state["issues"]) + check.data["issues"]
    engine._finish(cancelled=original["status"] == "cancelled")
    return engine, {"first": original["run_id"], "second": state["run_id"], "calculation_equal": not differences,
                    "differences": differences, "program_audit_passed": check.data["passed"], "remote_model_calls": 0}
