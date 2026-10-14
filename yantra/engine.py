"""Program owned permissions, budgets, durable state and research completion gates."""
from __future__ import annotations
from . import __version__

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .policies import OfflineDemoBackend
from .schemas import Assumptions, Issue, Limits, MainAction, Research, Result, Task
from .tools import ToolRegistry

PERMISSIONS = {
    "finance": ["read_materials", "extract_financials", "convert_units", "calculate_metrics"],
    "thesis": ["read_materials"],
    "valuation": ["valuation"],
    "verification": ["check_consistency"],
}
SECTIONS = ["投资结论摘要", "投资逻辑", "事实与数据基础", "财务分析与估值", "风险因素",
            "反向验证条件", "持续跟踪指标", "市场预期差"]
TERMINAL = {"completed", "partial", "failed", "cancelled"}
TOOL_SPECS = {
    "read_materials": "{company, material_ids?:list[str]}; returns fixed documents with source refs and hashes",
    "extract_financials": "{company, material_ids?:list[str]}; returns financials records with field/value/period/unit/currency/consolidated/source_ref/source_hash",
    "convert_units": "{value,from_unit,to_unit,currency}; deterministic unit conversion",
    "calculate_metrics": "{records:list[FinancialRecord]}; deterministic margins, growth, free cash flow, net debt",
    "valuation": "{model,parameters,basis,assumptions_version}; current confirmed method + parameters + version required; raw methods DCF/PE/PB/EV-EBITDA/DDM; fixed demonstration retains legacy DCF",
    "check_consistency": "{results:dict[artifact,Result],assumptions,research}; original evidence and calculation audit, structured issues",
}


class StopRun(Exception):
    pass


