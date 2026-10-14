"""Native ADK agents, tools and callbacks around the durable research controller."""

from __future__ import annotations

from contextvars import ContextVar
import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool, ToolContext
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from pydantic import Field

from .schemas import WorkerAction
from .tools import ToolRegistry
from .adk_runtime import TraceStore, execution_scope, invocation_scope, native_app


ROLES = ("finance", "thesis", "valuation", "verification")
ROLE_DESCRIPTIONS = {
    "finance": "读取原始材料，提取带来源与口径的财务字段，计算财务指标。",
    "thesis": "依据财务成果分析投资假说、风险、反证及市场预期比较基准。",
    "valuation": "提出方法与估值假设，按当前授权的方法、参数与版本执行确定性DCF、PE、PB、EV/EBITDA或单阶段DDM；固定演示保留旧DCF。",
    "verification": "核验原始证据、计算、假设版本和业务缺口，返回定向修复要求。",
}
_root_agent: ContextVar[Any] = ContextVar("yantra_root_agent", default=None)
_worker_execution: ContextVar[Any] = ContextVar("yantra_worker_execution", default=None)


def _text_response(value: dict[str, Any]) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[
        types.Part(text=json.dumps(value, ensure_ascii=False, default=str))
    ]))


class ResearchModel(BaseLlm):
    """Deterministic entry routing; inference stays in the budgeted backend."""

    model: str = "yantra_research_controller"

    async def generate_content_async(self, llm_request, stream=False):
        for part in llm_request.contents[-1].parts or []:
            if part.function_response and part.function_response.name == "execute_research":
                yield _text_response(part.function_response.response)
                return
        yield LlmResponse(content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name="execute_research", args={}))
        ]))


class WorkerDecisionModel(BaseLlm):
    """Adapt one validated backend action to an ADK tool call or final result."""

    model: str = "yantra_budgeted_decision_backend"

    async def generate_content_async(self, llm_request, stream=False):
        from .engine import StopRun

        execution = _worker_execution.get()
        engine, task, local = execution["engine"], execution["task"], execution["local"]
        remaining = task.time_limit_seconds - (time.monotonic() - execution["started"])
        usage = None
        try:
            action = WorkerAction.model_validate(await engine._decision(
                "worker", task.model_dump(), local, timeout=remaining
            ))
            if getattr(engine.backend, "inline_worker", False) and engine.backend.last_usage:
                value = engine.backend.last_usage
                usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=value["input_tokens"],
                    candidates_token_count=value["output_tokens"], total_token_count=value["total_tokens"])
            engine._log("worker_action", {"task_id": task.task_id, **action.model_dump()})
            if action.action == "return":
                response = _text_response(engine._validate_worker_result(task, local, action.result))
                response.usage_metadata = usage
                yield response
                return
            # Unknown tools cannot reach ADK's dispatcher or its transfer mechanism.
            engine._prepare_worker_tool(execution, action.tool, action.arguments)
        except StopRun as exc:
            response = _text_response(engine._partial_worker_result(task, local, str(exc)))
            response.usage_metadata = usage
            yield response
            return
        yield LlmResponse(usage_metadata=usage, content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name=action.tool, args=action.arguments))
        ]))


def worker_model_guard(callback_context, llm_request):
    """Reject unbound, mismatched or expired tasks before any backend request."""
    execution = _worker_execution.get()
    if execution is None or callback_context.agent_name != execution.get("agent_name", f"{execution['task'].target}_worker"):
        raise ValueError("Worker requires a controller-assigned matching task.")
    if time.monotonic() - execution["started"] >= execution["task"].time_limit_seconds:
        execution["stop_reason"] = "子任务时间上限"
    if execution.get("stop_reason"):
        return _text_response(execution["engine"]._partial_worker_result(
            execution["task"], execution["local"], execution["stop_reason"]
        ))
    # The backend receives only the task and local receipts, never chat history.
    llm_request.contents = [types.Content(role="user", parts=[
        types.Part(text="执行当前控制器限定的研究任务。")
    ])]
    return None


