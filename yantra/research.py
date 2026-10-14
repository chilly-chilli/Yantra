"""v0.2 raw-material research, using the existing ADK controller and workers."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import uuid4
from . import __version__

from .backend import MAIN_INSTRUCTION, WORKER_INSTRUCTION, RealModelBackend
from .budget import BudgetExceeded, MAX_RUN_SECONDS, RunBudget, deadline
from .engine import Engine, StopRun, TERMINAL
from .frameworks import FrameworkStore
from .materials import RawMaterialAdapter, cutoff_time, research_chunks
from .schemas import Assumptions, ChildArguments, CollaborationArguments, Limits, MainAction, Pricing, ReadArguments, Research, Result, Task, ThesisData, ValuationProposal
from .v2_tools import RawToolRegistry

RAW_PERMISSIONS = {
    "finance": ["read_materials", "extract_financials", "convert_units", "calculate_metrics", "search_public", "fetch_source", "spawn_subtask", "collaborate"],
    "thesis": ["read_materials", "search_public", "fetch_source", "spawn_subtask", "collaborate"],
    "valuation": ["valuation", "read_materials", "spawn_subtask", "collaborate"],
    "verification": ["check_consistency", "read_materials", "search_public", "fetch_source", "spawn_subtask", "collaborate"],
}

RAW_MAIN = MAIN_INSTRUCTION + """
本轮为原始材料研究，按 raw-v2 规则覆盖旧演示要求：默认自主模式，无需 ask_user；人工模式才等待用户确认。
主Agent可approve_transfer（给transfer_id），revise_framework（给framework_text及rationale）或revise_standard（给standard_changes及rationale）。转派只有批准后才执行；框架修订经独立核验后供以后运行选择，本次始终固定快照。标准修改不得移除程序保障。
框架及主体证券见输入。每次都已主动独立检索，还可按缺口补查。估值提案 model 可以为 not_applicable、parameters={}，说明真实FCFF口径、正现金流、周期性、股数或价格缺口；此时继续核验及研究，不能拒绝整项研究。
核验无需强制存在估值。有财务及定性证据时可交付 partial。主框架选择写入结果；不强迫不适用公司做DCF。
一旦finance有可定位财务记录及定性evidence，应进入thesis；关键缺口可以保留partial，不以补齐全部旧演示字段为下一阶段条件。
补查须改变查询、页段或来源。相同材料没有新增有效结果时，不能重复同一财务任务；转入已有证据的论证、适用性判断及独立核验。
知识submission_issues若显示旧基准冲突，研究角色须按当前knowledge_context基准重新协调提议并交独立核验，不能覆盖新版本。被否决的提议保留理由，不用重复提交绕过否决。
"""
RAW_WORKER = WORKER_INSTRUCTION + """
raw-v2 覆盖旧演示要求：可以请求 spawn_subtask 在父授权范围内动态创建第三层角色，role 用任务需要的自由文本；第三层禁止继续创建。通信及读取用 collaborate；跨Agent新任务先 request_transfer，等待主Agent内部批准，不可当作消息执行。
finance: extract_financials 已由预算内模型依据原始 anchors 提取，返回财务和 evidence，保留其 gaps/conflicts。部分字段仍计算可用指标，不为凑齐旧演示字段编数据。
finance可用result.data={"use_receipts":true}返回，由控制器从本任务成功的extract_financials和calculate_metrics原样组装底表、证据及指标，避免重复输出大表。actual_calls仍必须准确。
read_materials与extract_financials可指定query（如合并现金流量表/consolidated statement of cash flows）或anchor_refs定向阅读原文，material_ids限定文档；缺口补查使用新查询而非重复默认选区。
thesis: 分析具体经营、竞争和风险证据，引用实际材料；管理层陈述附主体，预测不可称历史事实；result.data 可增加 summary 形成证据支持的研究摘要。
thesis的expectations必须是对象（含status、baseline、statement），不能是列表；claims/hypotheses为对象列表，risks/disconfirming/tracking/gaps为字符串列表。缺口用约定字段，不能改变接口形状。
valuation propose: model可为dcf/pe/pb/ev_ebitda/ddm/not_applicable。先按方法检查来源、身份、币种、期间、股本类别、合并/归母口径及截止时点。PE/PB以每股EPS/BVPS乘同类同期间同日可比倍数；EV/EBITDA须明确净债务、少数股东权益和总股数才能发布每股结论；DDM为年度D0*(1+g)/(k-g)。新方法parameters按MethodInputs合约（见valuation工具description），每项来源保存FinancialRecord或MultipleObservation；basis解释参数与相关论点。框架与方法为两个维度。不适用时not_applicable并保留缺口；不能强制绕过条件、补造证据或把PB叫NAV。市场基准缺失保持unresolved。
autonomous 的当前假设可由控制器授权，basis 每项含出处、口径及假设理由；manual 仍明确确认。
verification: 程序一致性通过仍要独立审查经济判断、财务口径、事实与预测、风险及方法适用性。可否决 passed，不能删除程序问题；否决须写具体 Issue。不同公司能形成不同摘要。
v0.3 knowledge_search/read 提供历史线索，knowledge_propose 只提议；knowledge_review 仅独立核验角色可用。历史论点不能作为本次已确认结论，问题获准只表示有核查价值。事实复用必须检查哈希、期间、证券、币种及口径。比较业务和集团指标时不得跨口径推出论点状态；先固定本次判断，再比较、补查、修订和独立核验。重点条目记录R1-R4及具体理由。
"""


class RawBackend(RealModelBackend):
    async def main(self, payload):
        return await self._decision("research_coordinator", RAW_MAIN, MainAction, payload)

    async def worker(self, task, local):
        from .schemas import WorkerAction, ThesisAction, ProposalAction
        schema = ThesisAction if task["target"] == "thesis" and task["depth"] == 2 else ProposalAction if task["target"] == "valuation" and task["purpose"] == "propose" else WorkerAction
        return await self._decision("research_worker", RAW_WORKER, schema, {"task": task, "local": local})

    async def review_knowledge(self, payload):
        from .knowledge.schemas import ReviewBatch
        return await self._decision("knowledge_verifier", "独立核验逐条提议的原文、提取口径、推理、假设、反证及问题价值。每条返回准确proposal_id/content_hash、passed和理由；预测不能成为历史事实，未知问题答案不通过。无充分依据否决，不能因归档或全报告核验而批准。", ReviewBatch, payload)

    async def compare_knowledge(self, payload):
        from .knowledge.schemas import ComparisonBatch, KnowledgeItem
        return await self._decision("historical_comparison", "当前判断已固定，逐条比较历史关键论点及未关闭问题。记录支持/削弱/反驳/范围或假设变化/不足，来源必须来自本次证据。业务与集团指标分开；不得用不同期间和口径直接推翻或证实旧论点。给出补查任务，不声称状态已通过核验。", ComparisonBatch, payload)


def pricing_from_env():
    try:
        return Pricing(input_per_million=float(os.environ["MODEL_INPUT_CNY_PER_MILLION"]),
                       output_per_million=float(os.environ["MODEL_OUTPUT_CNY_PER_MILLION"]),
                       source=os.environ["MODEL_PRICE_SOURCE"], as_of=os.environ["MODEL_PRICE_AS_OF"]).model_dump()
    except (KeyError, ValueError) as exc:
        raise ValueError("真实研究需配置人民币输入/输出单价、价格来源和日期（或 --pricing JSON）；不使用固定每调用费用") from exc


def decision_context(value):
    if isinstance(value, dict):
        result = {k: decision_context(v) for k, v in value.items() if not (k == "text" and "lines" in value)}
        if isinstance(value.get("observations"), list):
            result["observations"] = [{"tool": o["tool"], "ok": o["response"]["ok"], "evidence_refs": o["response"].get("evidence_refs", [])} for o in value["observations"]]
        return result
    if isinstance(value, list):
        return [decision_context(x) for x in value]
    return value


class RawEngine(Engine):
    permissions = RAW_PERMISSIONS

    @contextmanager
    def _lock(self):
        with super()._lock():
            self.run_budget.state = self.state
            self.tools.research_context = self.state["research"]
            if getattr(self, "knowledge", None):
                self.knowledge.refresh()
            yield

    def __init__(self, directory, state, backend=None):
        self.directory, self.state, self.backend = Path(directory), state, backend
        self.tools = RawToolRegistry(self.directory / "materials")
        self.tools.calculations.update(state.get("calculations", {}))
        self.run_budget = RunBudget(self.state, self._save)
        self.run_budget.state_path = self.directory
        from .extensions import ExtensionRuntime
        self.extensions = ExtensionRuntime(state.get("capabilities",{}).get("extensions",{}))
        self.permissions = {role:list(tools)+self.extensions.permissions(role) for role,tools in RAW_PERMISSIONS.items()}
        self.tools.extension_contracts = self.extensions.contracts()
        skills = state.get("capabilities",{}).get("extensions",{}).get("skills",[])
        if skills:
            self.tools.extension_contracts["read_skill_resource"] = {"description":"读取当前运行已冻结且本角色获准的技能文本资源。",
                "arguments_schema":{"type":"object","properties":{"skill":{"type":"string"},"path":{"type":"string"}},"required":["skill","path"],"additionalProperties":False}}
            for role in self.permissions:
                if any(role in s["roles"] for s in skills):self.permissions[role].append("read_skill_resource")
        profile = state.get("capabilities",{}).get("models",{}).get("ocr")
        if profile:
            from .models import OCRRecognizer
            self.tools.adapter.recognizer = OCRRecognizer(profile,self)
        self.tools.adapter.check = self._check_adapter_budget
        self.tools.research_context = state["research"]
        self.tools.adapter.timeout = lambda: min(45, self.run_budget.remaining())
        self.knowledge = None
        if state.get("knowledge"):
            from .knowledge.runtime import ResearchKnowledge, CONTRACTS
            self.knowledge = ResearchKnowledge(self)
            self.tools.extension_contracts.update(CONTRACTS)
            for role in self.permissions:
                self.permissions[role].extend(["knowledge_search", "knowledge_read"])
                self.permissions[role].append("knowledge_review" if role == "verification" else "knowledge_propose")
        from .adk_app import build_workers
        self.adk_workers = build_workers(self)

    def _check_adapter_budget(self):
        self.run_budget.check()
        from .adk_app import _worker_execution
        execution = _worker_execution.get()
        if execution and time.monotonic() - execution["started"] >= execution["task"].time_limit_seconds:
            raise BudgetExceeded("材料获取／解析达到父任务截止时点")

    @classmethod
    def create_raw(cls, storage, research, inputs, limits=None, pricing=None, backend=None, framework="auto", framework_file=None,
                   settings=None, llm_profile=None, ocr_profile=None, embedding_profile=None, knowledge_dir=None, explicit_parameters=None):
        r = Research.model_validate(research).model_dump()
        if r["market"] and r["security_code"]:
            code = r["security_code"]
            if not code.isdigit() or (r["market"] == "CN" and len(code) != 6) or (r["market"] == "HK" and not 1 <= len(code) <= 5):
                raise ValueError("CN证券代码须六位数字；HK须一至五位数字")
            if r["market"] == "HK":
                r["security_code"] = code.zfill(5)
        cutoff_time(r["cutoff"])
        if r["reference_price"] and not (r["price_time"] and r["price_source"] and r["trading_currency"]):
            raise ValueError("reference price requires timestamp, currency, security and source")
        if r["price_time"] and cutoff_time(r["price_time"]) > cutoff_time(r["cutoff"]):
            raise ValueError("price is after research cutoff")
        config = Limits.model_validate({"total_tools": 100, "task_tools": 16, "max_models": 160, "max_main_loops": 40,
            "run_seconds": MAX_RUN_SECONDS, "task_seconds": 240, "repair_rounds": 4, **(limits or {})})
        started = time.time()
        end = deadline(started, config.run_seconds)
        from .models import ModelCatalog,create_backend
        catalog=ModelCatalog(settings) if settings is not None else None
        chosen_llm=catalog.resolve("llm",llm_profile) if catalog else None
        chosen_ocr=catalog.resolve("ocr",ocr_profile) if catalog else None
        chosen_embedding=catalog.resolve("embedding",embedding_profile) if catalog else None
        selected = backend or (create_backend(chosen_llm,RawBackend) if chosen_llm else RawBackend.from_env(timeout_seconds=90))
        price = Pricing.model_validate(pricing or (chosen_llm or {}).get("pricing") or pricing_from_env()).model_dump()
        if not inputs:
            raise ValueError("provide --material file/directory/URL; no manifest required")
        run_id = "run-" + uuid4().hex[:12]
        directory = Path(storage).resolve() / run_id
        directory.mkdir(parents=True)
        RawMaterialAdapter.create(directory / "materials",r["company"],[])
        registry = RawToolRegistry(directory / "materials")
        from .extensions import ExtensionCatalog
        extensions=ExtensionCatalog(settings).snapshot(directory) if settings else {}
        if chosen_llm is None and backend is None:
            chosen_llm={"name":"env","kind":"llm","provider":"openai-compatible","model_id":selected.model_id,
                        "base_url":os.getenv("MODEL_BASE_URL",""),"api_key_env":"MODEL_API_KEY","pricing":price}
        framework_store = FrameworkStore(Path(storage).resolve() / "frameworks")
        state = {"run_id": run_id, "status": "running", "mode": "real-model" if backend is None else "test",
            "raw_materials": True, "research": r, "created_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "valuation_schema": 2,
            "deadline": end, "limits": config.model_dump(), "pricing": price, "model_ledger": [],
            "program_version": __version__, "config": selected.config_summary() if hasattr(selected, "config_summary") else {"backend": "injected-test"},
            "capabilities":{"models":{"llm":chosen_llm,"ocr":chosen_ocr,"embedding":chosen_embedding},"extensions":extensions},
            "knowledge":{"root":str(Path(knowledge_dir or Path(storage)/"knowledge").resolve()),"archived":{},"proposals":[],"startup":[],"fixed_refs":[],"snapshots":[],"comparisons":[],"reused":[]},
            "explicit_parameters": explicit_parameters if explicit_parameters is not None else {"horizon":r["horizon"],"framework":framework},
            "input_source_ids": [m["id"] for m in registry.load_manifest()["materials"]],
            "input_snapshot": registry.snapshot(), "assumptions": Assumptions().model_dump(), "assumption_history": [], "user_overrides": [],
            "tasks": [], "results": [], "artifacts": {}, "calculations": {}, "issues": [], "invalidated": [], "report_history": [],
            "stop_reasons": [], "pending_user": None, "in_flight": None, "report": None,
            "framework": framework_store.builtin("quality" if framework == "auto" else framework), "framework_auto": framework == "auto", "framework_history": [], "framework_file": str(Path(framework_file).resolve()) if framework_file else None,
            "framework_original": Path(framework_file).read_text() if framework_file else None,
            "standard_version": 1, "standard_history": [], "transfers": [], "messages": [], "searches": [], "prepared": False,
            "budget": {"tool_calls": 0, "model_calls": 0, "main_loops": 0, "repair_rounds": 0, "cost_reserved_usd": 0,
                       "active_seconds": 0, "model_cost_cny": 0, "external_service_cost_cny": 0}}
        engine = cls(directory, state, selected)
        engine.knowledge.initialize()
        from .knowledge.preferences import Preferences
        defaults, applications = Preferences(engine.knowledge.store).resolve(state["knowledge"]["company_id"], state["explicit_parameters"],
            {"horizon":r["horizon"],"framework":framework,"questions":r["questions"],"topics":r["topics"]},run_id)
        state["preference_applications"] = applications
        from .knowledge.schemas import digest
        state["preference_signature"] = digest(Preferences(engine.knowledge.store).list())
        state["preference_defaults"] = {"horizon":r["horizon"],"framework":framework,"questions":r["questions"],"topics":r["topics"]}
        engine._set_requirements(defaults, initial=True)
        engine._save()
        try:
            RawMaterialAdapter.create(directory/"materials",r["company"],inputs,engine._check_adapter_budget,engine.tools.adapter.recognizer,engine.knowledge.cached_parse)
        except BudgetExceeded as exc:
            state["stop_reasons"].append(str(exc))
            engine.tools.adapter.gap(str(exc))
        state["input_source_ids"]=[m["id"] for m in engine.tools.load_manifest()["materials"]]
        state["input_snapshot"]=engine.tools.snapshot()
        engine.knowledge.archive_sources(closing=bool(state["stop_reasons"]))
        engine._save()
        engine._log("run_created", {"research": r, "limits": state["limits"], "deadline": end, "pricing": price})
        return engine

    async def _decision(self, method, *args, timeout=None):
        if self.backend is None:
            from .models import create_backend
            profile=self.state.get("capabilities",{}).get("models",{}).get("llm")
            self.backend = create_backend(profile,RawBackend) if profile else RawBackend.from_env(timeout_seconds=90)
        skills=self.state.get("capabilities",{}).get("extensions",{}).get("skills",[])
        self.backend.runtime_skills=skills
        if isinstance(self.backend, RealModelBackend):
            self.backend.max_output_tokens = 8192 if method == "extract" else 4096
        if self.state["budget"]["model_calls"] >= self.state["limits"]["max_models"]:
            raise StopRun("模型决策次数上限")
        if method == "worker":
            task, local = args
            local["inbox"] = [m for m in self.state["messages"] if m["target_task_id"] == task["task_id"]]
            local["shared_results"] = [r for r in self.state["results"] if r["task_id"] in task["shared_refs"]]
        args = tuple(decision_context(x) for x in args)
        instruction = RAW_MAIN if method == "main" else RAW_WORKER if method == "worker" else str(method)
        payload = json.dumps(args, ensure_ascii=False, default=str)
        receipt = self.run_budget.reserve(payload, instruction + json.dumps(self.tools.tool_contracts())+json.dumps(skills,ensure_ascii=False), getattr(self.backend, "max_output_tokens", 4096))
        from .adk_app import _worker_execution
        execution = _worker_execution.get()
        receipt.update(method=method, task_id=execution["task"].task_id if execution else "main",
                       framework_version=self.state["framework"]["version"], assumptions_version=self.state["assumptions"]["version"])
        self.state["budget"]["model_calls"] += 1
        self.state["in_flight"] = {"kind": "decision", "method": method, "reservation": receipt["id"]}
        self._save()
        try:
            remaining = min(timeout or 90, 90, self.run_budget.remaining())
            from .adk_runtime import execution_scope
            with execution_scope(self, execution["task"] if execution else None, "decision"):
                return await asyncio.wait_for(getattr(self.backend, method)(*args), remaining)
        finally:
            self.run_budget.settle(receipt, getattr(self.backend, "last_usage", None))
            receipt["diagnostics"] = getattr(self.backend, "last_diagnostics", {})
            self.state["in_flight"] = None
            self.state["budget"]["active_seconds"] = time.time() - datetime.fromisoformat(self.state["created_at"]).timestamp()
            self._save()

    def _view(self):
        return super()._view() | {"framework": self.state["framework"], "standard_version": self.state["standard_version"],
            "extension_tools":self.extensions.contracts(),
            "raw_v2": True, "identity": self.state["research"], "transfer_requests": [r for r in self.state["transfers"] if r["status"] == "requested"],
            "knowledge":{k:self.state.get("knowledge",{}).get(k) for k in ("company_id","security_id","startup","startup_notice","snapshots","comparisons","reused","submission_issues")}, "user_requirements":self.state.get("requirement_notes","")}

    async def advance(self):
        # prepare itself is durable and uses the same run lock/deadline/budget.
        if self.state["status"] in TERMINAL:
            return self.state
        try:
            with self._lock():
                self.run_budget.state = self.state
                self.run_budget.check()
                if self.state.get("normalization_policy") != "raw-unit-v2":
                    aggregate = {"financials": [], "evidence": [], "gaps": []}
                    with (self.directory / "events.jsonl").open() as events:
                        for line in events:
                            self.run_budget.check()
                            event = json.loads(line)
                            if event["kind"] == "model_extraction":
                                for key in aggregate:
                                    aggregate[key].extend(x for x in event["payload"]["response"].get(key, []) if x not in aggregate[key])
                    if aggregate["financials"] or aggregate["evidence"]:
                        if self.state["budget"]["tool_calls"] >= self.state["limits"]["total_tools"]:
                            raise StopRun("全系统工具预算耗尽")
                        self.state["budget"]["tool_calls"] += 1
                        accepted = self.tools.accept_extraction(aggregate, self.state["research"]["cutoff"])
                        finance = self.state["artifacts"].get("finance")
                        if finance and accepted["financials"]:
                            finance["data"].update(accepted)
                            finance["data"].pop("metrics", None)
                            finance["evidence_refs"] = sorted({x["source_ref"] for x in accepted["financials"] + accepted["evidence"]})
                            for key in ("thesis", "proposal", "valuation", "verification"):
                                old = self.state["artifacts"].pop(key, None)
                                if old:
                                    self.state["invalidated"].append(old["task_id"])
                        self._log("raw_unit_normalization_revalidated", {"financials": len(accepted["financials"]), "model_calls": 0})
                    self.state["normalization_policy"] = "raw-unit-v2"
                    self._save()
                if not self.state.get("input_fetch_retry_done"):
                    self.state["input_fetch_retry_done"] = True
                    index = self.tools.load_manifest()
                    import re
                    for gap in list(index["gaps"]):
                        match = re.search(r"https?://\S+", gap)
                        if not gap.startswith("获取输入链接失败") or not match:
                            continue
                        url = match.group(0).rstrip(":")
                        self.state["budget"]["tool_calls"] += 1
                        if self.state["budget"]["tool_calls"] > self.state["limits"]["total_tools"]:
                            raise StopRun("全系统工具预算耗尽")
                        try:
                            entry = await asyncio.to_thread(self.tools.adapter.fetch, url)
                            self._log("input_source_recovered", {"url": url, "source_id": entry["id"]})
                        except Exception as exc:
                            self._log("input_source_retry_failed", {"url": url, "error_type": type(exc).__name__})
                    self.state["input_snapshot"] = self.tools.snapshot()
                    self._save()
                if not self.state["prepared"]:
                    await self._prepare()
                    self.state["prepared"] = True
                    self._save()
                if not self.state.get("input_extraction_done"):
                    identifiers = self.state.get("input_source_ids")
                    if identifiers is None:
                        # Earlier development snapshots predate the input-ID field.
                        identifiers = [m["id"] for m in self.tools.load_manifest()["materials"] if m["file"].endswith(".pdf")][:1]
                    if identifiers:
                        await self._extract(self.state["research"]["cutoff"], {"company": self.state["research"]["company"], "cutoff": self.state["research"]["cutoff"], "material_ids": identifiers})
                    self.state["input_extraction_done"] = True
                    self._save()
        except (BudgetExceeded, StopRun) as exc:
            self._stop(str(exc))
            self._finish(cancelled=str(exc) == "用户取消")
            return self.state
        except Exception as exc:
            self._stop("准备阶段错误：" + type(exc).__name__)
            self._finish()
            return self.state
        with self._lock():
            if self.state["status"] == "waiting":
                try:
                    self.run_budget.check()
                except BudgetExceeded as exc:
                    self._stop(str(exc))
                    self._finish()
                return self.state
            if self.state["in_flight"]:
                self._stop("上次操作中断，保留预留费用；未完成操作不自动重放")
                self.state["in_flight"] = None
                self._finish()
                return self.state
            while self.state["status"] == "running":
                try:
                    self.run_budget.check()
                    self._apply_requirements()
                    self._apply_pending_parameters()
                    if self.state["budget"]["main_loops"] >= self.state["limits"]["max_main_loops"]:
                        raise StopRun("主循环次数上限")
                    self.state["budget"]["main_loops"] += 1
                    action = MainAction.model_validate(await self._decision("main", self._view()))
                    self._log("main_action", action.model_dump())
                    if action.action == "delegate_task":
                        finance = self.state["artifacts"].get("finance", {}).get("data", {})
                        if action.target == "finance" and action.purpose != "repair" and finance.get("financials") and finance.get("evidence") and not self.state["artifacts"].get("thesis"):
                            action = MainAction(action="delegate_task", target="thesis", purpose="analyze", question="基于已有可定位财务及经营证据形成论点、风险与反证，保留财务缺口；按需要独立补查", rationale="已有事实基础，进入论证并保留partial缺口")
                        if action.target == "verification" and self.state["artifacts"].get("thesis") and not self.state["artifacts"].get("proposal"):
                            action = MainAction(action="delegate_task", target="valuation", purpose="propose", question="按来源与口径评估DCF、PE、PB、EV/EBITDA、DDM；不足时提交not_applicable及依据，继续独立核验", rationale="核验前保留可核查的方法适用性判断")
                        await self._delegate(action)
                    elif action.action == "ask_user":
                        self._pause(action.question)
                    elif action.action == "approve_transfer":
                        await self._approve_transfer(action)
                    elif action.action == "revise_framework":
                        if not action.framework_text or not action.rationale:
                            raise StopRun("框架修订缺少规则及理由")
                        rules = await self._decision("framework", {"original": action.framework_text})
                        store = FrameworkStore(self.directory.parent / "frameworks")
                        self.state["framework_draft"] = store.draft(rules, action.framework_text, self.state["framework"]["version"], action.rationale, self.state["run_id"])
                        self._log("framework_draft", self.state["framework_draft"])
                    elif action.action == "revise_standard":
                        if not action.standard_changes or not action.rationale:
                            raise StopRun("标准修订缺少具体标准及理由")
                        self.state["standard_history"].append({"version": self.state["standard_version"], "changes": action.standard_changes, "reason": action.rationale})
                        self.state["standard_version"] += 1
                        for key in ("thesis", "valuation", "verification"):
                            old = self.state["artifacts"].pop(key, None)
                            if old:
                                self.state["invalidated"].append(old["task_id"])
                    else:
                        if action.candidate and action.candidate != self._report():
                            raise StopRun("候选报告包含未核验改写")
                        if not self.state["stop_reasons"] and self.state["artifacts"].get("thesis"):
                            if not self.state["artifacts"].get("proposal"):
                                await self._delegate(MainAction(action="delegate_task", target="valuation", purpose="propose", question="提交方法适用性判断；无可靠估值路径时明确not_applicable及缺口"))
                                self._save()
                                continue
                            if not self.state["artifacts"].get("verification"):
                                await self._delegate(MainAction(action="delegate_task", target="verification", purpose="audit", question="独立核验本次证据、论点、方法及缺口；必要时否决"))
                                self._save()
                                continue
                        self._finish()
                except (BudgetExceeded, StopRun) as exc:
                    self._stop(str(exc))
                    self._finish(cancelled=str(exc) == "用户取消")
                except Exception as exc:
                    self._stop("执行错误：" + type(exc).__name__ + "/" + str(getattr(exc, "code", "")))
                    self._finish()
                self._save()
            return self.state

    async def _prepare(self):
        if self.knowledge:
            self.knowledge.startup()
            self._save()
        # Every run searches all four purposes, even with a full set of user materials.
        name = self.state["research"]["company"]
        if not self.state["research"]["market"] or not self.state["research"]["security_code"]:
            receipt = await asyncio.to_thread(self.tools.adapter.search, f"{name} 证券代码 官方 交易所", "identity", 3)
            self.state["searches"].append(receipt)
            self.state["budget"]["tool_calls"] += 1
            self.state["input_snapshot"] = self.tools.snapshot()
            sources = self.tools._read_materials({"company": name}).data["documents"]
            resolution = await self._decision("identity", {"company": name, "sources": sources})
            anchors = self.tools.anchors()
            candidates = [c for c in resolution["candidates"] if c["source_ref"] in anchors and c["quote"] in anchors[c["source_ref"]][0]["text"] and c["security_code"] in c["quote"]]
            candidates = list({(c["market"], c["security_code"], c["trading_currency"], c["share_class"]): c for c in candidates}.values())
            self.state["identity_candidates"] = candidates
            if len(candidates) != 1:
                raise StopRun("公司或证券身份有歧义或证据不足：请用 --market 和 --security-code 确认具体证券后启动")
            identity = candidates[0]
            self.state["identity"] = identity
            self.state["research"].update({k: identity[k] for k in ("market", "exchange", "security_code", "trading_currency","share_class")})
            if self.knowledge:
                self.knowledge.initialize()
        queries = [("disclosure", f"{name} 年报 财务报告 官方"), ("counter_evidence", f"{name} 风险 竞争 现金流"),
                   ("changes", f"{name} 公告 最新 经营变化"), ("expectations", f"{name} 市场预期 估值 consensus")]
        for purpose, query in queries:
            self.run_budget.check()
            self.state["budget"]["tool_calls"] += 1
            if self.state["budget"]["tool_calls"] > self.state["limits"]["total_tools"]:
                raise StopRun("全系统工具预算耗尽")
            self._log("independent_search_started", {"query": query, "purpose": purpose})
            receipt = await asyncio.to_thread(self.tools.adapter.search, query, purpose, 2)
            self.state["searches"].append(receipt)
            self.state["input_snapshot"] = self.tools.snapshot()
            if self.knowledge:
                self.knowledge.archive_sources()
            self._log("independent_search_result", receipt)
            self._save()
        if self.state.get("framework_auto") and not self.state.get("framework_file"):
            choice = await self._decision("choose_framework", {"company": name, "sources": self.tools._read_materials({"company": name}).data["documents"][:3]})
            self.state["framework"] = FrameworkStore(self.directory.parent / "frameworks").builtin(choice["identifier"])
            self.state["framework_selection_reason"] = choice["reason"]
        if self.state.get("framework_file"):
            text = self.state["framework_original"]
            rule = await self._decision("framework", {"original": text})
            store = FrameworkStore(self.directory.parent / "frameworks")
            draft = store.draft(rule, text, self.state["framework"]["version"], "用户自定义", self.state["run_id"])
            self.state["framework_draft"] = draft
            self._log("framework_draft", draft)
            task = self._make_task(MainAction(action="delegate_task",target="verification",purpose="audit",question="独立核验用户框架草稿"))
            task.inputs = {"framework_draft": draft}
            task.authorized_tools = []
            task.framework_version = draft["version"]
            self.state["tasks"].append(task.model_dump())
            review = await self._decision("review_framework", {"task_id":task.task_id,"draft":draft})
            self._log("framework_review", {"task_id":task.task_id,**review})
            self.state["results"].append(Result(task_id=task.task_id,status="complete" if review["passed"] else "partial",
                data=review,assumptions_version=task.assumptions.version,actual_calls=0,
                unresolved=[] if review["passed"] else review["reasons"]).model_dump())
            if not review["passed"]:
                raise StopRun("用户框架未通过独立核验："+"；".join(review["reasons"]))
            self.state["framework"] = store.verify(draft["version"], {"passed":True,"review_task_id":task.task_id,"reasons":review["reasons"]})
            self.state["framework_selection_reason"] = "用户指定框架经独立核验后固定于本次研究"

    def _make_task(self, action):
        a, r = self.state["artifacts"], self.state["research"]
        role, purpose = action.target, action.purpose
        allowed = {"finance": {"collect", "supplement", "repair"}, "thesis": {"analyze", "repair"}, "valuation": {"propose", "calculate", "repair"}, "verification": {"audit"}}
        if purpose not in allowed[role]:
            raise StopRun("非法专业任务目的")
        if role == "thesis" and not a.get("finance"):
            raise StopRun("投资逻辑缺少资料输入")
        if role == "valuation" and not a.get("thesis"):
            raise StopRun("估值任务缺少投资论点")
        if role == "valuation" and purpose != "propose" and not self.state["assumptions"]["confirmed"]:
            raise StopRun("估值缺少当前版本授权")
        if purpose == "repair":
            if not any(i["target"] == role and i["severity"] in {"major", "critical"} for i in self.state["issues"]):
                raise StopRun("修复未对应独立核验问题")
            if self.state["budget"]["repair_rounds"] >= self.state["limits"]["repair_rounds"]:
                raise StopRun("核验修复达到上限")
            self.state["budget"]["repair_rounds"] += 1
        wanted = {"finance": [], "thesis": ["finance"], "valuation": ["finance", "thesis", "proposal"], "verification": list(a)}[role]
        inputs = {k: a[k] for k in wanted if k in a}
        if purpose == "repair":
            inputs["repair_requirements"] = {"issues": [i for i in self.state["issues"] if i["target"] == role]}
            prior = a.get(role)
            if not prior and role == "thesis":
                prior = next((r for r in reversed(self.state["results"]) if "claims" in r["data"]), None)
            if prior:
                inputs["prior_result"] = prior
        inputs["framework"] = self.state["framework"]
        if self.knowledge:
            inputs["historical_knowledge"] = {"records":self.state["knowledge"]["startup"],"notice":self.state["knowledge"].get("startup_notice")}
            inputs["knowledge_context"]={"company_id":self.state["knowledge"]["company_id"],"security_id":self.state["knowledge"]["security_id"],"base_version":self.knowledge.store.version(self.state["knowledge"]["company_id"])}
            inputs["knowledge_comparisons"] = {"records":self.state["knowledge"]["comparisons"]}
            if role == "verification":
                inputs["knowledge_proposals"] = {"records":[self.knowledge.store.proposal(p) for p in self.state["knowledge"]["proposals"]]}
        if role == "verification" and self.state.get("framework_draft"):
            inputs["framework_draft"] = self.state["framework_draft"]
        remaining = max(0, self.state["limits"]["total_tools"] - self.state["budget"]["tool_calls"])
        return Task(task_id="task-" + uuid4().hex[:12], run_id=self.state["run_id"], target=role, company=r["company"], horizon=r["horizon"], cutoff=r["cutoff"],
            question=action.question or "完成限定研究任务", inputs=inputs, input_refs=[v["task_id"] for v in inputs.values() if "task_id" in v],
            assumptions=self.state["assumptions"], authorized_tools=self.permissions[role], completion_conditions=["保留真实原始证据及缺口", "独立核验"],
            tool_limit=min(remaining, self.state["limits"]["task_tools"]), time_limit_seconds=min(self.state["limits"]["task_seconds"], max(.001, self.run_budget.remaining())),
            cost_limit_usd=0, purpose=purpose, shared_refs=[v["task_id"] for v in inputs.values() if "task_id" in v],
            cost_limit_cny=max(0,self.state["limits"]["max_cost_cny"]-self.state["budget"]["model_cost_cny"]),
            framework_version=self.state["framework"]["version"], standard_version=self.state["standard_version"])

    async def _delegate(self, action):
        task = self._make_task(action)
        self.state["tasks"].append(task.model_dump())
        if self.knowledge and task.target == "verification":
            self.knowledge.store.register_reviewer(task.task_id, self.state["run_id"])
        self._log("task_dispatched", task.model_dump())
        self._save()
        from .adk_app import run_worker
        result = Result.model_validate(await run_worker(self, task.model_dump()))
        if result.task_id != task.task_id or result.assumptions_version != task.assumptions.version:
            raise StopRun("成果任务或假设版本不匹配")
        key = "proposal" if task.target == "valuation" and task.purpose == "propose" else task.target
        if key == "proposal":
            try:
                proposal = ValuationProposal.model_validate(result.data)
                if proposal.model != "not_applicable":
                    candidate = Assumptions(model=proposal.model, parameters=proposal.parameters, basis=proposal.basis).model_dump()
                    self._check_authorization(candidate)
            except (ValueError, StopRun, FileNotFoundError, KeyError) as exc:
                result.status = "partial"
                result.data = {"model": "not_applicable", "parameters": {}, "basis": {}, "scope": "方法硬门槛未通过，拒绝发布估值", "gaps": [str(exc)]}
                result.unresolved = list(dict.fromkeys(result.unresolved + [str(exc)]))
        if key == "finance" and not result.data.get("financials") and self.tools.extraction_path.exists():
            persisted = json.loads(self.tools.extraction_path.read_text())
            result.data.update(persisted)
            result.evidence_refs = sorted(set(result.evidence_refs) | {r["source_ref"] for r in persisted["financials"] + persisted["evidence"]})
            result.unresolved = list(dict.fromkeys(result.unresolved + ["补查未产出新底表，保留此前已校验的原始提取快照"]))
            self._log("validated_extraction_retained", {"task_id": task.task_id, "financials": len(persisted["financials"]), "evidence": len(persisted["evidence"])})
        self.state["results"].append(result.model_dump())
        if key in self.state["artifacts"]:
            self.state["invalidated"].append(self.state["artifacts"][key]["task_id"])
        self.state["artifacts"][key] = result.model_dump()
        if key == "proposal":
            if result.data.get("model") not in {"dcf", "pe", "pb", "ev_ebitda", "ddm", "not_applicable"}:
                result.data = {"model": "not_applicable", "parameters": {}, "basis": {},
                    "scope": "本轮未取得符合合约的估值提案；程序不授权估值，方法适用性仍需核验",
                    "gaps": list(dict.fromkeys(result.unresolved + ["估值提案未完整返回；未运行DCF，不提供目标价"]))}
                result.status = "partial"
                self.state["artifacts"][key] = result.model_dump()
            ValuationProposal.model_validate(result.data)
            if result.data.get("model") != "not_applicable":
                model = result.data["model"]
                self._validate_parameters(result.data["parameters"], model)
                current = self.state["assumptions"]
                if current["parameters"]:
                    self.state["assumption_history"].append(json.loads(json.dumps(current)))
                    current["version"] += 1
                current.update(model=model, parameters=result.data["parameters"], basis=result.data.get("basis", {}), confirmed=False, authorization_source="none")
                self._check_authorization(current)
                if self.state["research"]["confirmation_mode"] == "autonomous":
                    self._authorize()
            else:
                current = self.state["assumptions"]
                if current["parameters"]:
                    self.state["assumption_history"].append(json.loads(json.dumps(current)))
                    self.state["assumptions"] = Assumptions(version=current["version"]+1).model_dump()
        if key == "verification":
            self.state["issues"] = result.data.get("issues", [])
            if self.state.get("framework_draft") and result.data.get("framework_passed") and result.data.get("passed"):
                store = FrameworkStore(self.directory.parent / "frameworks")
                verified = store.verify(self.state["framework_draft"]["version"], {"passed": True, "review_task_id": task.task_id})
                self._log("framework_verified_for_future_runs", verified)
        else:
            for dependent in (["thesis", "proposal", "valuation", "verification"] if key == "finance" else ["valuation", "verification"] if key in {"thesis", "proposal"} else ["verification"]):
                artifact = self.state["artifacts"].pop(dependent, None)
                if artifact:
                    self.state["invalidated"].append(artifact["task_id"])
            if key == "finance" and self.state["assumptions"]["parameters"]:
                self.state["assumption_history"].append(dict(self.state["assumptions"]))
                self.state["assumptions"] = Assumptions(version=self.state["assumptions"]["version"] + 1).model_dump()
        self._log("task_result", result.model_dump())
        if self.knowledge:
            if key == "finance":
                self.knowledge.archive_extraction()
            elif key in {"proposal","valuation"}:
                self.knowledge.prioritize_refs(result.evidence_refs,"R2","原文位置用于本次估值适用性或参数检查："+str(result.data.get("model","")))
            elif key == "thesis":
                self.knowledge.archive_thesis(result.model_dump())
                await self._compare_history(result.model_dump())
            elif key == "verification":
                await self._review_knowledge(task)
                self.knowledge.commit_reviewed()
        self._save()

    async def _review_knowledge(self, task):
        from .knowledge.schemas import ReviewBatch
        if not hasattr(self.backend, "review_knowledge"):
            self._log("knowledge_review_unavailable", {"state":"pending","reason":"injected backend does not implement independent knowledge review"})
            return
        pending = [self.knowledge.store.proposal(p) for p in self.state["knowledge"]["proposals"]]
        pending = [p for p in pending if p["item"]["kind"] in {"fact","thesis","question"} and not (p["revision_id"] and self.knowledge.store.verification(p["revision_id"])["status"] != "pending")]
        for offset in range(0, len(pending), 12):
            batch = pending[offset:offset+12]
            payload = {"task_id":task.task_id,"proposals":batch,"original_anchors":[{"ref":ref,"text":chunk[0]["text"],"location":chunk[0]["location"]} for ref,chunk in self.tools.anchors().items() if any(ref == e["anchor_ref"] for p in batch for e in p["item"]["evidence"]+p["item"]["opposing_evidence"])]}
            reviewed = ReviewBatch.model_validate(await self._decision("review_knowledge", payload))
            expected = {p["id"] for p in batch}
            if {r.proposal_id for r in reviewed.reviews} != expected or len(reviewed.reviews) != len(expected):
                raise StopRun("知识核验未完整绑定本批提议")
            for review in reviewed.reviews:
                self.knowledge.service.review(review.model_dump(),task.task_id,"verification")

    async def _compare_history(self, result):
        from .knowledge.schemas import ComparisonBatch, KnowledgeItem
        meta = self.state["knowledge"]
        evidence = [self.knowledge.ref(r) for r in result["evidence_refs"] if r in self.tools.anchors()]
        snapshot = self.knowledge.store.snapshot(self.state["run_id"],meta["company_id"],result["data"],evidence)
        meta["snapshots"].append(snapshot)
        history = [r for r in meta["startup"] if r["item"]["kind"] in {"thesis","question"} and r["item"]["judgment"] != "closed"]
        comparisons = []
        if history and hasattr(self.backend,"compare_knowledge"):
            parsed = ComparisonBatch.model_validate(await self._decision("compare_knowledge", {"snapshot_id":snapshot,"current":result,"history":history,"evidence":evidence}))
            allowed = {(r["entry_id"],r["id"]) for r in history}
            for comparison in parsed.comparisons:
                if (comparison.entry_id,comparison.revision_id) not in allowed:
                    raise StopRun("历史比较引用不属于本次固定知识版本")
                self.knowledge.store.validate_evidence(KnowledgeItem.model_validate(self.knowledge.item("thesis",{},[],opposing_evidence=comparison.evidence)))
                prior=next(r for r in history if r["entry_id"]==comparison.entry_id and r["id"]==comparison.revision_id)
                scope=prior["item"]["content"].get("metric_scope")
                if scope and comparison.metric_scope!=scope and comparison.relation in {"supports","refutes"}:
                    comparison.relation="insufficient"
                    comparison.reason="指标范围不一致，需要核对业务与集团口径；"+comparison.reason
                comparisons.append(comparison.model_dump())
        else:
            comparisons = [{"entry_id":r["entry_id"],"revision_id":r["id"],"relation":"insufficient","reason":"未完成独立历史差异判断","metric_scope":"requires_review","evidence":[],"follow_up":"核对当前与历史论点的期间、业务范围、支持与反证"} for r in history]
        identifier = self.knowledge.store.compare(snapshot,comparisons)
        meta["comparisons"].append({"id":identifier,"snapshot_id":snapshot,"comparisons":comparisons})
        self._log("history_compared_after_snapshot",meta["comparisons"][-1])

    def _set_requirements(self, values, initial=False):
        changed = []
        for key in ("horizon","questions","topics"):
            if key in values and values[key] != self.state["research"].get(key):
                self.state["research"][key] = values[key]
                changed.append(key)
        framework = values.get("framework")
        if framework and (initial or framework != self.state.get("requirement_framework")):
            self.state["framework_auto"] = framework == "auto"
            selected = FrameworkStore(self.directory.parent/"frameworks").builtin("quality" if framework == "auto" else framework)
            if selected != self.state["framework"]:
                self.state["framework"] = selected
                changed.append("framework")
            self.state["requirement_framework"] = framework
        if values.get("notes","") != self.state.get("requirement_notes",""):
            changed.append("notes")
        self.state["requirement_notes"] = values.get("notes","")
        if changed and not initial:
            for key in ("thesis","proposal","valuation","verification"):
                old = self.state["artifacts"].pop(key,None)
                if old:
                    self.state["invalidated"].append(old["task_id"])
            self.state["assumption_history"].append(dict(self.state["assumptions"]))
            self.state["assumptions"] = Assumptions(version=self.state["assumptions"]["version"]+1).model_dump()
            self._log("requirements_applied_artifacts_invalidated",{"fields":changed})

    def _apply_requirements(self):
        if not self.knowledge:
            return
        request = self.directory/"preferences.request.json"
        from .knowledge.preferences import Preferences
        from .knowledge.schemas import digest
        signature = digest(Preferences(self.knowledge.store).list())
        if not request.exists() and signature == self.state.get("preference_signature"):
            return
        payload = json.loads(request.read_text()) if request.exists() else {}
        explicit = self.state["explicit_parameters"]
        if payload.get("modify_this_task"):
            for key in payload.get("keys",[]):
                explicit.pop(key,None)
        values, applied = Preferences(self.knowledge.store).resolve(self.state["knowledge"]["company_id"],explicit,self.state["preference_defaults"],self.state["run_id"])
        self._set_requirements(values)
        self.state["preference_applications"] = applied
        self.state["preference_signature"] = signature
        request.unlink(missing_ok=True)

    @staticmethod
    def _validate_parameters(parameters, model="dcf"):
        from .valuation import validate_inputs
        if model != "dcf":
            validate_inputs(model, parameters)
            return
        # Reuse all numeric guards, with currency validated separately.
        currency = parameters.get("currency")
        if currency not in {"CNY", "HKD", "USD"}:
            raise ValueError("DCF currency must be explicit CNY/HKD/USD")
        legacy = {k: v for k, v in parameters.items() if k != "minority_interest"}
        Engine._validate_parameters(legacy | {"currency": "CNY"})
        if "minority_interest" in parameters:
            from .tools import _number
            if _number(parameters["minority_interest"]) < 0:
                raise ValueError("minority interest must be nonnegative")

    def _check_authorization(self, assumptions):
        metrics = self.state["artifacts"].get("finance", {}).get("data", {}).get("metrics", {})
        params = assumptions["parameters"]
        model = assumptions.get("model", "dcf")
        self._validate_parameters(params, model)
        if model != "dcf":
            self.tools.validate_method_sources(model, params, self.state["research"])
            required = ["metric", "comparable_rule"] if model != "ddm" else ["metric", "cost_of_equity", "terminal_growth"]
            if any(not assumptions["basis"].get(k) for k in required):
                raise StopRun("估值方法参数依据不完整")
            return
        if metrics.get("currency") != params["currency"] or metrics.get("security_code") != self.state["research"]["security_code"] or not metrics.get("share_scope"):
            raise StopRun("DCF币种或证券股数口径未通过授权检查")
        if params["base_fcf"] not in metrics.get("fcf", {}).values() or params["net_debt"] != metrics.get("net_debt") or params["shares"] != metrics.get("shares"):
            raise StopRun("DCF现金流、净债务或股数缺少确定性财务调节")
        if any(not assumptions["basis"].get(k) for k in params if k not in {"currency", "unit"}):
            raise StopRun("DCF参数依据不完整")
        if self.state.get("valuation_schema", 1) >= 2:
            if metrics.get("fcff_bridge", {}).get("method") != "ebit":
                raise StopRun("新研究优先使用已核对EBIT桥接；OCF倒推路径尚未开放，unlevered标签不能代替利息及税盾核对")
            if "minority_interest" not in params or metrics.get("minority_interest") != params["minority_interest"]:
                raise StopRun("DCF少数股东权益缺少明确数值及来源；未知不能按零计算")
            if metrics.get("share_scope") != "total ordinary shares":
                raise StopRun("DCF需要匹配的总普通股股数")
            self.tools.validate_dcf_sources(assumptions, self.state["research"])
    def _authorize(self):
        assumptions = self.state["assumptions"]
        self._check_authorization(assumptions)
        assumptions.update(confirmed=True, authorization_source="controller")
        self._log("autonomous_valuation_authorized", {"model": assumptions.get("model", "dcf"), "version": assumptions["version"], "basis": assumptions["basis"]})

    def _apply_pending_parameters(self):
        path = self.directory / "parameters.request.json"
        if not path.exists() or not self.state["assumptions"]["parameters"]:
            return
        changes = json.loads(path.read_text())
        current = self.state["assumptions"]
        # New requests may replace method + parameters + basis; plain dicts remain legacy DCF edits.
        model = changes.get("model", current.get("model", "dcf"))
        updated = changes["parameters"] if "parameters" in changes else current["parameters"] | changes
        basis = changes.get("basis", current["basis"] | {k: "用户明确修改并确认" for k in changes})
        self._validate_parameters(updated, model)
        candidate = Assumptions(model=model, version=current["version"]+1, confirmed=True, authorization_source="user", parameters=updated, basis=basis).model_dump()
        self._check_authorization(candidate)
        self.state["assumption_history"].append(json.loads(json.dumps(current)))
        self.state["assumptions"] = candidate
        self.state["artifacts"]["proposal"]["data"].update(model=model, parameters=updated, basis=basis)
        self.state["artifacts"]["proposal"]["assumptions_version"] = candidate["version"]
        self.state["user_overrides"] = sorted(set(self.state["user_overrides"]) | set(changes))
        for key in ("thesis", "valuation", "verification"):
            old = self.state["artifacts"].pop(key, None)
            if old:
                self.state["invalidated"].append(old["task_id"])
        self.state["issues"] = []
        if self.state["report"]:
            self.state["report_history"].append(self.state["report"])
        self.state["report"] = None
        self._log("user_parameters_applied", {"changes": changes, "version": self.state["assumptions"]["version"]})
        path.unlink()
        self._save()

    def _prepare_worker_tool(self, execution, name, arguments):
        self.run_budget.check()
        task, local = execution["task"], execution["local"]
        if name not in task.authorized_tools:
            raise StopRun("请求未授权工具")
        if task.depth == 3 and name == "spawn_subtask":
            raise StopRun("禁止第四层委派")
        if time.monotonic() - execution["started"] >= task.time_limit_seconds:
            raise StopRun("子任务时间上限")
        if self.state["budget"]["tool_calls"] >= self.state["limits"]["total_tools"] or local["actual_calls"] + local.get("descendant_calls", 0) >= task.tool_limit:
            raise StopRun("工具预算耗尽")
        args = dict(arguments)
        if name in {"read_materials", "extract_financials"}:
            args.update(company=task.company, cutoff=task.cutoff)
        if name == "valuation":
            current = self.state["assumptions"]
            if task.target != "valuation" or not current["confirmed"] or args.get("model", "dcf") != current.get("model", "dcf") or args.get("parameters") != current["parameters"] or args.get("assumptions_version") != current["version"] or task.assumptions.model_dump() != Assumptions.model_validate(current).model_dump():
                raise StopRun("估值必须使用当前授权版本")
            self._check_authorization(current)
            args["basis"] = current["basis"]
        if name == "check_consistency":
            args = {"results": task.inputs, "assumptions": task.assumptions.model_dump(), "research": self.state["research"], "user_overrides": self.state["user_overrides"]}
        import hashlib
        fingerprint = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
        if execution["failures"].get(fingerprint, 0) > self.state["limits"]["retries"]:
            raise StopRun("同一失败操作重试上限")
        return args, fingerprint

    async def _execute_worker_tool(self, execution, name, arguments):
        args, fingerprint = self._prepare_worker_tool(execution, name, arguments)
        local, task = execution["local"], execution["task"]
        self.state["budget"]["tool_calls"] += 1
        local["actual_calls"] += 1
        self._save()
        self._log("tool_started", {"task_id": task.task_id, "name": name, "arguments": args})
        if name == "spawn_subtask":
            response = await self._spawn(task, args)
        elif name == "collaborate":
            response = self._collaborate(task, args)
        elif name.startswith("knowledge_") and self.knowledge:
            response = self.knowledge.execute(name,args,task)
        elif name == "read_skill_resource":
            try:
                data=self.extensions.read_resource(args["skill"],args["path"],task.target)
                response={"ok":True,"data":data,"evidence_refs":[],"calculation_refs":[]}
            except (ValueError,OSError,UnicodeError) as exc:
                response={"ok":False,"data":{},"error":{"code":"SKILL_RESOURCE","message":str(exc),"retryable":False},"evidence_refs":[],"calculation_refs":[]}
        elif name in self.extensions.bindings:
            spec,_=self.extensions.bindings[name]
            receipt=None
            if spec["cost_kind"]=="model":
                if self.state["budget"]["model_calls"]>=self.state["limits"]["max_models"]:
                    raise StopRun("模型调用预算耗尽")
                receipt=self.run_budget.reserve_fixed(spec["cost_cny"],"extension",{"source":spec["cost_source"],"as_of":spec["cost_as_of"],"tool":name})
                self.state["budget"]["model_calls"]+=1
                self._save()
            else:
                self.state["budget"]["external_service_cost_cny"]+=spec["cost_cny"]
                self._save()
            try:
                remaining=min(self.run_budget.remaining(),task.time_limit_seconds-(time.monotonic()-execution["started"]))
                data=await self.extensions.call(name,args,max(.01,remaining))
                encoded=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
                if len(encoded)>1_000_000:raise ValueError("扩展输出超过 1 MB")
                source=self.tools.adapter.add(encoded,".json","extension:"+name)
                self.state["input_snapshot"]=self.tools.snapshot()
                response={"ok":True,"data":{"result":data,"source_id":source["id"],"source_hash":source["source_hash"]},
                          "evidence_refs":[c["ref"] for c in source["chunks"]],"calculation_refs":[]}
            except Exception as exc:
                response={"ok":False,"data":{},"error":{"code":"EXTENSION_FAILED","message":"扩展调用失败："+type(exc).__name__,"retryable":False},"evidence_refs":[],"calculation_refs":[]}
            finally:
                if receipt:self.run_budget.settle(receipt,None)
        else:
            if name == "extract_financials":
                try:
                    await asyncio.wait_for(self._extract(task.cutoff, args), min(self.run_budget.remaining(), max(.01, task.time_limit_seconds - (time.monotonic() - execution["started"]))))
                except TimeoutError:
                    self.tools.adapter.gap("提取达到子任务截止时点，保留已提取部分")
            response = (await asyncio.to_thread(self.tools.execute, name, args)).model_dump()
            if name in {"fetch_source", "search_public"}:
                self.state["input_snapshot"] = self.tools.snapshot()
                if self.knowledge:
                    self.knowledge.archive_sources()
        local["tool_outputs"].append({"tool": name, "arguments": args, "response": response})
        if not response["ok"]:
            execution["failures"][fingerprint] = execution["failures"].get(fingerprint, 0) + 1
        self.state["calculations"] = self.tools.calculations
        self._log("tool_result", {"task_id": task.task_id, "name": name, "response": response})
        self._save()
        return response

    async def _extract(self, cutoff, args=None):
        parsed = ReadArguments.model_validate(args or {"company": self.state["research"]["company"], "cutoff": cutoff})
        documents = self.tools.adapter.read_materials(parsed)["documents"]
        source_hashes = [d["source_hash"] for d in documents]
        extraction_key = {"sources": source_hashes, "query": parsed.query, "anchor_refs": parsed.anchor_refs, "selection_version": "financial-headings-v2"}
        if self.state.get("extraction_key") == extraction_key and self.tools.extraction_path.exists():
            return
        batches = []
        for doc in documents:
            # Prioritize statements and risk/operating sections while retaining an explicit coverage gap.
            chunks = research_chunks(doc, parsed.max_chunks, parsed.query, parsed.anchor_refs)
            anchors = [{"ref": c["ref"], "location": c["location"], "text": c["text"][:9000]} for c in chunks]
            if anchors:
                batches.append({"source_hash": doc["source_hash"], "url": doc["url"], "disclosure_date": doc.get("date"), "date_basis": doc.get("date_basis"), "anchors": anchors})
        aggregate = {"financials": [], "evidence": [], "gaps": []}
        if self.tools.extraction_path.exists():
            previous = json.loads(self.tools.extraction_path.read_text())
            aggregate = {k: previous[k] for k in aggregate}
        for document in batches[:12]:
            extracted = await self._decision("extract", {"document": document, "cutoff": cutoff, "framework": self.state["framework"]})
            self._log("model_extraction", {"source_hash": document["source_hash"], "response": extracted})
            for key in aggregate:
                aggregate[key].extend(x for x in extracted.get(key, []) if x not in aggregate[key])
            self.tools.accept_extraction(aggregate, cutoff)
        aggregate["gaps"].append("本次预算内提取每份材料最多12个相关区域及12份材料；复杂版式和未覆盖区域需补查")
        self.tools.accept_extraction(aggregate, cutoff)
        self.state["extracted_source_hashes"] = source_hashes
        self.state["extraction_key"] = extraction_key
        if self.knowledge:
            self.knowledge.archive_extraction()

    async def _spawn(self, parent, args):
        spec = ChildArguments.model_validate(args)
        if parent.depth >= 3 or set(spec.tools) - (set(parent.authorized_tools) - {"spawn_subtask"}) or set(spec.shared_refs) - (set(parent.shared_refs) | set(parent.input_refs)):
            raise StopRun("子任务层级、工具或共享引用越权")
        from .adk_app import _worker_execution
        execution = _worker_execution.get()
        used = execution["local"]["actual_calls"] + execution["local"].get("descendant_calls", 0) if execution else 0
        child = parent.model_copy(update={"task_id": "task-" + uuid4().hex[:12], "parent_task_id": parent.task_id,
            "issuer": parent.task_id, "depth": 3, "dynamic_role": spec.role, "question": spec.question,
            "authorized_tools": spec.tools, "shared_refs": spec.shared_refs, "completion_conditions": spec.completion_conditions,
            "inputs": {k: v for k, v in parent.inputs.items() if v.get("task_id") in spec.shared_refs or k == "framework"},
            "input_refs": spec.shared_refs,
            "tool_limit": min(max(0, parent.tool_limit - used), self.state["limits"]["total_tools"] - self.state["budget"]["tool_calls"]),
            "time_limit_seconds": min(parent.time_limit_seconds, max(.001, self.run_budget.remaining()))})
        self.state["tasks"].append(child.model_dump())
        self._log("subtask_created", child.model_dump())
        result = await self._work_task(child.model_dump())
        self.state["results"].append(result)
        if execution:
            execution["local"]["descendant_calls"] = execution["local"].get("descendant_calls", 0) + result["actual_calls"]
        self._log("subtask_result", result)
        return {"ok": True, "data": result, "evidence_refs": result["evidence_refs"], "calculation_refs": result["calculation_refs"]}

    def _collaborate(self, task, args):
        spec = CollaborationArguments.model_validate(args)
        if spec.target_task_id not in task.shared_refs:
            raise StopRun("跨任务上下文未授权共享")
        if spec.operation == "read":
            result = next((r for r in self.state["results"] if r["task_id"] == spec.target_task_id), None)
            return {"ok": bool(result), "data": result or {}, "evidence_refs": (result or {}).get("evidence_refs", []), "calculation_refs": (result or {}).get("calculation_refs", [])}
        receipt = {"issuer": task.task_id, **spec.model_dump()}
        if spec.operation == "request_transfer":
            receipt.update(id=uuid4().hex, status="requested")
            self.state["transfers"].append(receipt)
        else:
            self.state["messages"].append(receipt)
        self._log("collaboration_" + spec.operation, receipt)
        return {"ok": True, "data": receipt, "evidence_refs": [], "calculation_refs": []}

    async def _approve_transfer(self, action):
        request = next((r for r in self.state["transfers"] if r["id"] == action.transfer_id and r["status"] == "requested"), None)
        if not request or not request["completion_conditions"] or not request["message"]:
            raise StopRun("转派申请无效或缺少任务及完成条件")
        target = next(t for t in self.state["tasks"] if t["task_id"] == request["target_task_id"])
        issuer = next(t for t in self.state["tasks"] if t["task_id"] == request["issuer"])
        if set(request["input_refs"]) - set(issuer["shared_refs"]):
            raise StopRun("转派输入超出共享授权")
        self.run_budget.check()
        if request["tool_budget"] > self.state["limits"]["total_tools"] - self.state["budget"]["tool_calls"]:
            raise StopRun("转派预算不足")
        request.update(status="approved", approval_reason=action.rationale)
        task = Task.model_validate(target).model_copy(update={"task_id": "task-" + uuid4().hex[:12], "issuer": "main", "parent_task_id": issuer["task_id"],
            "question": request["message"], "input_refs": request["input_refs"], "tool_limit": request["tool_budget"],
            "inputs": {str(i): next(r for r in self.state["results"] if r["task_id"] == i) for i in request["input_refs"]},
            "time_limit_seconds": min(target["time_limit_seconds"], self.run_budget.remaining())})
        self.state["tasks"].append(task.model_dump())
        result = await self._work_task(task.model_dump())
        self.state["results"].append(result)
        request.update(status="executed", task_id=task.task_id)
        self._log("transfer_executed", {"approval": request, "result": result})

    def _validate_worker_result(self, task, local, result):
        if task.target == "thesis" and task.depth == 2:
            from pydantic import ValidationError
            try:
                result.data = ThesisData.model_validate(result.data).model_dump(exclude_none=True)
            except ValidationError:
                raise StopRun("投资逻辑成果未遵守claims、hypotheses、expectations及风险列表合约") from None
        if task.target == "finance" and task.depth == 2:
            receipts = [o["response"]["data"] for o in local["tool_outputs"] if o["tool"] == "extract_financials" and o["response"]["ok"]]
            if receipts:
                expected = receipts[-1]
                metrics = [o["response"]["data"] for o in local["tool_outputs"] if o["tool"] == "calculate_metrics" and o["response"]["ok"]]
                if result.data.get("use_receipts"):
                    result.data = {**expected, "metrics": metrics[-1] if metrics else {}}
                if result.data.get("financials") != expected["financials"]:
                    raise StopRun("财务成果不能改写已校验提取记录")
                result.data.update(evidence=expected["evidence"], conflicts=expected["conflicts"])
                result.data["gaps"] = list(dict.fromkeys(result.data.get("gaps", []) + expected["gaps"]))
                if metrics and result.data.get("metrics") != metrics[-1]:
                    raise StopRun("财务指标成果不能改写确定性计算凭证")
        if task.depth == 3:
            if result.task_id != task.task_id or result.actual_calls != local["actual_calls"]:
                raise StopRun("第三层成果归属错误")
            return result.model_dump()
        if task.target == "verification":
            result.data["business_review_completed"] = True
        return super()._validate_worker_result(task, local, result)

    def _partial_worker_result(self, task, local, reason):
        result = super()._partial_worker_result(task, local, reason)
        if task.target == "finance" and not result["data"].get("financials") and self.tools.extraction_path.exists():
            saved = json.loads(self.tools.extraction_path.read_text())
            result["data"].update(saved)
            result["evidence_refs"] = sorted(set(result["evidence_refs"]) | {r["source_ref"] for r in saved["financials"] + saved["evidence"]})
            if saved["financials"] or saved["evidence"]:
                result["status"] = "partial"
        if task.target == "verification" and task.depth == 2:
            result["data"].update(passed=False, business_review_completed=False)
            result["data"].setdefault("issues", []).append({"id": "business-review-incomplete", "severity": "major", "target": "verification", "requirement": "complete_independent_review", "message": "核验模型未返回完整成果，程序检查不能代替独立业务核验"})
        return result

    def _pause(self, question):
        if self.state["research"]["confirmation_mode"] == "autonomous":
            raise StopRun("自主模式保留资料缺口并收束，不强制等待用户资料")
        return super()._pause(question)

    async def respond(self, decision, parameters=None):
        if decision == "cancel":
            return await super().respond(decision, parameters)
        try:
            self.run_budget.check()
        except BudgetExceeded as exc:
            self._stop(str(exc))
            self._finish()
            return self.state
        if decision == "confirm":
            self._check_authorization(self.state["assumptions"])
        state = await super().respond(decision, parameters)
        if decision in {"confirm", "modify"}:
            state["assumptions"]["authorization_source"] = "user"
            self._save()
        return state

    def _report(self):
        report = super()._report()
        thesis = self.state["artifacts"].get("thesis", {}).get("data", {})
        if not isinstance(report["市场预期差"], dict):
            report["市场预期差"] = {"status": "unverified_hypothesis", "baseline": None, "statement": "市场预期成果未通过结构合约，待重新核验"}
            report["投资结论摘要"]["gaps"].append("市场预期成果形状无效，不能作为已核验基准")
        if thesis.get("summary"):
            report["投资结论摘要"]["statement"] = thesis["summary"]
        report["事实与数据基础"]["research_evidence"] = self.state["artifacts"].get("finance", {}).get("data", {}).get("evidence", [])
        report["事实与数据基础"].update(framework=self.state["framework"], standard_version=self.state["standard_version"], searches=self.state["searches"])
        if self.knowledge:
            report["事实与数据基础"]["knowledge"] = {"fixed_refs":self.knowledge.fixed_refs(),"snapshots":self.state["knowledge"]["snapshots"],"comparisons":self.state["knowledge"]["comparisons"],"notice":self.state["knowledge"].get("startup_notice"),"submission_issues":self.state["knowledge"].get("submission_issues",[])}
        report["事实与数据基础"]["input_version"] = {"manifest_hash": self.state["input_snapshot"]["manifest_hash"],
            "sources": [{k: m.get(k) for k in ("id", "file", "source_hash", "url", "acquired_at", "date")} for m in self.state["input_snapshot"]["manifest"]["materials"]]}
        comparison = self.state["artifacts"].get("valuation", {}).get("data", {}).get("market_comparison")
        if comparison:
            report["市场预期差"].update(comparison=comparison, baseline=comparison["baseline"])
            if comparison["status"] == "unresolved":
                report["市场预期差"].update(status="unresolved", statement=comparison["statement"])
                report["投资结论摘要"]["gaps"].append(comparison["statement"])
        return report

    def _finish(self, cancelled=False):
        marker=self.directory/"source-missing.json"
        if marker.exists():
            self.state["reproducibility"]=json.loads(marker.read_text())
            issue={"id":"permanently-deleted-source","severity":"critical","target":"finance","requirement":"source_missing","message":"历史证据已永久删除，无法完整复现；相关成果需重新获取与核验"}
            if issue not in self.state["issues"]:
                self.state["issues"].append(issue)
            verification=self.state["artifacts"].get("verification")
            if verification:
                verification["data"]["passed"]=False
        finance = self.state["artifacts"].get("finance")
        if finance and not finance["data"].get("financials") and self.tools.extraction_path.exists():
            saved = json.loads(self.tools.extraction_path.read_text())
            finance["data"].update(saved)
            finance["evidence_refs"] = sorted(set(finance["evidence_refs"]) | {r["source_ref"] for r in saved["financials"] + saved["evidence"]})
            finance["unresolved"] = list(dict.fromkeys(finance["unresolved"] + ["使用持久化的已校验提取快照，补查失败保留缺口"]))
        self.state["budget"]["active_seconds"] = time.time() - datetime.fromisoformat(self.state["created_at"]).timestamp()
        cancelled = cancelled or (self.directory / "cancel.request").exists()
        super()._finish(cancelled)
        if self.state["artifacts"].get("finance", {}).get("data", {}).get("evidence") and self.state["status"] == "failed":
            self.state["status"] = "partial"
            self._save()
        if self.knowledge:
            self.knowledge.finish()
            self._save()
