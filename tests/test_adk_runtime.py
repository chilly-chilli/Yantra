"""Native ADK calls, controller guardrails and standard entry behavior."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import Field

from yantra.adk_app import YantraAgent, run_engine
from yantra.backend import RealModelBackend
from yantra.engine import Engine, create_adk_engine
from yantra.policies import OfflineDemoBackend
from yantra.schemas import MainAction
from test_adk_structure import test_materials


class ScriptedProvider(BaseLlm):
    """ADK provider test double; never makes a remote model request."""
    model: str = "test_provider"
    payloads: list[dict] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        payload = json.loads(llm_request.contents[-1].parts[0].text)
        self.payloads.append(payload)
        backend = OfflineDemoBackend()
        action = (await backend.worker(payload["task"], payload["local"]) if "task" in payload
                  else await backend.main(payload))
        yield LlmResponse(content=types.Content(role="model", parts=[
            types.Part(text=json.dumps(action, ensure_ascii=False))
        ]))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_adk_entry_selects_configured_model_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = test_materials(Path(temporary) / "inputs")
            backend = RealModelBackend("test", "https://unused.invalid/v1", "test-only")
            with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(Path(temporary) / "runs"),
                "YANTRA_MODE": "real-model", "YANTRA_MATERIALS_DIR": str(inputs)}), \
                    patch.object(RealModelBackend, "from_env", return_value=backend):
                engine = create_adk_engine()
                self.assertEqual(engine.state["mode"], "real-model")
                self.assertIs(engine.backend, backend)
                self.assertEqual(create_adk_engine(run_id=engine.state["run_id"]).state["mode"], "real-model")

    async def test_native_worker_function_events_and_real_backend_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = test_materials(Path(temporary) / "inputs")
            backend = RealModelBackend("test", "https://unused.invalid/v1", "test-only")
            provider = ScriptedProvider()
            backend._model = provider
            engine = Engine.create(Path(temporary) / "runs", {"company": "DEMO", "horizon": "12个月",
                "cutoff": "2025-12-31"}, mode="real-model", backend=backend, materials_dir=inputs)
            events = []
            original = Runner.run_async

            async def observe(runner, **kwargs):
                async for event in original(runner, **kwargs):
                    events.append(event)
                    yield event

            with patch.object(Runner, "run_async", observe):
                self.assertEqual((await run_engine(engine))["status"], "waiting")
                count = len(provider.payloads)
                self.assertEqual((await run_engine(engine))["status"], "waiting")
                self.assertEqual(len(provider.payloads), count)
                self.assertEqual((await engine.respond("confirm"))["status"], "partial")
            calls = [(event.author, part.function_call.name) for event in events if event.content
                     for part in event.content.parts or [] if part.function_call and event.author.endswith("_worker")]
            self.assertEqual(calls, [("finance_worker", "read_materials"), ("finance_worker", "extract_financials"),
                ("finance_worker", "calculate_metrics"), ("thesis_worker", "read_materials"),
                ("valuation_worker", "valuation"), ("verification_worker", "check_consistency")])
            responses = [part.function_response for event in events if event.content
                         for part in event.content.parts or [] if part.function_response
                         and part.function_response.name in {name for _, name in calls}]
            self.assertEqual(len(responses), 6)
            self.assertEqual(engine.state["budget"]["tool_calls"], 6)
            self.assertEqual(engine.state["budget"]["model_calls"], len(provider.payloads))
            self.assertTrue(engine.state["artifacts"]["verification"]["data"]["passed"])
            for payload in provider.payloads:
                if "task" in payload:
                    self.assertEqual(set(payload), {"task", "local"})
                    self.assertNotIn("research_snapshot", payload["local"])
                    self.assertNotIn("run_id", payload["local"])

    async def test_native_callbacks_and_budget_stop_execution(self):
        for limits, expected_calls in (({"total_tools": 0}, 0), ({"task_tools": 1}, 1)):
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as temporary:
                inputs = test_materials(Path(temporary) / "inputs")
                engine = Engine.create(Path(temporary) / "runs", {"company": "DEMO", "horizon": "12个月",
                    "cutoff": "2025-12-31"}, limits=limits, materials_dir=inputs)
                state = await run_engine(engine)
                self.assertIn(state["status"], {"partial", "failed"})
                self.assertEqual(state["budget"]["tool_calls"], expected_calls)
                self.assertFalse(state["assumptions"]["confirmed"])

    async def test_worker_cannot_request_recursive_transfer_or_unconfirmed_valuation(self):
        class UnauthorizedBackend(OfflineDemoBackend):
            def __init__(self, tool):
                self.tool = tool

            async def worker(self, task, local):
                return self._tool(self.tool, {})

        for name in ("transfer_to_agent", "delegate_task", "valuation"):
            with self.subTest(tool=name), tempfile.TemporaryDirectory() as temporary:
                inputs = test_materials(Path(temporary) / "inputs")
                engine = Engine.create(Path(temporary) / "runs", {"company": "DEMO", "horizon": "12个月",
                    "cutoff": "2025-12-31"}, mode="test", backend=UnauthorizedBackend(name), materials_dir=inputs)
                await run_engine(engine)
                self.assertEqual(engine.state["budget"]["tool_calls"], 0)
                self.assertIn("未授权工具或递归分派", engine.state["results"][0]["unresolved"][0])

    async def test_valuation_requires_confirmed_current_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = test_materials(Path(temporary) / "inputs")
            engine = Engine.create(Path(temporary) / "runs", {"company": "DEMO", "horizon": "12个月",
                "cutoff": "2025-12-31"}, materials_dir=inputs)
            await run_engine(engine)
            task = engine._make_task(MainAction(action="delegate_task", target="valuation", purpose="propose"))

            class UnconfirmedBackend(OfflineDemoBackend):
                async def worker(self, task, local):
                    return self._tool("valuation", {"parameters": task["assumptions"]["parameters"],
                                                  "assumptions_version": task["assumptions"]["version"]})

            engine.backend = UnconfirmedBackend()
            before = engine.state["budget"]["tool_calls"]
            result = await engine._work_task(task.model_dump())
            self.assertIn("当前已确认假设", result["unresolved"][0])
            self.assertEqual(engine.state["budget"]["tool_calls"], before)

    async def test_shared_web_root_isolates_sessions_and_supports_modification(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = test_materials(Path(temporary) / "inputs")
            storage = Path(temporary) / "runs"
            root = YantraAgent()
            service = InMemorySessionService()
            runner = Runner(agent=root, app_name="yantra", session_service=service)
            for session_id in ("first", "second"):
                await service.create_session(app_name="yantra", user_id="test", session_id=session_id)

            async def send(session_id, text):
                response = None
                async for event in runner.run_async(user_id="test", session_id=session_id,
                    new_message=types.Content(role="user", parts=[types.Part(text=text)])):
                    if event.is_final_response() and event.content:
                        response = json.loads("".join(part.text or "" for part in event.content.parts or []))
                return response

            with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(storage), "YANTRA_MODE": "offline-demo",
                                         "YANTRA_MATERIALS_DIR": str(inputs)}):
                try:
                    first, second = await asyncio.gather(send("first", "DEMO"), send("second", "DEMO"))
                    self.assertEqual(first["status"], "waiting")
                    self.assertEqual(second["status"], "waiting")
                    self.assertNotEqual(first["run_id"], second["run_id"])
                    waiting = Engine.load(storage, second["run_id"]).state
                    invalid = await send("second", "修改 非JSON")
                    self.assertEqual(invalid["status"], "error")
                    self.assertEqual(Engine.load(storage, second["run_id"]).state, waiting)
                    result = await send("first", 'modify {"growth_rate":0.03}')
                    self.assertEqual(result["status"], "partial")
                    changed = Engine.load(storage, first["run_id"]).state
                    self.assertEqual(changed["assumptions"]["version"], 2)
                    self.assertEqual(changed["assumptions"]["parameters"]["growth_rate"], .03)
                    self.assertEqual(Engine.load(storage, second["run_id"]).state, waiting)
                    session = await service.get_session(app_name="yantra", user_id="test", session_id="first")
                    self.assertEqual(json.loads(session.state["research_snapshot"])["run_id"], first["run_id"])
                    self.assertIsNone(root.engine)
                    self.assertTrue(all(worker.engine is None for worker in root.sub_agents))
                finally:
                    await runner.close()

    async def test_missing_materials_returns_error_without_creating_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = Path(temporary) / "runs"
            service = InMemorySessionService()
            await service.create_session(app_name="yantra", user_id="test", session_id="missing")
            runner = Runner(agent=YantraAgent(), app_name="yantra", session_service=service)
            with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(storage), "YANTRA_MODE": "offline-demo",
                                         "YANTRA_MATERIALS_DIR": str(Path(temporary) / "absent")}):
                try:
                    events = [event async for event in runner.run_async(user_id="test", session_id="missing",
                        new_message=types.Content(role="user", parts=[types.Part(text="DEMO")]))]
                    last = next(event for event in reversed(events) if event.is_final_response())
                    response = json.loads(last.content.parts[0].text)
                    self.assertEqual(response["status"], "error")
                    self.assertIn("YANTRA_MATERIALS_DIR", response["message"])
                    self.assertFalse(storage.exists())
                finally:
                    await runner.close()


if __name__ == "__main__":
    unittest.main()