def worker_tool_guard(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext):
    """Check role, budget and confirmed assumptions before ADK executes a tool."""
    from .engine import StopRun

    execution = _worker_execution.get()
    if execution is None or tool_context.agent_name != execution.get("agent_name", f"{execution['task'].target}_worker"):
        raise ValueError("Tool requires a controller-assigned matching task.")
    try:
        normalized, _ = execution["engine"]._prepare_worker_tool(execution, tool.name, args)
    except StopRun as exc:
        execution["stop_reason"] = str(exc)
        return {"ok": False, "error": {"code": "POLICY_BLOCKED", "message": str(exc), "retryable": False}}
    args.clear()
    args.update(normalized)
    return None


class ResearchTool(BaseTool):
    """Expose existing validated tool schemas through ADK's tool lifecycle."""

    def __init__(self, name: str, contract: dict[str, Any]):
        super().__init__(name=name, description=contract["description"])
        self.arguments_schema = contract["arguments_schema"]

    def _get_declaration(self):
        return types.FunctionDeclaration(name=self.name, description=self.description,
                                         parameters_json_schema=self.arguments_schema)

    async def run_async(self, *, args, tool_context):
        from .engine import StopRun

        execution = _worker_execution.get()
        if execution is None or tool_context.agent_name != execution.get("agent_name", f"{execution['task'].target}_worker"):
            raise ValueError("Tool requires a controller-assigned matching task.")
        execution["tool_context"] = tool_context
        try:
            result = execution["engine"]._execute_worker_tool(execution, self.name, args)
            import inspect
            return await result if inspect.isawaitable(result) else result
        except StopRun as exc:
            execution["stop_reason"] = str(exc)
            return {"ok": False, "error": {"code": "POLICY_BLOCKED", "message": str(exc), "retryable": False}}


class WorkerAgent(LlmAgent):
    """A native leaf agent with only its role's registered ADK tools."""

    engine: Any = Field(default=None, exclude=True)
    role: str

    def __init__(self, role: str, engine: Any = None, **kwargs):
        from .engine import PERMISSIONS

        contracts = engine.tools.tool_contracts() if engine is not None else ToolRegistry().tool_contracts()
        permissions = getattr(engine, "permissions", PERMISSIONS)
        super().__init__(
            name=kwargs.pop("name", f"{role}_worker"), role=role, engine=engine,
            model=WorkerDecisionModel(), description=ROLE_DESCRIPTIONS[role],
            instruction=ROLE_DESCRIPTIONS[role] + "仅处理当前任务，不得递归委派。",
            tools=[ResearchTool(name, contracts[name]) for name in permissions[role]],
            sub_agents=[], disallow_transfer_to_parent=True, disallow_transfer_to_peers=True,
            include_contents="none", output_key="result",
            before_model_callback=worker_model_guard, before_tool_callback=worker_tool_guard,
            **kwargs,
        )

    async def _run_async_impl(self, ctx: InvocationContext):
        with invocation_scope(ctx.invocation_id):
            async for event in super()._run_async_impl(ctx):
                yield event


def build_workers(engine: Any = None) -> list[WorkerAgent]:
    return [WorkerAgent(role=role, engine=engine) for role in ROLES]


def require_session_scope(engine, tool_context):
    if (engine.directory / "adk-scopes.json").exists() and not TraceStore(engine.directory).permits(
        tool_context.session.app_name, tool_context.user_id, tool_context.session.id
    ):
        raise ValueError("Research belongs to another session.")


