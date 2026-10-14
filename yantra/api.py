"""Local ADK API with a session-scoped research trace endpoint."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse
from google.adk.auth.credential_service.in_memory_credential_service import InMemoryCredentialService
from google.adk.cli.api_server import ApiServer
from google.adk.cli.utils.base_agent_loader import BaseAgentLoader
from google.adk.evaluation.local_eval_set_results_manager import LocalEvalSetResultsManager
from google.adk.evaluation.local_eval_sets_manager import LocalEvalSetsManager
from google.adk.memory import InMemoryMemoryService
from google.adk.sessions import DatabaseSessionService

from .adk_app import YantraAgent
from .adk_artifacts import ResearchArtifactService
from .engine import TERMINAL
from .adk_runtime import TraceStore, native_app, run_directory


class YantraLoader(BaseAgentLoader):
    def __init__(self, root_agent=None):
        self.app = native_app(root_agent or YantraAgent(), name="yantra")

    def list_agents(self):
        return ["yantra"]

    def load_agent(self, name):
        if name != "yantra":
            raise ValueError("unknown app")
        return self.app


def create_app(*, session_service=None, root_agent=None):
    """ADK's routes and lifecycle, with no cloud, model or dotenv side effects."""
    storage = Path(os.getenv("YANTRA_RUNS_DIR", "runs")).resolve()
    local = storage / ".adk"
    local.mkdir(parents=True, exist_ok=True)
    service = session_service or DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{local / 'sessions.db'}")
    server = ApiServer(agent_loader=YantraLoader(root_agent), session_service=service,
        artifact_service=ResearchArtifactService(storage), memory_service=InMemoryMemoryService(),
        credential_service=InMemoryCredentialService(),
        eval_sets_manager=LocalEvalSetsManager(agents_dir=str(local)),
        eval_set_results_manager=LocalEvalSetResultsManager(agents_dir=str(local)),
        agents_dir=str(Path(__file__).resolve().parent.parent))
    app = server.get_fast_api_app()

    @app.middleware("http")
    async def read_only_artifact(request, call_next):
        if request.method in {"POST", "DELETE"} and re.fullmatch(
            r"/apps/[^/]+/users/[^/]+/sessions/[^/]+/artifacts(?:/.*)?", request.url.path
        ):
            return JSONResponse(status_code=403, content={"detail": "Research artifacts are controller-owned and read-only."})
        return await call_next(request)

    @app.get("/apps/{app_name}/users/{user_id}/sessions/{session_id}/research/trace")
    async def trace(app_name: str, user_id: str, session_id: str,
                    after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500), task_id: str | None = None):
        session = await service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        try:
            directory = run_directory(storage, session.state.get("run_id") if session else None)
            store = TraceStore(directory)
            if not store.permits(app_name, user_id, session_id):
                raise ValueError("scope unavailable")
        except ValueError:
            raise HTTPException(404, "Research trace unavailable in this session.") from None
        state = json.loads((directory / "state.json").read_text())
        statuses = {result["task_id"]: result["status"] for result in state["results"]}
        tasks = [{key: task.get(key) for key in ("task_id", "parent_task_id", "target", "depth")}
                 | {"status": statuses.get(task["task_id"], "interrupted" if state["status"] in TERMINAL else "running")}
                 for task in state["tasks"] if task_id is None or task["task_id"] == task_id]
        return {"run_id": state["run_id"], "status": state["status"], "tasks": tasks,
                **store.read(after=after, limit=limit, task_id=task_id)}

    return app
