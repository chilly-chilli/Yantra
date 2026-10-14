"""Declared web hierarchy and execution use the same isolated workers."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.cli.utils.base_agent_loader import BaseAgentLoader
from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.cli.utils.agent_loader import AgentLoader

from yantra.adk_app import ROLES, YantraAgent, run_engine, run_worker
from yantra.engine import Engine
from yantra.engine import PERMISSIONS


def test_materials(directory):
    """Temporary fictional inputs; do not restore removed project materials."""
    directory.mkdir()
    fields = ["revenue", "operating_profit", "net_profit", "operating_cash_flow", "capex", "debt", "cash", "shares"]
    materials = []
    for year, values in ((2024, [1000, 150, 110, 160, 60, 150, 50, 100]),
                         (2025, [1200, 180, 132, 192, 72, 150, 50, 100])):
        name = f"demo_fy{year}"
        text = ["虚构测试输入", "company DEMO", "currency CNY", "unit million", "scope consolidated"]
        text += [f"测试 {field}: {value}" for field, value in zip(fields, values)]
        (directory / f"{name}.txt").write_text("\n".join(text) + "\n")
        materials.append({"id": name, "file": f"{name}.txt", "date": f"{year}-12-31", "period": f"FY{year}",
                          "currency": "CNY", "unit": "million", "consolidated": True})
    (directory / "manifest.json").write_text(json.dumps({"company": "DEMO", "company_name": "测试公司（虚构）",
        "version": "structure-regression-test", "fictional": True, "adapter": "local-fixed-test",
        "materials": materials, "financial_fields": {field: i + 6 for i, field in enumerate(fields)}, "gaps": []}))
    return directory


class FixedLoader(BaseAgentLoader):
    def __init__(self, root):
        self.root = root

    def load_agent(self, name):
        if name != "yantra":
            raise ValueError("unknown test app")
        return self.root

    def list_agents(self):
        return ["yantra"]


class WebStructureTests(unittest.TestCase):
    def test_standard_api_persists_waiting_session_across_restart(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            inputs = test_materials(Path(temporary) / "inputs")
            storage = Path(temporary) / "runs"
            database = f"sqlite:///{temporary}/session.db"

            def create_app():
                return get_fast_api_app(agents_dir=str(project / "yantra"),
                    session_service_uri=database, use_local_storage=False, web=True)

            def send(client, session_id, text):
                response = client.post("/run_sse", json={"appName": "yantra", "userId": "restart",
                    "sessionId": session_id, "newMessage": {"role": "user", "parts": [{"text": text}]}})
                self.assertEqual(response.status_code, 200)
                events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
                return next(json.loads(part["text"]) for event in reversed(events)
                    for part in event.get("content", {}).get("parts", []) if part.get("text"))

            with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(storage), "YANTRA_MODE": "offline-demo",
                                         "YANTRA_MATERIALS_DIR": str(inputs)}), patch("google.adk.cli.utils.envs.load_dotenv_for_agent"):
                with TestClient(create_app()) as client:
                    self.assertIn("yantra", client.get("/list-apps").json())
                    session = client.post("/apps/yantra/users/restart/sessions", json={})
                    self.assertEqual(session.status_code, 200)
                    session_id = session.json()["id"]
                    first = send(client, session_id, "DEMO")
                    self.assertEqual(first["status"], "waiting")
                waiting = Engine.load(storage, first["run_id"]).state
                with TestClient(create_app()) as client:
                    session = client.get(f"/apps/yantra/users/restart/sessions/{session_id}")
                    self.assertEqual(session.status_code, 200)
                    self.assertEqual(session.json()["state"]["run_id"], first["run_id"])
                    self.assertEqual(send(client, session_id, "继续")["status"], "waiting")
                    self.assertEqual(Engine.load(storage, first["run_id"]).state["budget"], waiting["budget"])
                    final = send(client, session_id, "确认")
                    self.assertEqual(final["run_id"], first["run_id"])
                    self.assertEqual(final["status"], "partial")

    def test_entry_declares_four_leaf_workers_without_starting_a_run(self):
        from yantra.agent import root_agent

        self.assertIsNone(root_agent.engine)
        self.assertIsInstance(root_agent, LlmAgent)
        self.assertEqual([w.role for w in root_agent.sub_agents], list(ROLES))
        for worker in root_agent.sub_agents:
            self.assertIs(worker.parent_agent, root_agent)
            self.assertIsNone(worker.engine)
            self.assertEqual(worker.sub_agents, [])
            self.assertIsInstance(worker, LlmAgent)
            self.assertEqual([tool.name for tool in worker.tools], PERMISSIONS[worker.role])

    def test_adk_loader_discovers_package_and_direct_agent_directory(self):
        project = Path(__file__).resolve().parents[1]
        for directory in (project, project / "yantra"):
            loader = AgentLoader(str(directory))
            self.assertIn("yantra", loader.list_agents())
            root = loader.load_agent("yantra")
            self.assertIsInstance(root, App)
            root = root.root_agent
            self.assertIsInstance(root, LlmAgent)
            self.assertEqual([worker.role for worker in root.sub_agents], list(ROLES))

    def test_web_graph_and_saved_confirmation_flow(self):
        root = YantraAgent()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"YANTRA_RUNS_DIR": temporary}):
            storage = Path(temporary) / "runs"
            inputs = test_materials(Path(temporary) / "inputs")

            def create_engine(company="DEMO", run_id=None):
                if run_id:
                    return Engine.load(storage, run_id)
                return Engine.create(storage, {"company": company, "horizon": "12个月", "cutoff": "2025-12-31"}, materials_dir=inputs)

            app = get_fast_api_app(agents_dir=temporary, agent_loader=FixedLoader(root),
                                   use_local_storage=False, web=True)
            with patch("yantra.engine.create_adk_engine", side_effect=create_engine), TestClient(app) as client:
                graph = client.get("/dev/apps/yantra/graph")
                self.assertEqual(graph.status_code, 200)
                for worker in root.sub_agents:
                    self.assertIn(worker.name, graph.json()["dotSrc"])
                    self.assertIn(f"yantra -> {worker.name}", graph.json()["dotSrc"])
                    for tool in worker.tools:
                        self.assertIn(tool.name, graph.json()["dotSrc"])
                path = "/apps/yantra/users/test/sessions"
                session = client.post(path, json={})
                self.assertEqual(session.status_code, 200)
                session_id = session.json()["id"]

                def send(text):
                    response = client.post("/run_sse", json={"appName": "yantra", "userId": "test", "sessionId": session_id,
                        "newMessage": {"role": "user", "parts": [{"text": text}]}})
                    self.assertEqual(response.status_code, 200)
                    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
                    return next(json.loads(part["text"]) for event in reversed(events)
                                for part in event.get("content", {}).get("parts", []) if part.get("text"))

                first = send("DEMO")
                self.assertEqual(first["status"], "waiting")
                run_id = first["run_id"]
                waiting = Engine.load(storage, run_id).state
                second = send("继续")
                self.assertEqual(second["status"], "waiting")
                self.assertEqual(Engine.load(storage, run_id).state["budget"], waiting["budget"])
                final = send("确认")
                self.assertEqual(final["run_id"], run_id)
                self.assertEqual(final["status"], "partial")
                self.assertEqual(len(final["report"]), 8)
                for worker in root.sub_agents:
                    self.assertIsNone(worker.engine)
                self.assertIsNone(root.engine)


class WorkerBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_workers_bind_each_task_to_its_own_engine(self):
        root = YantraAgent()
        observed = []

        class TaskEngine:
            def __init__(self, marker):
                self.marker = marker
                self.adk_workers = root.sub_agents

            async def _work_task(self, task):
                await asyncio.sleep(.01)
                observed.append((self.marker, task["task_id"]))
                return {"task_id": task["task_id"], "marker": self.marker}

        first, second = await asyncio.gather(
            run_worker(TaskEngine("first"), {"target": "finance", "task_id": "first"}),
            run_worker(TaskEngine("second"), {"target": "finance", "task_id": "second"}))
        self.assertEqual(first, {"task_id": "first", "marker": "first"})
        self.assertEqual(second, {"task_id": "second", "marker": "second"})
        self.assertCountEqual(observed, [("first", "first"), ("second", "second")])
        self.assertTrue(all(worker.engine is None for worker in root.sub_agents))

    async def test_cli_root_reuses_its_workers_across_pause_and_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = test_materials(Path(temporary) / "inputs")
            engine = Engine.create(Path(temporary) / "runs", {"company": "DEMO", "horizon": "12个月", "cutoff": "2025-12-31"}, materials_dir=inputs)
            self.assertEqual((await run_engine(engine))["status"], "waiting")
            root = engine.adk_root_agent
            self.assertIs(engine.adk_workers, root.sub_agents)
            self.assertEqual((await run_engine(engine))["status"], "waiting")
            self.assertIs(engine.adk_root_agent, root)
            self.assertEqual((await engine.respond("confirm"))["status"], "partial")
            self.assertEqual(engine.state["budget"]["tool_calls"], 6)
            self.assertTrue(engine.state["artifacts"]["verification"]["data"]["passed"])


if __name__ == "__main__":
    unittest.main()