async def execute_research(tool_context: ToolContext) -> dict[str, Any]:
    """Advance research and apply only explicit confirmation or modification.

    The controller dispatches the declared workers and persists every budget,
    evidence receipt and assumption version. Missing user input stays waiting.
    """
    from .backend import BackendError
    from .engine import create_adk_engine

    root = _root_agent.get()
    if root is None:
        raise ValueError("Research must run through the Yantra root agent.")
    engine = root.engine
    message = "".join(part.text or "" for part in (
        tool_context.user_content.parts if tool_context.user_content else []
    ) if not part.thought).strip()
    try:
        if message == "/memories" or message.startswith(("/remember ","/forget ")):
            from .engine import Engine
            from .knowledge.preferences import Preferences
            from .knowledge.store import KnowledgeStore, atomic_bytes
            if engine is None and tool_context.state.get("run_id"):
                engine = Engine.load(Path(os.getenv("YANTRA_RUNS_DIR","runs")),tool_context.state["run_id"])
                require_session_scope(engine, tool_context)
            metadata = engine.state.get("knowledge",{}) if engine else {}
            store = KnowledgeStore(metadata.get("root") or os.getenv("YANTRA_KNOWLEDGE_DIR",".yantra/knowledge"))
            preferences = Preferences(store)
            if message == "/memories":
                return {"requirements":preferences.list()}
            if message.startswith("/forget "):
                return preferences.revoke(message.split(" ",1)[1].strip())
            data = json.loads(message.split(" ",1)[1])
            saved = preferences.save(data["original"],data["meaning"],data["parameters"],data.get("scope") or metadata.get("company_id") or "global",source="adk:/remember",identifier=data.get("id"))
            if data.get("apply_to_current"):
                if engine is None or engine.state["status"] not in {"running","waiting"}:
                    raise ValueError("requirement saved; active session required for application")
                atomic_bytes(engine.directory/"preferences.request.json",json.dumps({"modify_this_task":bool(data.get("modify_this_task")),"keys":list(data["parameters"])}).encode())
                saved["application"] = "pending_at_scheduling_boundary"
            return saved
        if engine is None:
            run_id = tool_context.state.get("run_id")
            engine = create_adk_engine(company=message or "DEMO", run_id=run_id)
            if not engine.state.get("raw_materials"):
                engine.adk_workers = root.sub_agents
            if run_id:
                require_session_scope(engine, tool_context)
                decision = {"确认": "confirm", "拒绝": "reject", "取消": "cancel"}.get(message, message)
                if decision in {"confirm", "reject", "cancel"}:
                    await engine.respond(decision)
                elif message.startswith("modify ") or message.startswith("修改 "):
                    await engine.respond("modify", json.loads(message.split(" ", 1)[1]))
        TraceStore(engine.directory).bind(tool_context.session)
        with execution_scope(engine, kind="entry"):
            state = await engine.advance()
        if state.get("report") and not (engine.directory / "adk-artifacts.json").exists():
            from .adk_artifacts import publish_outputs
            publish_outputs(engine)
    except (FileNotFoundError, ValueError, BackendError) as exc:
        # Do not expose provider messages, credentials or arbitrary file paths.
        return {"status": "error", "error_code": getattr(exc, "code", type(exc).__name__), "message": (
            "缺少本地研究材料，请设置 YANTRA_MATERIALS_DIR（含 manifest.json）。"
            if isinstance(exc, FileNotFoundError) else
            "真实模型模式需配置 MODEL_ID、MODEL_BASE_URL、MODEL_API_KEY。"
            if isinstance(exc, BackendError) else "研究配置或用户操作无效，请检查当前状态与参数。"
        )}
    snapshot = {key: state.get(key) for key in ("run_id", "status", "stage", "pending_user", "report")}
    if snapshot["status"] == "waiting":
        snapshot["resume_instruction"] = (
            '阅读假设后输入 confirm/确认、reject/拒绝、cancel/取消，'
            '或 modify {"growth_rate":0.03} 修改并确认；未输入时持续等待。'
        )
    tool_context.state["run_id"] = state["run_id"]
    tool_context.state["status"] = state["status"]
    return snapshot


def research_model_guard(callback_context, llm_request):
    """Keep entry routing scoped to this turn, including its tool response."""
    llm_request.contents = llm_request.contents[-1:]
    return None


