"""External API regressions use actual ADK; providers and materials are synthetic."""
import json
import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from google.adk.sessions import DatabaseSessionService, InMemorySessionService
from google.genai import types

from yantra.api import create_app
from yantra.backend import RealModelBackend
from yantra.engine import Engine
from yantra.adk_runtime import TraceStore
from test_adk_runtime import ScriptedProvider
from test_adk_structure import test_materials


class UsageProvider(ScriptedProvider):
    async def generate_content_async(self, llm_request, stream=False):
        async for response in super().generate_content_async(llm_request, stream):
            response.usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5, total_token_count=15)
            yield response


def send(client, session_id, text, user="owner"):
    response = client.post("/run_sse", json={"appName": "yantra", "userId": user, "sessionId": session_id,
        "newMessage": {"role": "user", "parts": [{"text": text}]}})
    if response.status_code != 200:
        raise AssertionError(response.text)
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    return next(json.loads(part["text"]) for event in reversed(events)
                for part in event.get("content", {}).get("parts", []) if part.get("text"))


class NativeTraceTests(unittest.TestCase):
    def test_external_trace_restart_scope_pagination_and_single_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "runs"
            inputs = test_materials(root / "inputs")
            backend = RealModelBackend("test", "https://unused.invalid/v1", "synthetic-secret")
            provider = UsageProvider()
            backend._model = provider

            def engine_factory(company="DEMO", run_id=None):
                return Engine.load(storage, run_id, backend=backend) if run_id else Engine.create(storage,
                    {"company": company, "horizon": "12个月", "cutoff": "2025-12-31"},
                    mode="test", backend=backend, materials_dir=inputs)

            def app():
                return create_app(session_service=DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{root / 'sessions.db'}"))

            with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(storage)}), patch("yantra.engine.create_adk_engine", side_effect=engine_factory):
                with TestClient(app()) as client:
                    session_id = client.post("/apps/yantra/users/owner/sessions", json={}).json()["id"]
                    first = send(client, session_id, "DEMO")
                    self.assertEqual(first["status"], "waiting")
                    path = f"/apps/yantra/users/owner/sessions/{session_id}/research/trace"
                    trace = client.get(path, params={"limit": 500}).json()
                    self.assertEqual(trace["run_id"], first["run_id"])
                    self.assertEqual(trace["accounting"], "observation_only")
                    worker_events = [e for e in trace["events"] if e["runner_kind"] == "worker" and e["kind"] == "event"]
                    self.assertTrue(worker_events)
                    calls = [tool["name"] for e in worker_events for tool in e["tools"] if tool["kind"] == "call"]
                    self.assertEqual(calls, ["read_materials", "extract_financials", "calculate_metrics", "read_materials"])
                    for event in worker_events:
                        self.assertTrue(event["task_id"].startswith("task-"))
                        self.assertTrue(event["session_id"])
                        self.assertTrue(event["invocation_id"])
                        self.assertTrue(event["parent_invocation_id"])
                    ids = [(e["session_id"], e["event_id"]) for e in trace["events"] if e["kind"] == "event"]
                    self.assertEqual(len(ids), len(set(ids)))
                    before = Engine.load(storage, first["run_id"]).state["budget"]
                    count = len(provider.payloads)
                    self.assertEqual(send(client, session_id, "继续")["status"], "waiting")
                    self.assertEqual(len(provider.payloads), count)
                    self.assertEqual(Engine.load(storage, first["run_id"]).state["budget"], before)
                    page = client.get(path, params={"limit": 2}).json()
                    following = client.get(path, params={"after": page["cursor"], "limit": 2}).json()
                    self.assertGreater(following["events"][0]["sequence"], page["cursor"])
                    task_id = worker_events[0]["task_id"]
                    filtered = client.get(path, params={"task_id": task_id}).json()
                    self.assertTrue(all(e["task_id"] == task_id for e in filtered["events"]))
                    stranger = client.post("/apps/yantra/users/stranger/sessions", json={"state": {"run_id": first["run_id"]}}).json()["id"]
                    self.assertEqual(client.get(f"/apps/yantra/users/stranger/sessions/{stranger}/research/trace").status_code, 404)
                    self.assertEqual(send(client, stranger, "继续", "stranger")["status"], "error")
                with TestClient(app()) as client:
                    self.assertEqual(client.get(path).json()["run_id"], first["run_id"])
                    final = send(client, session_id, "确认")
                    self.assertEqual(final["status"], "partial")
                    trace = client.get(path, params={"limit": 500}).json()
                    self.assertTrue(any(t["target"] == "verification" for t in trace["tasks"]))
                state = Engine.load(storage, first["run_id"]).state
                self.assertEqual(state["budget"]["model_calls"], len(provider.payloads))
                self.assertEqual(len([e for e in trace["events"] if "usage" in e]), len(provider.payloads))
                self.assertFalse(any(e["runner_kind"] == "decision" and e["task_id"] != "main" for e in trace["events"]))
                self.assertEqual(state["budget"]["tool_calls"], 6)
                self.assertTrue(state["artifacts"]["verification"]["data"]["passed"])
                raw = json.dumps(trace, ensure_ascii=False)
                for private in ("synthetic-secret", "测试 revenue", '"financials":', '"arguments":', "state_delta", "thought"):
                    self.assertNotIn(private, raw)

    def test_trace_ignores_incomplete_crash_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = TraceStore(temporary)
            store.append({"task_id": "task-one", "kind": "event"})
            with (Path(temporary) / "adk-trace.jsonl").open("a") as handle:
                handle.write('{"unfinished":')
            self.assertEqual(len(store.read()["events"]), 1)
            store.append({"task_id": "task-two", "kind": "event"})
            self.assertEqual(len(store.read()["events"]), 2)

    def test_native_artifacts_versions_scope_missing_files_and_ownership(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "runs"
            inputs = test_materials(root / "inputs")

            def factory(company="DEMO", run_id=None):
                return Engine.load(storage, run_id) if run_id else Engine.create(storage,
                    {"company": company, "horizon": "12个月", "cutoff": "2025-12-31"}, materials_dir=inputs)

            service = InMemorySessionService()
            with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(storage)}), patch("yantra.engine.create_adk_engine", side_effect=factory):
                with TestClient(create_app(session_service=service)) as client:
                    session_id = client.post("/apps/yantra/users/owner/sessions", json={}).json()["id"]
                    first = send(client, session_id, "DEMO")
                    send(client, session_id, "确认")
                    path = f"/apps/yantra/users/owner/sessions/{session_id}/artifacts"
                    self.assertEqual(client.get(path).json(), ["calculations.json", "report.json", "report.md"])
                    old = client.get(path + "/report.json/versions/0").json()
                    raw = base64.urlsafe_b64decode(old["inlineData"]["data"])
                    directory = storage / first["run_id"]
                    self.assertEqual(raw, (directory / "report-v1.json").read_bytes())
                    metadata = client.get(path + "/report.json/versions/0/metadata").json()
                    self.assertEqual(metadata["customMetadata"]["business_version"], 1)
                    self.assertEqual(metadata["customMetadata"]["owner"], "research_controller")
                    self.assertEqual(client.delete(path + "/report.json").status_code, 403)
                    self.assertEqual(client.post(path, json={"filename": "report.json", "artifact": {"text": "replace"}}).status_code, 403)
                    self.assertEqual(client.get(path + "/../state.json").status_code, 404)
                    stranger = client.post("/apps/yantra/users/stranger/sessions", json={}).json()["id"]
                    other = f"/apps/yantra/users/stranger/sessions/{stranger}/artifacts"
                    self.assertEqual(client.get(other).json(), [])
                    self.assertEqual(client.get(other + "/report.json").status_code, 404)
                    send(client, session_id, 'modify {"growth_rate":0.03}')
                    self.assertEqual(client.get(path + "/report.json/versions").json(), [0, 1])
                    self.assertEqual(client.get(path + "/report.json/versions/0").json(), old)
                with TestClient(create_app(session_service=service)) as client:
                    self.assertEqual(client.get(path + "/report.json/versions/0").json(), old)
                    self.assertEqual(client.get(path + "/report.json/versions/latest/metadata").json()["customMetadata"]["business_version"], 2)
                    (directory / "report-v1.json").unlink()
                    self.assertEqual(client.get(path + "/report.json/versions/0").status_code, 404)
                    self.assertEqual(client.get(path + "/report.json/versions/0/metadata").status_code, 200)
                    (directory / "report-v2.json").write_text("tampered")
                    self.assertEqual(client.get(path + "/report.json/versions/1").status_code, 404)
