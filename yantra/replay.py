"""Offline re-audit and deterministic recomputation of immutable raw-run snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from decimal import InvalidOperation
from uuid import uuid4

from .research import RawEngine
from .tools import _hash
from .v2_tools import canonical
from .schemas import ToolResponse


async def replay_raw(storage, original):
    if original["status"] in {"running", "waiting"}:
        raise ValueError("活动或等待中的研究不能自动批准复测")
    source = Path(storage).resolve() / original["run_id"]
    if original.get("knowledge"):
        from .knowledge.store import KnowledgeStore
        knowledge = KnowledgeStore(original["knowledge"]["root"])
        for fixed in original["knowledge"].get("fixed_refs",[]):
            record = knowledge.read(fixed["entry_id"],fixed["id"])
            for ref in record["item"]["evidence"]+record["item"]["opposing_evidence"]:
                knowledge.source(ref["source_id"],allow_deleted=True)
        for entry in original["input_snapshot"]["manifest"]["materials"]:
            knowledge.source(entry["id"],allow_deleted=True)
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
    differences, errors = [], {}
    for ref, receipt in original["calculations"].items():
        if ref != f"calc:{_hash(receipt)[:24]}":
            differences.append(ref)
        try:
            if receipt["kind"] == "unit_conversion":
                recomputed = engine.tools._convert_units(receipt["inputs"]).data
                recomputed = {k: v for k, v in recomputed.items() if k != "calculation_ref"}
            elif receipt["kind"] == "valuation_method":
                from .valuation import receipt as recompute
                recomputed = recompute(receipt["model"], receipt["inputs"], receipt["assumptions_version"], receipt["basis"])
            elif receipt["kind"] in {"dcf", "dcf_bridge"}:
                recomputed = engine.tools._valuation({"parameters": receipt["inputs"], "basis": receipt.get("basis", {}), "assumptions_version": receipt["assumptions_version"]}).data["calculation"]
            elif receipt["kind"] == "raw_financial_metrics":
                rows = [{k: v for k, v in row.items() if k not in {"normalized_value", "normalized_unit"}} for row in receipt["inputs"]]
                recomputed = engine.tools._calculate_metrics({"records": rows}, verify_sources=False, screening_version=1 if "financial_screening" in receipt["results"] else 0).data["calculation"]
            elif receipt["kind"] == "expectation_comparison":
                recomputed = engine.tools._compare_expectations(receipt["inputs"]).data["calculation"]
            else:
                raise ValueError("unsupported calculation kind")
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            if ref not in differences:
                differences.append(ref)
            errors[ref] = str(exc)
            continue
        if canonical(recomputed) != canonical(receipt):
            if ref not in differences:
                differences.append(ref)
    try:
        check = engine.tools._check_consistency({"results": original["artifacts"], "assumptions": original["assumptions"], "research": original["research"], "user_overrides": original["user_overrides"]})
    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
        check = ToolResponse(ok=True, data={"passed": False, "issues": [{"id": "offline-audit-refusal", "severity": "critical", "target": "verification", "requirement": "repair_audit_inputs", "message": "程序核验拒绝：" + str(exc)}]})
    if differences:
        check.data["passed"] = False
        check.data["issues"].append({"id": "offline-calculation-differences", "severity": "critical", "target": "valuation", "requirement": "repair_calculation_receipts", "message": "离线数学复算、哈希或方法类型不一致：" + ", ".join(differences)})
    engine.state["replay_audit"] = check.model_dump()
    engine._save()
    engine._log("offline_replay", {"source_run": original["run_id"], "calculation_differences": differences,
                                 "scope": state["replay_scope"], "errors": errors, "live_retrieval": False, "remote_model_calls": 0})
    if not check.data["passed"]:
        engine.state["issues"] = list(engine.state["issues"]) + check.data["issues"]
    engine._finish(cancelled=original["status"] == "cancelled")
    return engine, {"first": original["run_id"], "second": state["run_id"], "calculation_equal": not differences,
                    "differences": differences, "errors": errors, "program_audit_passed": check.data["passed"], "remote_model_calls": 0}