def research_tool_guard(tool, args, tool_context):
    if tool.name != "execute_research" or args:
        return {"status": "error", "message": "研究必须通过程序控制器分派，禁止直接转移或绕过确认。"}
    return None


class YantraAgent(LlmAgent):
    """Native root agent; shared web instances never hold mutable research state."""

    engine: Any = Field(default=None, exclude=True)

    def __init__(self, engine: Any = None, **kwargs: Any):
        workers = getattr(engine, "adk_workers", None) if engine is not None else None
        super().__init__(
            name=kwargs.pop("name", "yantra"), engine=engine,
            model=ResearchModel(), description="证据核验型买方研究协调器；通过控制器调度四类 Worker。",
            instruction="使用 execute_research 推进研究并原样返回状态。未明确确认时保持等待。",
            tools=[FunctionTool(execute_research)], sub_agents=workers or build_workers(),
            output_key="research_snapshot", disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True, before_model_callback=research_model_guard,
            before_tool_callback=research_tool_guard, **kwargs,
        )
        if engine is not None:
            engine.adk_workers = self.sub_agents

    async def _run_async_impl(self, ctx: InvocationContext):
        token = _root_agent.set(self)
        try:
            with invocation_scope(ctx.invocation_id):
                async for event in super()._run_async_impl(ctx):
                    yield event
        finally:
            _root_agent.reset(token)


async def _run_agent(agent: BaseAgent, state: dict[str, Any], output_key: str) -> dict[str, Any]:
    service = InMemorySessionService()
    session_id = uuid4().hex
    app_name = agent.root_agent.name
    await service.create_session(app_name=app_name, user_id="yantra", session_id=session_id, state=state)
    runner = Runner(app=native_app(agent, name=app_name), session_service=service)
    try:
        result = None
        async for event in runner.run_async(user_id="yantra", session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text="执行限定任务。")])):
            value = event.actions.state_delta.get(output_key)
            if value is not None:
                result = json.loads(value) if isinstance(value, str) else value
        if result is None:
            raise RuntimeError("ADK execution did not return a result.")
        return result
    finally:
        await runner.close()


async def run_worker_loop(execution: dict[str, Any]) -> dict[str, Any]:
    engine, task = execution["engine"], execution["task"]
    extensions = getattr(engine, "extensions", None)
    if extensions is not None and extensions.snapshot.get("skill_loading") == "native_readonly_v1" and any(task.target in spec["roles"] and spec.get("loading")=="native" for spec in extensions.snapshot.get("skills", [])):
        from .adk_skills import FrozenSkills
        execution["skills"] = FrozenSkills(extensions, task.target, task.authorized_tools)
    if getattr(engine, "adk_workers", None) is None:
        engine.adk_workers = build_workers()
    if task.depth == 3:
        agent = WorkerAgent(role=task.target, engine=engine, name="child_" + task.task_id.replace("-", "_"))
        agent.tools = [tool for tool in agent.tools if tool.name in task.authorized_tools]
    else:
        agent = next(worker for worker in engine.adk_workers if worker.role == task.target)
    execution["agent_name"] = agent.name
    token = _worker_execution.set(execution)
    try:
        with execution_scope(engine, task, "worker"):
            return await _run_agent(agent, {"task": task.model_dump()}, "result")
    finally:
        _worker_execution.reset(token)


async def run_worker(engine: Any, task: dict[str, Any]) -> dict[str, Any]:
    if task["target"] not in ROLES:
        raise ValueError("Unknown worker role.")
    return await engine._work_task(task)


async def run_engine(engine: Any) -> dict[str, Any]:
    root = getattr(engine, "adk_root_agent", None)
    if root is None:
        root = YantraAgent(engine=engine)
        engine.adk_root_agent = root
    with execution_scope(engine, kind="entry"):
        await _run_agent(root, {}, "research_snapshot")
    return engine.state