class Engine:
    def __init__(self, directory: Path, state: dict, backend=None):
        self.directory = directory
        self.state = state
        self.backend = backend
        self.tools = ToolRegistry(directory / "materials")
        self.tools.calculations.update(state.get("calculations", {}))

    @staticmethod
    def _backend(mode):
        if mode == "real-model":
            from .backend import RealModelBackend
            return RealModelBackend.from_env()
        return OfflineDemoBackend()

    @classmethod
    def create(cls, storage: Path, research: dict, mode="offline-demo", limits=None, materials_dir=None, backend=None):
        if mode not in ("offline-demo", "real-model", "test"):
            raise ValueError("mode must be offline-demo, real-model or test")
        r = Research.model_validate(research).model_dump()
        date.fromisoformat(r["cutoff"])
        if r["price_time"]:
            price_day = datetime.fromisoformat(r["price_time"].replace("Z", "+00:00")).date()
            if price_day > date.fromisoformat(r["cutoff"]):
                raise ValueError("price time must not be later than research cutoff")
        if r["reference_price"] is not None and (not r["price_time"] or not r.get("price_source")):
            raise ValueError("reference price requires price_time and price_source")
        config = limits if isinstance(limits, Limits) else Limits.model_validate(limits or {})
        source = Path(materials_dir) if materials_dir else Path(__file__).parent / "materials"
        if not (source / "manifest.json").is_file():
            raise FileNotFoundError("materials directory must contain manifest.json")
        # Resolve configuration errors before creating a run directory.
        selected_backend = backend or cls._backend(mode)
        run_id = "run-" + uuid4().hex[:12]
        directory = Path(storage).resolve() / run_id
        directory.mkdir(parents=True)
        shutil.copytree(source, directory / "materials")
        registry = ToolRegistry(directory / "materials")
        state = {
            "run_id": run_id, "status": "running", "mode": mode, "research": r,
            "created_at": datetime.now(timezone.utc).isoformat(), "limits": config.model_dump(),
            "config": selected_backend.config_summary() if hasattr(selected_backend, "config_summary") else {"backend": "injected-test"},
            "program_version": __version__, "input_snapshot": registry.snapshot(),
            "assumptions": Assumptions().model_dump(), "assumption_history": [], "user_overrides": [],
            "tasks": [], "results": [], "artifacts": {}, "calculations": {}, "issues": [],
            "invalidated": [], "report_history": [], "stop_reasons": [], "pending_user": None,
            "in_flight": None, "report": None,
            "budget": {"tool_calls": 0, "model_calls": 0, "main_loops": 0, "repair_rounds": 0,
                       "cost_reserved_usd": 0.0, "active_seconds": 0.0},
        }
        engine = cls(directory, state, selected_backend)
        engine._save()
        engine._log("run_created", {"research": r, "mode": mode, "config": state["config"],
                                    "input_snapshot": state["input_snapshot"], "limits": state["limits"]})
        return engine

    @classmethod
    def load(cls, storage: Path, run_id: str, backend=None):
        if not run_id.startswith("run-") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in run_id):
            raise ValueError("invalid run id")
        directory = Path(storage).resolve() / run_id
        state = json.loads((directory / "state.json").read_text())
        if state.get("raw_materials"):
            from .research import RawEngine
            return RawEngine(directory, state, backend)
        return cls(directory, state, backend)

    @contextmanager
    def _lock(self):
        with (self.directory / ".lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("run is being executed by another process") from exc
            self.state = json.loads((self.directory / "state.json").read_text())
            self.tools.calculations.update(self.state["calculations"])
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _save(self):
        temporary = self.directory / "state.tmp"
        with temporary.open("w") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.directory / "state.json")

    def _log(self, kind, payload):
        with (self.directory / "events.jsonl").open("a") as handle:
            entry = {"run_id": self.state["run_id"], "time": datetime.now(timezone.utc).isoformat(),
                     "kind": kind, "payload": payload, "budget": dict(self.state["budget"])}
            handle.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _stop(self, reason):
        if reason not in self.state["stop_reasons"]:
            self.state["stop_reasons"].append(reason)
            self._log("stop_condition", {"reason": reason})
        self._save()

    def _model_budget(self):
        b, limits = self.state["budget"], self.state["limits"]
        if b["model_calls"] >= limits["max_models"]:
            raise StopRun("模型/决策调用预算耗尽")
        if b["active_seconds"] >= limits["run_seconds"]:
            raise StopRun("运行活动时间预算耗尽")
        reserved = limits["model_cost_usd"] if self.state["mode"] == "real-model" else 0
        if b["cost_reserved_usd"] + reserved > limits["max_cost_usd"]:
            raise StopRun("配置的模型费用预留预算耗尽")
        b["model_calls"] += 1
        b["cost_reserved_usd"] = round(b["cost_reserved_usd"] + reserved, 8)
        self._save()

    async def _decision(self, method, *args, timeout=None):
        if self.backend is None:
            self.backend = self._backend(self.state["mode"])
            summary = self.backend.config_summary()
            if summary != self.state["config"]:
                self._log("resume_model_config", summary)
                self.state["config"] = summary
        self._model_budget()
        started = time.monotonic()
        self.state["in_flight"] = {"kind": "decision", "method": method}
        self._save()
        try:
            from .adk_runtime import execution_scope
            task = args[0] if method == "worker" else None
            with execution_scope(self, task, "decision"):
                return await asyncio.wait_for(getattr(self.backend, method)(*args), timeout=min(timeout or 30, 30))
        finally:
            self.state["budget"]["active_seconds"] += time.monotonic() - started
            self.state["in_flight"] = None
            self._save()

    def _view(self):
        return {k: self.state[k] for k in ("run_id", "research", "artifacts", "assumptions", "issues", "stop_reasons", "budget", "limits")} | {
            "candidate_report": self._report(), "action_contract": "Choose exactly one delegate_task, ask_user or finalize. Use finalize candidate={} to submit the provided candidate_report without repeating it. Nonempty candidate must equal candidate_report. No direct calculations or factual invention.",
            "task_purposes": {"finance": "collect/supplement/repair", "thesis": "analyze/repair", "valuation": "propose/calculate/repair", "verification": "audit"},
        }

    async def advance(self):
        with self._lock():
            if self.state["status"] == "waiting" or self.state["status"] in TERMINAL:
                return self.state
            if self.tools.snapshot() != self.state["input_snapshot"]:
                self._stop("固定输入被修改，资料版本校验失败")
                self._finish()
                return self.state
            if self.state["in_flight"]:
                self._stop("上次运行中断，保留已计费调用；未完成操作不自动重放")
                self.state["in_flight"] = None
                self._finish()
                return self.state
            while self.state["status"] == "running":
                try:
                    if self.state["budget"]["main_loops"] >= self.state["limits"]["max_main_loops"]:
                        raise StopRun("主循环次数上限")
                    self.state["budget"]["main_loops"] += 1
                    action = MainAction.model_validate(await self._decision("main", self._view()))
                    self._log("main_action", action.model_dump())
                    if action.action == "delegate_task":
                        await self._delegate(action)
                    elif action.action == "ask_user":
                        self._pause(action.question)
                    else:
                        if action.candidate and action.candidate != self._report():
                            raise StopRun("候选报告包含未核验的改写；只能提交当前结构化成果")
                        self._finish()
                except StopRun as exc:
                    self._stop(str(exc))
                    self._finish()
                except Exception as exc:
                    # Avoid logging provider exception strings that can contain credentials.
                    code = getattr(exc, "code", "")
                    self._stop("不可恢复错误：" + type(exc).__name__ + ("/" + code if code else ""))
                    self._log("execution_error", {"exception_type": type(exc).__name__, "code": code})
                    self._finish()
                self._save()
            return self.state

    def _make_task(self, action):
        a = self.state["artifacts"]
        role, purpose = action.target, action.purpose
        # A model cannot relabel a repair as fresh collection to reset the cap.
        targeted_issues = [i for i in self.state["issues"] if i["severity"] in ("critical", "major") and i["target"] == role]
        if role != "verification" and targeted_issues:
            purpose = "repair"
        allowed = {"finance": {"collect", "supplement", "repair"}, "thesis": {"analyze", "repair"},
                   "valuation": {"propose", "calculate", "repair"}, "verification": {"audit"}}
        if purpose not in allowed[role]:
            raise StopRun("非法子任务目的")
        if role == "thesis" and not a.get("finance"):
            raise StopRun("投资逻辑任务缺少财务输入")
        if role == "valuation" and not a.get("thesis"):
            raise StopRun("估值任务缺少投资假说")
        if role == "valuation" and purpose != "propose" and not self.state["assumptions"]["confirmed"]:
            raise StopRun("未经当前版本用户确认，禁止正式估值")
        if role == "verification" and not a.get("valuation"):
            raise StopRun("核验缺少估值成果")
        if purpose == "repair":
            open_issues = [i for i in self.state["issues"] if i["severity"] in ("critical", "major") and i["target"] == role]
            if not open_issues:
                raise StopRun("修复任务未对应核验问题")
            if self.state["budget"]["repair_rounds"] >= self.state["limits"]["repair_rounds"]:
                raise StopRun("核验修复达到上限，保留未解决问题")
            self.state["budget"]["repair_rounds"] += 1
            # Preserve the complete issue list as task input; never rely only on model text.
        wanted = {"finance": [], "thesis": ["finance"], "valuation": ["finance", "thesis", "proposal"],
                  "verification": ["finance", "thesis", "proposal", "valuation"]}[role]
        inputs = {k: a[k] for k in wanted if k in a}
        if purpose == "repair":
            inputs["repair_requirements"] = {"issues": open_issues}
        remaining = max(0, self.state["limits"]["total_tools"] - self.state["budget"]["tool_calls"])
        r = self.state["research"]
        return Task(task_id="task-" + uuid4().hex[:12], run_id=self.state["run_id"], target=role,
                    company=r["company"], horizon=r["horizon"], cutoff=r["cutoff"], question=action.question or "完成限定研究任务",
                    input_refs=[v["task_id"] for v in inputs.values() if "task_id" in v], inputs=inputs,
                    assumptions=self.state["assumptions"], authorized_tools=PERMISSIONS[role],
                    completion_conditions=["返回结构化成果", "关键事实附原始位置", "不得递归分派或编造缺失数据"],
                    tool_limit=min(remaining, self.state["limits"]["task_tools"]),
                    time_limit_seconds=min(self.state["limits"]["task_seconds"], max(.001, self.state["limits"]["run_seconds"] - self.state["budget"]["active_seconds"])),
                    cost_limit_usd=max(0, self.state["limits"]["max_cost_usd"] - self.state["budget"]["cost_reserved_usd"]), purpose=purpose)

    async def _delegate(self, action):
        task = self._make_task(action)
        self.state["tasks"].append(task.model_dump())
        self._log("task_dispatched", task.model_dump())
        self._save()
        from .adk_app import run_worker
        result = Result.model_validate(await run_worker(self, task.model_dump()))
        if result.task_id != task.task_id or result.assumptions_version != task.assumptions.version:
            raise StopRun("子任务编号或假设版本不匹配")
        self.state["results"].append(result.model_dump())
        self._log("task_result", result.model_dump())
        a = self.state["artifacts"]
        key = "proposal" if task.target == "valuation" and task.purpose == "propose" else task.target
        if key == "proposal":
            self._validate_parameters(result.data.get("parameters", {}))
            if result.data.get("model") != "dcf":
                raise StopRun("v0.1仅支持所声明的工业企业DCF模型")
        if result.status == "blocked":
            self._stop("子任务受阻：" + "; ".join(result.unresolved))
        if any("全系统工具预算耗尽" in u for u in result.unresolved):
            self._stop("全系统工具预算耗尽")
        previous_artifact = a.get(key)
        if previous_artifact:
            self.state["invalidated"].append(previous_artifact["task_id"])
        a[key] = result.model_dump()
        if key == "proposal" and result.data.get("parameters"):
            current = self.state["assumptions"]
            if current["parameters"] and current["parameters"] != result.data["parameters"]:
                self.state["assumption_history"].append(json.loads(json.dumps(current)))
                current["version"] += 1
                for dependent in ("thesis", "valuation", "verification"):
                    if dependent in a:
                        self.state["invalidated"].append(a.pop(dependent)["task_id"])
                self.state["user_overrides"] = []
                # Record the proposal's accepted version without rewriting historical returns.
                a[key]["assumptions_version"] = current["version"]
            self.state["assumptions"]["parameters"] = result.data["parameters"]
            self.state["assumptions"]["basis"] = result.data.get("basis", {})
            self.state["assumptions"]["confirmed"] = False
        if key == "verification":
            self.state["issues"] = [Issue.model_validate(i).model_dump() for i in result.data.get("issues", [])]
        else:
            if "verification" in a:
                self.state["invalidated"].append(a.pop("verification")["task_id"])
            if task.purpose == "repair" and key in ("finance", "thesis") and "valuation" in a:
                self.state["invalidated"].append(a.pop("valuation")["task_id"])
            previous_metrics = (previous_artifact or {}).get("data", {}).get("metrics", {})
            updated_metrics = result.data.get("metrics", {})
            changed_model_inputs = any(previous_metrics.get(k) != updated_metrics.get(k) for k in ("fcf", "net_debt", "shares"))
            if task.purpose == "repair" and key == "finance" and changed_model_inputs:
                # Source changes can alter model inputs: require a new proposal and confirmation.
                self.state["assumption_history"].append(dict(self.state["assumptions"]))
                self.state["assumptions"]["version"] += 1
                self.state["assumptions"]["confirmed"] = False
                if "proposal" in a:
                    self.state["invalidated"].append(a.pop("proposal")["task_id"])
        self._save()

    def _validate_worker_result(self, task, local, result):
        if result.task_id != task.task_id or result.actual_calls != local["actual_calls"]:
            raise StopRun("子 Agent 返回的任务编号或调用数错误")
        if task.target == "verification":
            receipts = [x["response"] for x in local["tool_outputs"] if x["tool"] == "check_consistency" and x["response"]["ok"]]
            if not receipts:
                raise StopRun("核验 Agent 未执行程序一致性检查")
            automatic = receipts[-1]["data"]
            additional = [Issue.model_validate(i).model_dump() for i in result.data.get("issues", [])]
            issues = automatic["issues"] + [i for i in additional if i not in automatic["issues"]]
            passed = bool(result.data.get("passed")) and automatic["passed"] and not any(i["severity"] in ("critical", "major") for i in issues)
            result.data = result.data | automatic | {"issues": issues, "passed": passed}
            result.evidence_refs = receipts[-1]["evidence_refs"]
            result.calculation_refs = receipts[-1]["calculation_refs"]
            if not result.data["passed"]:
                result.status = "partial"
                result.unresolved = list(dict.fromkeys(result.unresolved + [i["message"] for i in issues]))
        if task.target == "valuation" and task.purpose != "propose" and not any(x["tool"] == "valuation" and x["response"]["ok"] for x in local["tool_outputs"]):
            raise StopRun("估值 Agent 未执行确定性估值计算")
        return result.model_dump()

    def _prepare_worker_tool(self, execution, name, arguments):
        task, local = execution["task"], execution["local"]
        if time.monotonic() - execution["started"] >= task.time_limit_seconds:
            raise StopRun("子任务时间上限")
        if name not in task.authorized_tools or name not in PERMISSIONS[task.target]:
            raise StopRun("子 Agent 请求了未授权工具或递归分派")
        args = dict(arguments)
        if name in ("read_materials", "extract_financials"):
            args["company"] = task.company
            args["cutoff"] = task.cutoff
        if name == "valuation":
            current = self.state["assumptions"]
            if (task.target != "valuation" or task.purpose == "propose" or not current["confirmed"]
                    or args.get("assumptions_version") != current["version"] or args.get("parameters") != current["parameters"]):
                raise StopRun("估值工具只能使用当前已确认假设")
        if name == "check_consistency":
            # Verifier may not hide material from the program checker.
            args = {"results": task.inputs, "assumptions": task.assumptions.model_dump(), "research": self.state["research"],
                    "user_overrides": self.state.get("user_overrides", [])}
        fingerprint = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
        if execution["failures"].get(fingerprint, 0) >= self.state["limits"]["retries"] + 1:
            raise StopRun("同一失败操作已达到两次重试上限")
        if self.state["budget"]["tool_calls"] >= self.state["limits"]["total_tools"]:
            raise StopRun("全系统工具预算耗尽")
        if local["actual_calls"] >= task.tool_limit:
            raise StopRun("子任务工具预算耗尽")
        return args, fingerprint

    def _execute_worker_tool(self, execution, name, arguments):
        task, local = execution["task"], execution["local"]
        args, fingerprint = self._prepare_worker_tool(execution, name, arguments)
        self.state["budget"]["tool_calls"] += 1
        local["actual_calls"] += 1
        self.state["in_flight"] = {"kind": "tool", "task_id": task.task_id, "name": name}
        self._save()
        self._log("tool_started", {"task_id": task.task_id, "name": name, "arguments": args})
        call_start = time.monotonic()
        response = self.tools.execute(name, args).model_dump()
        self.state["budget"]["active_seconds"] += time.monotonic() - call_start
        self.state["calculations"] = self.tools.calculations
        self.state["in_flight"] = None
        local["tool_outputs"].append({"tool": name, "arguments": args, "response": response})
        self._log("tool_result", {"task_id": task.task_id, "name": name, "response": response})
        self._save()
        if not response["ok"]:
            execution["failures"][fingerprint] = execution["failures"].get(fingerprint, 0) + 1
        if name in ("read_materials", "extract_financials"):
            new_refs = set(response["evidence_refs"]) - execution["seen_evidence"]
            execution["no_new_evidence"] = 0 if new_refs else execution["no_new_evidence"] + 1
            execution["seen_evidence"].update(new_refs)
            if execution["no_new_evidence"] >= 2:
                raise StopRun("连续两次取证没有新增有效证据，返回主 Agent 重新判断")
        return response

    async def _work_task(self, taskdict):
        from .adk_app import run_worker_loop

        task = Task.model_validate(taskdict)
        specs = self.tools.tool_contracts()
        local = {"tool_outputs": [], "actual_calls": 0, "research": self.state["research"],
                 "tool_specs": {k: specs[k] for k in task.authorized_tools}}
        execution = {"engine": self, "task": task, "local": local, "failures": {},
                     "no_new_evidence": 0, "seen_evidence": set(), "started": time.monotonic()}
        try:
            return await run_worker_loop(execution)
        except StopRun as exc:
            reason = str(exc)
        except Exception as exc:
            reason = "子 Agent 行动或工具错误：" + type(exc).__name__
        return self._partial_worker_result(task, local, reason)

    def _partial_worker_result(self, task, local, reason):
        # Limited local exit returns observations to the supervisor instead of ending research.
        evidence = sorted({r for o in local["tool_outputs"] for r in o["response"]["evidence_refs"]})
        calculations = sorted({r for o in local["tool_outputs"] for r in o["response"]["calculation_refs"]})
        partial_data = {"observations": local["tool_outputs"]}
        for output in local["tool_outputs"]:
            if not output["response"]["ok"]:
                continue
            if output["tool"] == "extract_financials":
                partial_data.update(output["response"]["data"])
            if output["tool"] == "calculate_metrics":
                partial_data["metrics"] = output["response"]["data"]
            if output["tool"] in ("valuation", "check_consistency"):
                partial_data.update(output["response"]["data"])
        return Result(task_id=task.task_id, status="partial" if evidence or calculations else "blocked",
                      data=partial_data, evidence_refs=evidence,
                      assumptions_version=task.assumptions.version, calculation_refs=calculations,
                      unresolved=[reason], suggested_tasks=[{"target": task.target, "question": "重新判断是否补查：" + reason}],
                      actual_calls=local["actual_calls"]).model_dump()

    def _pause(self, question):
        if not self.state["artifacts"].get("proposal") or not self.state["assumptions"]["parameters"]:
            raise StopRun("确认前必须先提交可审阅的模型参数")
        self.state["status"] = "waiting"
        self.state["pending_user"] = {"question": question, "version": self.state["assumptions"]["version"],
                                      "hypotheses": self.state["artifacts"].get("thesis", {}).get("data", {}).get("hypotheses", []),
                                      "proposal": self.state["artifacts"]["proposal"]["data"],
                                      "notice": "等待明确输入；重启或resume不会默认确认"}
        self._log("user_confirmation_requested", self.state["pending_user"])
        self._save()

    @staticmethod
    def _validate_parameters(p, model="dcf"):
        if model != "dcf":
            raise ValueError("fixed demonstration supports legacy DCF only")
        required = {"base_fcf", "growth_rate", "discount_rate", "terminal_growth", "years", "net_debt", "shares", "currency", "unit"}
        if set(p) != required or p["currency"] != "CNY" or p["unit"] != "million":
            raise ValueError("DCF参数必须完整，金额使用CNY million，股数为million shares")
        if any(isinstance(p[k], bool) or not isinstance(p[k], (int, float)) or not math.isfinite(p[k]) for k in required - {"currency", "unit"}):
            raise ValueError("DCF参数须为有限数值")
        if not 0 < p["discount_rate"] < 1 or not -1 < p["growth_rate"] < 1 or not 0 <= p["terminal_growth"] < p["discount_rate"]:
            raise ValueError("增长率/折现率不满足模型适用范围")
        if p["base_fcf"] <= 0 or p["shares"] <= 0 or int(p["years"]) != p["years"] or not 1 <= p["years"] <= 20:
            raise ValueError("DCF须正现金流、正股数和1至20个整数预测年")

    async def respond(self, decision, parameters=None):
        with self._lock():
            if decision == "cancel":
                self.state["status"] = "cancelled"
                self.state["pending_user"] = None
                self._stop("用户取消")
                self._finish(cancelled=True)
                return self.state
            if decision not in ("confirm", "modify", "reject"):
                raise ValueError("decision must be confirm/modify/reject/cancel")
            if self.state["status"] == "cancelled":
                raise ValueError("cancelled run cannot resume")
            if not self.state["artifacts"].get("proposal"):
                raise ValueError("no proposal available to confirm")
            if decision in ("confirm", "reject") and self.state["status"] != "waiting":
                raise ValueError("run is not awaiting confirmation")
            if decision == "modify":
                if not parameters:
                    raise ValueError("modify requires parameters")
                current = self.state["assumptions"]
                model = parameters.get("model", current.get("model", "dcf"))
                updated = parameters["parameters"] if "parameters" in parameters else current["parameters"] | parameters
                basis = parameters.get("basis", current["basis"] | {k: "用户明确修改并确认" for k in parameters})
                self._validate_parameters(updated, model)
                candidate = Assumptions(model=model, version=current["version"]+1, confirmed=True, parameters=updated, basis=basis, authorization_source="user").model_dump()
                if self.state.get("raw_materials"):
                    self._check_authorization(candidate)
                self.state["assumption_history"].append(json.loads(json.dumps(current)))
                self.state["assumptions"] = candidate
                self.state["user_overrides"] = sorted(set(self.state.get("user_overrides", [])) | set(parameters))
                for k in ("thesis", "valuation", "verification"):
                    artifact = self.state["artifacts"].pop(k, None)
                    if artifact:
                        self.state["invalidated"].append(artifact["task_id"])
                self.state["issues"] = []
                if self.state["report"]:
                    self.state["report_history"].append(self.state["report"])
                self.state["report"] = None
                self.state["stop_reasons"] = []
                self.state["artifacts"]["proposal"] = json.loads(json.dumps(self.state["artifacts"]["proposal"]))
                self.state["artifacts"]["proposal"]["data"]["parameters"] = updated
                self.state["artifacts"]["proposal"]["data"]["model"] = model
                self.state["artifacts"]["proposal"]["assumptions_version"] = self.state["assumptions"]["version"]
                self.state["artifacts"]["proposal"]["data"]["basis"] = self.state["assumptions"]["basis"]
            if decision == "reject":
                self.state["pending_user"] = None
                self._log("user_decision", {"decision": decision, "version": self.state["assumptions"]["version"]})
                self._stop("用户拒绝估值模型，未执行正式估值")
                self._finish()
                return self.state
            if decision == "confirm":
                self.state["assumptions"]["confirmed"] = True
                self.state["assumptions"]["authorization_source"] = "user"
            self.state["status"] = "running"
            self.state["pending_user"] = None
            self._log("user_decision", {"decision": decision, "parameters": parameters, "version": self.state["assumptions"]["version"]})
            self._save()
        return await self.advance()

    def _report(self):
        a = self.state["artifacts"]
        data = {k: v["data"] for k, v in a.items()}
        finance, thesis, valuation = data.get("finance", {}), data.get("thesis", {}), data.get("valuation", {})
        assumptions = self.state["assumptions"]
        gaps = sorted(set(self.state["stop_reasons"] + [u for r in a.values() for u in r.get("unresolved", [])] +
                          ["未解决冲突：" + c for r in a.values() for c in r.get("conflicts", [])] + self.state["input_snapshot"]["manifest"].get("gaps", []) +
                          finance.get("gaps", []) + thesis.get("gaps", []) + data.get("proposal", {}).get("gaps", [])))
        return {
            "投资结论摘要": {"type": "judgment", "statement": "证据不足以形成实盘投资结论；保留条件性研究假说。",
                               "company": self.state["research"]["company"], "mode": self.state["mode"], "gaps": gaps},
            "投资逻辑": {"claims": thesis.get("claims", []), "facts": [x for x in thesis.get("claims", []) if x.get("kind", x.get("type")) == "fact"], "assumptions": thesis.get("hypotheses", [])},
            "事实与数据基础": {"financials": finance.get("financials", []), "evidence_refs": a.get("finance", {}).get("evidence_refs", []),
                                 "research": self.state["research"], "input_version": self.state["input_snapshot"]},
            "财务分析与估值": {"historical_metrics": finance.get("metrics", {}), "model": data.get("proposal", {}),
                               "assumptions": assumptions, "forecast": valuation,
                               "calculation_refs": a.get("valuation", {}).get("calculation_refs", []),
                               "notice": "预测值依赖已确认假设；程序计算不构成收益保证"},
            "风险因素": thesis.get("risks", []) or ["研究材料不足"],
            "反向验证条件": thesis.get("disconfirming", []) or ["暂无足够材料定义，待补查"],
            "持续跟踪指标": thesis.get("tracking", []) or ["财务资料、价格与市场基准"],
            "市场预期差": thesis.get("expectations", {"status": "unverified_hypothesis", "baseline": None, "statement": "缺少市场比较基准，证据不足"}),
        }

    def _hard_checks(self, report):
        a, r, assumptions = self.state["artifacts"], self.state["research"], self.state["assumptions"]
        finance = a.get("finance", {}).get("data", {})
        valuation = a.get("valuation", {})
        verifier = a.get("verification", {}).get("data", {})
        thesis = a.get("thesis", {}).get("data", {})
        records = finance.get("financials", [])
        referenced = [x for result in a.values() for x in result.get("calculation_refs", [])]
        expectations = thesis.get("expectations", {})
        if not isinstance(expectations, dict):
            expectations = {}
        return {
            "company_horizon_cutoff": bool(r["company"] and r["horizon"] and r["cutoff"]),
            "reference_price_time_source": bool(r["reference_price"] and r["price_time"] and r.get("price_source")),
            "financial_sources_and_scope": bool(records) and all(all(k in x and x[k] is not None for k in ("source_ref", "source_hash", "period", "unit", "currency", "consolidated")) for x in records) and bool(verifier.get("passed")),
            "calculation_receipts": bool(referenced) and all(x in self.state["calculations"] for x in referenced),
            "current_confirmed_valuation": bool(assumptions["confirmed"] and valuation and valuation.get("assumptions_version") == assumptions["version"]),
            "facts_and_assumptions_separated": bool(thesis.get("claims") and thesis.get("hypotheses")) and all(x.get("kind", x.get("type")) in ("fact", "management_statement", "assumption", "prediction", "judgment") for x in thesis.get("claims", [])) and all(x.get("kind", x.get("type")) in ("assumption", "prediction", "judgment") for x in thesis.get("hypotheses", [])),
            "risks_and_disconfirmation": bool(thesis.get("risks") and thesis.get("disconfirming") and thesis.get("tracking")),
            "market_baseline_or_explicit_gap": bool(expectations.get("baseline") or (expectations.get("status") == "unverified_hypothesis" and expectations.get("statement"))),
            "eight_sections": list(report) == SECTIONS,
            "no_hidden_conflicts": not any(v.get("conflicts") for v in a.values()),
            "verification_passed": bool(verifier.get("passed")) and not any(i["severity"] in ("critical", "major") for i in self.state["issues"]),
            "no_important_gaps": not any(v.get("unresolved") or v.get("data", {}).get("gaps") for v in a.values()) and not self.state["stop_reasons"] and not self.state["input_snapshot"]["manifest"].get("gaps"),
        }

    def _finish(self, cancelled=False):
        report = self._report()
        checks = self._hard_checks(report)
        status = "cancelled" if cancelled else "completed" if all(checks.values()) else "partial" if self.state["artifacts"].get("finance", {}).get("data", {}).get("financials") else "failed"
        self.state.update(status=status, pending_user=None, report=report, hard_checks=checks)
        self._log("finalize_check", {"hard_checks": checks, "status": status,
                                     "business_review": "核验 Agent 的逻辑判断仍需业务判断；程序不能证明全部投资论证正确", "issues": self.state["issues"]})
        version = self.state["assumptions"]["version"]
        for name, content in ((f"report-v{version}.json", report), (f"calculations-v{version}.json", self.state["calculations"])):
            (self.directory / name).write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
        from .reporting import memo_markdown
        report_path = self.directory / f"report-v{self.state['assumptions']['version']}.md"
        report_path.write_text(memo_markdown(self.state, self.directory))
        self.state["report_path"] = str(report_path)
        self._save()
        from .adk_artifacts import publish_outputs
        publish_outputs(self)


