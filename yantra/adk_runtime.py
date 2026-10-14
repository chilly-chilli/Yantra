"""Native ADK lifecycle observation; the controller remains the only ledger writer."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from google.adk.apps import App
from google.adk.plugins.base_plugin import BasePlugin


_scope = ContextVar("yantra_adk_scope", default=None)
_invocation = ContextVar("yantra_adk_invocation", default=None)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@contextmanager
def execution_scope(engine, task=None, kind="decision"):
    task = task.model_dump() if hasattr(task, "model_dump") else task
    token = _scope.set({"engine": engine, "task": task, "kind": kind})
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def invocation_scope(invocation_id):
    token = _invocation.set(invocation_id)
    try:
        yield
    finally:
        _invocation.reset(token)


class TraceStore:
    """Durable, content-free projections, separate from business events and state."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def append(self, record):
        with (self.directory / "adk-trace.jsonl").open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            end = handle.seek(0, os.SEEK_END)
            if end:
                handle.seek(end - 1)
                if handle.read(1) != b"\n":
                    position = end
                    while position:
                        start = max(0, position - 4096)
                        handle.seek(start)
                        tail = handle.read(position - start)
                        newline = tail.rfind(b"\n")
                        if newline >= 0:
                            handle.truncate(start + newline + 1)
                            break
                        position = start
                    else:
                        handle.truncate(0)
            handle.write((json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())

    def bind(self, session):
        scope = {"app_name": session.app_name, "user_id": session.user_id, "session_id": session.id}
        with (self.directory / ".adk-scopes.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.directory / "adk-scopes.json"
            scopes = json.loads(path.read_text()) if path.exists() else []
            if scope not in scopes:
                scopes.append(scope)
                temporary = path.with_suffix(".tmp")
                with temporary.open("w") as handle:
                    json.dump(scopes, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(path)

    def permits(self, app_name, user_id, session_id):
        path = self.directory / "adk-scopes.json"
        return path.exists() and {"app_name": app_name, "user_id": user_id, "session_id": session_id} in json.loads(path.read_text())

    def read(self, *, after=0, limit=200, task_id=None):
        path = self.directory / "adk-trace.jsonl"
        records = []
        cursor = after
        if path.exists():
            with path.open() as handle:
                fcntl.flock(handle, fcntl.LOCK_SH)
                for index, line in enumerate(handle, 1):
                    if index <= after:
                        continue
                    # A crash can leave an unfinished last write, never an event.
                    if not line.endswith("\n"):
                        break
                    record = json.loads(line)
                    cursor = index
                    if task_id is None or record.get("task_id") == task_id:
                        records.append(record | {"sequence": index})
                        if len(records) >= limit:
                            break
        return {"events": records, "cursor": cursor, "accounting": "observation_only"}


def run_directory(storage, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"run-[a-z0-9-]+", run_id):
        raise ValueError("invalid run id")
    root = Path(storage).resolve()
    directory = (root / run_id).resolve()
    if not directory.is_relative_to(root) or not (directory / "state.json").is_file():
        raise ValueError("run unavailable")
    return directory


def event_projection(event):
    """Never serialize content, arguments, state deltas, provider errors or thoughts."""
    result = {"event_id": event.id, "author": event.author, "partial": bool(event.partial),
              "final": event.is_final_response(), "tools": []}
    for part in (event.content.parts or []) if event.content else []:
        if part.function_call:
            call = part.function_call
            result["tools"].append({"kind": "call", "name": call.name, "call_id": call.id,
                                    "arguments_hash": _digest(call.args)})
        if part.function_response:
            response = part.function_response
            value = response.response or {}
            result["tools"].append({"kind": "response", "name": response.name, "call_id": response.id,
                                    "ok": value.get("ok") if isinstance(value.get("ok"), bool) else None,
                                    "error": bool(value.get("error")), "result_hash": _digest(value)})
        if part.text and not part.thought:
            result["text_hash"] = _digest(part.text)
            result["text_bytes"] = len(part.text.encode())
            try:
                value = json.loads(part.text)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and value.get("status") in {"complete", "completed", "partial", "failed", "blocked", "waiting", "running", "cancelled", "error"}:
                result["result_status"] = value["status"]
    if event.usage_metadata:
        usage = event.usage_metadata
        result["usage"] = {"input_tokens": usage.prompt_token_count,
                           "output_tokens": usage.candidates_token_count,
                           "total_tokens": usage.total_token_count,
                           "cached_tokens": usage.cached_content_token_count}
    if event.error_code:
        result["error"] = "invalid_action" if event.error_code == "YANTRA_INVALID_ACTION" else "provider"
    return result


class ResearchLifecyclePlugin(BasePlugin):
    """Every owned Runner gets this plugin; it neither edits events nor settles fees."""

    def __init__(self, scope=None, parent_invocation_id=None):
        super().__init__(name="yantra_lifecycle")
        self.scope = scope
        self.parent_invocation_id = parent_invocation_id
        self.pending = {}
        self.close_context = None
        self.resources_closed = False
        self.finished = False

    def _directory(self, ctx):
        if self.scope:
            return self.scope["engine"].directory
        run_id = ctx.session.state.get("run_id")
        try:
            directory = run_directory(os.getenv("YANTRA_RUNS_DIR", "runs"), run_id)
        except ValueError:
            return None
        return directory if TraceStore(directory).permits(ctx.app_name, ctx.user_id, ctx.session.id) else None

    def _record(self, ctx, kind, **payload):
        task = (self.scope or {}).get("task") or {}
        record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind,
                  "task_id": task.get("task_id", "main"), "parent_task_id": task.get("parent_task_id"),
                  "role": task.get("target", "main"), "depth": task.get("depth", 1),
                  "runner_kind": (self.scope or {}).get("kind", "entry"),
                  "app_name": ctx.app_name, "session_id": ctx.session.id,
                  "invocation_id": ctx.invocation_id, "parent_invocation_id": self.parent_invocation_id,
                  **payload}
        directory = self._directory(ctx)
        if directory is None:
            self.pending.setdefault(ctx.invocation_id, []).append(record)
            return
        store = TraceStore(directory)
        run_id = directory.name
        for previous in self.pending.pop(ctx.invocation_id, []):
            store.append(previous | {"run_id": run_id})
        store.append(record | {"run_id": run_id})

    async def before_run_callback(self, *, invocation_context):
        if self.scope and self.scope["kind"] == "worker":
            self.close_context = invocation_context
        self._record(invocation_context, "invocation_started")

    async def on_event_callback(self, *, invocation_context, event):
        self._record(invocation_context, "event", **event_projection(event))

    async def _close_resources(self, invocation_context):
        task = (self.scope or {}).get("task") or {}
        if not self.resources_closed and self.scope and self.scope["kind"] == "worker" and (task.get("depth") == 2 or self.parent_invocation_id is None):
            extensions = getattr(self.scope["engine"], "extensions", None)
            if extensions is not None:
                await extensions.close()
                if invocation_context is not None:
                    self._record(invocation_context, "resources_closed")
            self.resources_closed = True

    async def after_run_callback(self, *, invocation_context):
        await self._close_resources(invocation_context)
        self.finished = True
        self._record(invocation_context, "invocation_finished")
        self.pending.pop(invocation_context.invocation_id, None)

    async def on_run_error_callback(self, *, invocation_context, error):
        self._record(invocation_context, "invocation_error", error_type=type(error).__name__)

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        self._record(callback_context._invocation_context, "model_error", error_type=type(error).__name__)

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        self._record(tool_context._invocation_context, "tool_error", tool=tool.name, error_type=type(error).__name__)

    async def close(self):
        try:
            await self._close_resources(self.close_context)
            if self.close_context is not None and not self.finished:
                self._record(self.close_context, "invocation_interrupted")
        finally:
            self.close_context = None
            self.pending.clear()


def native_app(agent, *, name=None):
    return App(name=name or agent.root_agent.name, root_agent=agent,
               plugins=[ResearchLifecyclePlugin(_scope.get(), _invocation.get())])
