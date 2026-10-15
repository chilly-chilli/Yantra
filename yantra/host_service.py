"""Local host boundary: durable intents and observation, one existing research writer."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from .cli_contract import CLIError, exception_error
from .configuration import ConfigStore
from .host_contract import INPUTS, schema
from . import host_outputs


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def state_revision(state):
    return hashlib.sha256(canonical(state).encode()).hexdigest()


def pending_id(state):
    return state_revision(state["pending_user"]) if state.get("pending_user") else None


def process_exists(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def progress(state, execution):
    """Project persisted controller observations, never a completion percentage."""
    if state is None:
        return {"phase": "preparing" if execution["state"] == "active" else execution["state"],
                "task_count": 0, "result_count": 0, "active_tasks": [], "gaps": []}
    tasks, results = state.get("tasks", []), state.get("results", [])
    finished = {item["task_id"] for item in results}
    pending = state.get("pending_user") or {}
    phase = state["status"]
    active = []
    if phase == "waiting":
        phase = "waiting_identity" if pending.get("kind") == "identity" else "waiting_confirmation"
    elif phase == "running":
        phase = "preparing" if state.get("raw_materials") and not state.get("prepared") else "coordinator"
        flight = state.get("in_flight") or {}
        task_id = (state.get("in_flight_tool") or {}).get("task_id")
        if flight.get("reservation"):
            receipt = next((item for item in state.get("model_ledger", []) if item["id"] == flight["reservation"]), {})
            task_id = receipt.get("task_id", task_id)
        elif flight.get("method") == "worker" and tasks:
            task_id = tasks[-1]["task_id"]
        task = next((item for item in tasks if item["task_id"] == task_id), None)
        if task and task_id not in finished and execution["state"] == "active":
            active = [{key: task.get(key) for key in ("task_id", "target", "purpose", "depth")}]
            phase = task["target"]
    if execution["state"] in {"interrupted", "failed"} and state["status"] == "running":
        phase = execution["state"]
    gaps = list((state.get("report") or {}).get("投资结论摘要", {}).get("gaps", []))
    gaps += state.get("stop_reasons", []) + state.get("input_snapshot", {}).get("manifest", {}).get("gaps", [])
    for artifact in state.get("artifacts", {}).values():
        gaps += artifact.get("unresolved", []) + artifact.get("data", {}).get("gaps", [])
    return {"phase": phase, "task_count": len(tasks), "result_count": len(results),
            "active_tasks": active, "gaps": sorted(set(gaps))}


class HostService:
    def __init__(self, workspace, *, config=None, runs_dir=None, knowledge_dir=None, env_file=None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.settings = ConfigStore(config or os.getenv("YANTRA_CONFIG") or self.workspace / ".yantra/config.json")
        self.runs = Path(runs_dir or os.getenv("YANTRA_RUNS_DIR") or self.workspace / "runs").expanduser().resolve()
        self.knowledge = Path(knowledge_dir or os.getenv("YANTRA_KNOWLEDGE_DIR") or self.workspace / ".yantra/knowledge").expanduser().resolve()
        self.env_file = Path(env_file).expanduser().resolve() if env_file else None
        self.control = self.runs / ".host"

    @contextmanager
    def database(self):
        self.control.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.control / "requests.sqlite", timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.executescript("""
                CREATE TABLE IF NOT EXISTS submissions (
                    run_id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at REAL NOT NULL, execution TEXT NOT NULL,
                    job TEXT, pid INTEGER, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS operations (
                    run_id TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    payload TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(run_id, operation_id));
            """)
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def gate(self, run_id, *, blocking=False):
        self.control.mkdir(parents=True, exist_ok=True)
        with (self.control / (run_id + ".lock")).open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                raise CLIError("run_busy", "执行者仍在运行；查询后再修改", retryable=True, exit_code=1) from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def row(self, run_id):
        with self.database() as db:
            return db.execute("SELECT * FROM submissions WHERE run_id=?", (run_id,)).fetchone()

    def directory(self, run_id):
        target = self.runs / run_id
        if target.resolve().parent != self.runs:
            raise CLIError("access_denied", "运行路径不可跨越配置的本地工作区")
        # Local workspace sharing is explicit. ADK-owned runs require the ADK session.
        if (target / "adk-scopes.json").exists() and self.row(run_id) is None:
            raise CLIError("session_scope_required", "该研究属于 ADK Session；请从原 Session 访问")
        return target

    def state(self, run_id):
        from .cli import read_state
        self.directory(run_id)
        return read_state(self.runs, run_id)

    def execution(self, row):
        if row is None:
            return {"state": "unmanaged", "notice": "旧入口任务；此接口没有执行者存活证据"}
        phase = row["execution"]
        if phase in {"active", "starting"}:
            try:
                with self.gate(row["run_id"]):
                    # The executor may have finished between the first read and
                    # acquiring its lock. Recheck before declaring interruption.
                    row = self.row(row["run_id"])
                    phase = row["execution"]
                    if phase == "active":
                        phase = "interrupted"
                    elif phase == "starting" and not process_exists(row["pid"]):
                        phase = "interrupted"
            except CLIError:
                phase = "starting" if phase == "starting" else "active"
        return {"state": phase, "error": json.loads(row["error"]) if row["error"] else None,
                "cancellation_requested": bool(row["cancel_requested"])}

    def status(self, request):
        from .cli import run_result
        row = self.row(request.run_id)
        directory = self.directory(request.run_id)
        state = None
        if not (directory / "state.json").exists():
            if row is None:
                raise CLIError("run_not_found", "研究不存在；请检查 workspace、runs-dir 和 run_id")
            intent = json.loads(row["payload"])
            result = {"run_id": request.run_id, "status": "accepted", "revision": row["request_hash"], "outputs": [],
                      "mode": intent["mode"], "research": {key: intent[key] for key in ("company", "questions", "topics")},
                      "budget": None, "limits": None, "requested_limits": intent["limits"]}
        else:
            state = self.state(request.run_id)
            args = SimpleNamespace(runs_dir=self.runs, env_file=self.env_file)
            result = run_result(state, args, self.settings)
            result.update(revision=state_revision(state), pending_id=pending_id(state),
                          assumptions=state["assumptions"], qualification=host_outputs.qualification(state),
                          outputs=host_outputs.public_index(directory))
        result["execution"] = self.execution(row)
        result["progress"] = progress(state, result["execution"])
        # Preserve the durable business state separately when no worker can advance it.
        if result["execution"]["state"] in {"interrupted", "failed", "cancelled"} and result["status"] in {"running", "accepted"}:
            result["saved_research_status"] = result["status"]
            result["status"] = result["execution"]["state"]
        if row:
            with self.database() as db:
                operations = db.execute("SELECT operation_id,result FROM operations WHERE run_id=? ORDER BY rowid DESC LIMIT 20", (request.run_id,)).fetchall()
            result["operations"] = [{"operation_id": item["operation_id"], **json.loads(item["result"])} for item in operations]
            result["parent_run_id"] = json.loads(row["payload"]).get("parent_run_id")
            result["next_actions"] = [{"action": "status", "input": {"run_id": request.run_id}},
                *[{"action": "read", "input": {"run_id": request.run_id, "version": item["version"], "artifact": "report.md"}}
                  for item in result["outputs"][-1:]]]
            if not row["cancel_requested"] and result["status"] in {"accepted", "running", "waiting", "interrupted"}:
                result["next_actions"].append({"action": "cancel", "input": {"run_id": request.run_id}})
            idle = result["execution"]["state"] not in {"starting", "active"}
            uncertain = any(item["receipt"] == "accepted" for item in result["operations"]) or bool(state and (state.get("in_flight") or state.get("in_flight_tool")))
            within_deadline = not state or not state.get("deadline") or datetime.fromisoformat(state["deadline"]).timestamp() > time.time() + state["limits"]["close_seconds"]
            if idle and not uncertain and not row["cancel_requested"] and within_deadline:
                common = {"run_id": request.run_id, "expected_revision": result["revision"], "required_input": ["operation_id"]}
                if state and state["status"] != "cancelled" and state.get("artifacts", {}).get("proposal"):
                    parameters_available = bool(state["assumptions"]["parameters"])
                    decisions = ["modify"] if parameters_available else []
                    if state["status"] == "waiting" or state.get("investment_logic_version"):
                        decisions += (["confirm"] if parameters_available else []) + ["reject"]
                    if decisions:
                        result["next_actions"].append({"action": "respond", **common, "pending_id": pending_id(state), "decisions": decisions})
                if state and state["status"] == "waiting" and (state.get("pending_user") or {}).get("kind") == "identity":
                    result["next_actions"].append({"action": "respond", **common, "pending_id": pending_id(state), "decisions": ["select_identity"]})
                if state is None or result["status"] == "interrupted" or (state.get("raw_materials") and state["status"] in {"partial", "failed"}):
                    result["next_actions"].append({"action": "resume", **common, "continue_research": bool(state and state["status"] in {"partial", "failed"})})
        result["follow_up"] = "新增材料使用 submit + 新幂等键 + parent_run_id，重新确定预算和截止；resume 保留原限制"
        return result

    def local_path(self, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.workspace / path).resolve()

    def normalize(self, request):
        data = request.model_dump(mode="json")
        for field in ("demo_materials_dir", "framework_file"):
            if data[field]:
                data[field] = str(self.local_path(data[field]))
        data["materials"] = [value if urlsplit(value).scheme in {"http", "https"} else str(self.local_path(value)) for value in data["materials"]]
        return data

    def validate_paths(self, data):
        paths = [p for p in data["materials"] if urlsplit(p).scheme not in {"http", "https"}]
        paths += [data[k] for k in ("demo_materials_dir", "framework_file") if data.get(k)]
        for path in paths:
            if not Path(path).exists():
                raise CLIError("material_path_invisible", "材料路径在 Yantra 所在机器不可见；首版仅支持共享本地路径", field="materials")
            if not os.access(path, os.R_OK):
                raise CLIError("material_permission_denied", "材料存在但不可读；请授予该路径读取权限", field="materials")

    def submit(self, request):
        data = self.normalize(request)
        request_hash = state_revision({"request": data, "config": str(self.settings.path),
                                      "env_file": str(self.env_file) if self.env_file else None,
                                      "knowledge": str(self.knowledge)})
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM submissions WHERE idem=?", (request.idempotency_key,)).fetchone()
            if row:
                if row["request_hash"] != request_hash:
                    raise CLIError("idempotency_conflict", "同一幂等键已有不同请求；不能创建第二项研究", field="idempotency_key")
                run_id, reused = row["run_id"], True
            else:
                self.validate_paths(data)
                if request.parent_run_id:
                    self.state(request.parent_run_id)
                run_id, reused = "run-" + uuid4().hex[:12], False
                db.execute("INSERT INTO submissions(run_id,idem,request_hash,payload,created_at,execution) VALUES(?,?,?,?,?,?)",
                           (run_id, request.idempotency_key, request_hash, canonical(data), time.time(), "accepted"))
            db.commit()
        if not reused:
            with self.gate(run_id):
                if self.row(run_id)["cancel_requested"]:
                    self.finish_job(run_id, "cancelled")
                else:
                    self.launch(run_id, "initial")
        return {"run_id": run_id, "receipt": "persisted", "reused": reused,
                "request_sha256": request_hash, "execution": self.execution(self.row(run_id)),
                "next_actions": [{"action": "status", "input": {"run_id": run_id}}]}

    def launch(self, run_id, job):
        command = [sys.executable, "-m", "yantra.host_worker", "--workspace", str(self.workspace),
                   "--config", str(self.settings.path), "--runs-dir", str(self.runs), "--knowledge-dir", str(self.knowledge), "--run-id", run_id]
        if self.env_file:
            command += ["--env-file", str(self.env_file)]
        with self.database() as db:
            db.execute("UPDATE submissions SET execution='starting', job=?, pid=NULL, error=NULL WHERE run_id=?", (job, run_id))
            db.commit()
        try:
            # No pipe or shell binds execution to the requesting host/MCP session.
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + (
                os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
            with open(os.devnull, "wb") as sink:
                child = subprocess.Popen(command, cwd=self.workspace, stdin=subprocess.DEVNULL, stdout=sink,
                                         stderr=sink, start_new_session=True, close_fds=True, env=environment)
            with self.database() as db:
                db.execute("UPDATE submissions SET pid=? WHERE run_id=?", (child.pid, run_id))
                db.commit()
        except OSError as exc:
            error = (CLIError("execution_permission_denied", "宿主存在命令入口但拒绝执行；检查指定命令及研究目录权限").error
                     if isinstance(exc, PermissionError) else
                     CLIError("executable_missing", "Python执行入口不存在；使用已安装环境的绝对路径").error
                     if isinstance(exc, FileNotFoundError) else exception_error(exc).error)
            if job.startswith("op:"):
                with self.database() as db:
                    db.execute("UPDATE operations SET result=? WHERE run_id=? AND operation_id=?",
                               (canonical({"receipt": "rejected", "error": error}), run_id, job.removeprefix("op:")))
                    db.commit()
            self.finish_job(run_id, "failed", error)

    def finish_job(self, run_id, phase, error=None):
        with self.database() as db:
            db.execute("UPDATE submissions SET execution=?,error=? WHERE run_id=?", (phase, canonical(error) if error else None, run_id))
            db.commit()

    def list(self, request):
        from .host_contract import RunInput
        # IDs are stable cursors; directories include existing CLI runs without importing them.
        identifiers = sorted({p.name for p in self.runs.glob("run-*") if p.is_dir()})
        with self.database() as db:
            identifiers = sorted(set(identifiers) | {r[0] for r in db.execute("SELECT run_id FROM submissions")})
        rows = []
        for identifier in identifiers:
            if request.after and identifier <= request.after:
                continue
            try:
                item = self.status(RunInput(run_id=identifier))
            except CLIError as exc:
                if exc.error["code"] in {"session_scope_required", "access_denied"}:
                    continue
                raise
            rows.append({key: item.get(key) for key in ("run_id", "status", "research", "execution", "revision", "qualification", "progress")})
            if len(rows) > request.limit:
                break
        return {"runs": rows[:request.limit], "next_cursor": rows[request.limit-1]["run_id"] if len(rows) > request.limit else None}

    def change(self, operation, request):
        payload = {"action": operation, **request.model_dump(mode="json")}
        sha = state_revision(payload)
        # Return a previous receipt before acquiring the active executor's lock.
        with self.database() as db:
            prior = db.execute("SELECT * FROM operations WHERE run_id=? AND operation_id=?", (request.run_id, request.operation_id)).fetchone()
        if prior:
            if prior["request_hash"] != sha:
                raise CLIError("operation_conflict", "操作标识已用于不同请求", field="operation_id")
            return {"run_id": request.run_id, "operation_id": request.operation_id, **json.loads(prior["result"])}
        with self.gate(request.run_id):
            row = self.row(request.run_id)
            if row is None:
                raise CLIError("host_submission_required", "修改／恢复旧运行请使用原 CLI 或所属 ADK Session；宿主入口只执行自己提交的研究")
            if row["execution"] == "starting" and process_exists(row["pid"]):
                raise CLIError("run_busy", "执行者正在启动；请先查询", retryable=True, exit_code=1)
            with self.database() as db:
                unresolved = db.execute("SELECT result FROM operations WHERE run_id=?", (request.run_id,)).fetchall()
            if any(json.loads(item["result"])["receipt"] == "accepted" for item in unresolved):
                raise CLIError("operation_outcome_unknown", "上次操作中断且未保存最终收据；先核对状态与成果，不自动重放或覆盖")
            state = self.state(request.run_id) if (self.directory(request.run_id) / "state.json").exists() else None
            if state is None and operation != "resume":
                raise CLIError("invalid_run_state", "研究尚未创建，无法响应；可明确 resume 重启执行者")
            if (state_revision(state) if state else row["request_hash"]) != request.expected_revision:
                raise CLIError("revision_conflict", "研究修订已改变；先查询最新状态", field="expected_revision")
            if state and (state.get("in_flight_tool") or state.get("in_flight")):
                raise CLIError("external_outcome_unknown", "中断操作的结果未知；保留预算且禁止自动重放，请读取现有成果或新建后续研究")
            if row["cancel_requested"] or (state and state["status"] == "cancelled"):
                raise CLIError("invalid_run_state", "已取消研究不能恢复")
            if operation == "respond" and pending_id(state) != request.pending_id:
                raise CLIError("pending_conflict", "待处理事项已改变；使用当前 pending_id")
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT * FROM operations WHERE run_id=? AND operation_id=?", (request.run_id, request.operation_id)).fetchone()
                if prior:
                    if prior["request_hash"] != sha:
                        raise CLIError("operation_conflict", "操作标识已用于不同请求")
                    return {"run_id": request.run_id, "operation_id": request.operation_id, **json.loads(prior["result"])}
                result = {"receipt": "accepted", "expected_revision": request.expected_revision}
                db.execute("INSERT INTO operations VALUES(?,?,?,?,?)", (request.run_id, request.operation_id, sha, canonical(payload), canonical(result)))
                db.commit()
            self.launch(request.run_id, "op:" + request.operation_id)
        return {"run_id": request.run_id, "operation_id": request.operation_id, **result}

    def cancel(self, request):
        row = self.row(request.run_id)
        directory = self.directory(request.run_id)
        if row is None:
            raise CLIError("host_submission_required", "旧任务请使用原 CLI 或所属 Session 取消")
        state = self.state(request.run_id) if (directory / "state.json").exists() else None
        if state and state["status"] in {"completed", "partial", "failed", "cancelled"}:
            return {"run_id": request.run_id, "cancellation": "cancelled" if state["status"] == "cancelled" else "already_terminal"}
        with self.database() as db:
            db.execute("UPDATE submissions SET cancel_requested=1 WHERE run_id=?", (request.run_id,))
            db.commit()
        (self.control / (request.run_id + ".cancel")).touch()
        if directory.is_dir():
            (directory / "cancel.request").touch()
        stopped = False
        try:
            with self.gate(request.run_id):
                if state is None:
                    self.finish_job(request.run_id, "cancelled")
                    stopped = True
                else:
                    self.launch(request.run_id, "cancel")
        except CLIError as exc:
            if exc.error["code"] != "run_busy":
                raise
        return {"run_id": request.run_id, "cancellation": "cancelled" if stopped else "request_accepted",
                "notice": "查询 status 确认实际停止；请求接受不代表研究已取消"}

    def doctor(self, request):
        import importlib.metadata
        from .models import ModelCatalog
        checks = []
        def check(code, ready, fix):
            checks.append({"code": code, "ready": ready, "fix": None if ready else fix})
        check("command_tool_" + request.command_tool, request.command_tool == "available", "宿主提供命令工具权限，或配置 Yantra stdio MCP；诊断不能自行探测宿主工具权限")
        check("execution_entry_missing", Path(sys.executable).is_file(), "安装 Python 3.11+ 和 yantra-research")
        for dependency, expected in (("google-adk", "2.11.0"), ("mcp", "1.30.0")):
            try:
                ready = importlib.metadata.version(dependency) == expected
            except importlib.metadata.PackageNotFoundError:
                ready = False
            check("dependency_" + dependency, ready, "从项目固定依赖安装；不要升级 ADK")
        check("workspace_invisible", self.workspace.is_dir(), "使用 Yantra 执行机器上可见的绝对 workspace 路径")
        check("workspace_permission_denied", os.access(self.workspace, os.R_OK | os.W_OK | os.X_OK), "仅授予指定研究目录读写权限")
        for value in request.materials:
            path = self.local_path(value)
            check("material_path_invisible", path.exists(), "首版无自动上传；提供共享本地路径")
            if path.exists():
                check("material_permission_denied", os.access(path, os.R_OK), "授予该材料路径读取权限")
        try:
            profile = ModelCatalog(self.settings).resolve("llm")
            names = [profile["api_key_env"]] if profile else ["MODEL_ID", "MODEL_BASE_URL", "MODEL_API_KEY"]
            if self.env_file:
                from dotenv import dotenv_values
                if not self.env_file.is_file():
                    raise CLIError("env_file_missing", "环境文件不存在")
                values = {**os.environ, **{k: v for k, v in dotenv_values(self.env_file).items() if v is not None}}
            else:
                values = os.environ
            check("credentials_missing", all(values.get(name) for name in names), "在本地环境文件填写凭据变量；不要粘贴到聊天或工具响应")
            pricing = (profile or {}).get("pricing") or all(values.get(name) for name in ("MODEL_INPUT_CNY_PER_MILLION", "MODEL_OUTPUT_CNY_PER_MILLION", "MODEL_PRICE_SOURCE", "MODEL_PRICE_AS_OF"))
            check("pricing_missing", bool(pricing), "通过 models add 或本地环境变量设置带来源和日期的定价")
        except Exception as exc:
            checks.append({"code": exception_error(exc).error["code"], "ready": False, "fix": "检查本地配置格式与环境文件路径"})
        return {"interface_version": "1", "checks": checks, "network": "not_probed", "research": "not_executed",
                "paths": {"workspace": str(self.workspace), "config": str(self.settings.path), "runs": str(self.runs),
                          "knowledge": str(self.knowledge)},
                "route": "skill_cli" if request.command_tool == "available" else "skill_mcp" if request.mcp_available else "setup_required",
                "lifecycle": "detached local process; requires host OS to retain it; no reboot auto-resume",
                "host_billing": "宿主费用独立；Yantra 不能读取或控制宿主账单"}

    def call(self, operation, value):
        if operation not in INPUTS:
            raise CLIError("operation_not_found", "操作不存在；请读取 schema")
        request = INPUTS[operation].model_validate(value)
        if operation == "schema":
            return schema()
        if operation == "read":
            directory = self.directory(request.run_id)
            self.state(request.run_id)
            return host_outputs.read(directory, request)
        if operation in {"respond", "resume"}:
            return self.change(operation, request)
        return getattr(self, operation)(request)