def create_adk_engine(company="DEMO", run_id=None):
    storage = Path(os.getenv("YANTRA_RUNS_DIR", "runs"))
    if run_id:
        return Engine.load(storage, run_id)
    mode = os.getenv("YANTRA_MODE", "real-model")
    if mode == "offline-demo":
        return Engine.create(storage, {"company": company, "horizon": "12个月", "cutoff": "2025-12-31"},
                             mode="offline-demo", materials_dir=os.getenv("YANTRA_MATERIALS_DIR") or None)
    if mode != "real-model":
        raise ValueError("YANTRA_MODE must be real-model or explicit offline-demo")
    from .research import RawEngine
    from .configuration import ConfigStore
    inputs = json.loads(os.getenv("YANTRA_RAW_MATERIALS", "[]"))
    if not isinstance(inputs, list) or any(not isinstance(item, str) for item in inputs):
        raise ValueError("YANTRA_RAW_MATERIALS must be a list of input paths or URLs")
    if not inputs and os.getenv("YANTRA_MATERIALS_DIR"):
        inputs = [os.environ["YANTRA_MATERIALS_DIR"]]
    return RawEngine.create_raw(storage, {"company": company, "horizon": os.getenv("YANTRA_HORIZON", "12个月"),
        "cutoff": os.getenv("YANTRA_CUTOFF", datetime.now(timezone.utc).isoformat()),
        "market": os.getenv("YANTRA_MARKET") or None, "security_code": os.getenv("YANTRA_SECURITY_CODE") or None,
        "exchange": os.getenv("YANTRA_EXCHANGE") or None, "trading_currency": os.getenv("YANTRA_TRADING_CURRENCY") or None,
        "company_id": os.getenv("YANTRA_COMPANY_ID") or None,"share_class":os.getenv("YANTRA_SHARE_CLASS") or None,
        **({"confirmation_mode": os.environ["YANTRA_CONFIRMATION_MODE"]} if "YANTRA_CONFIRMATION_MODE" in os.environ else {})},
        inputs, framework=os.getenv("YANTRA_FRAMEWORK", "auto"),settings=ConfigStore(),
        knowledge_dir=os.getenv("YANTRA_KNOWLEDGE_DIR",".yantra/knowledge"),
        explicit_parameters={k:v for k,v in {"horizon":os.getenv("YANTRA_HORIZON"),"framework":os.getenv("YANTRA_FRAMEWORK"),
                                           "confirmation_mode":os.getenv("YANTRA_CONFIRMATION_MODE")}.items() if v is not None})
