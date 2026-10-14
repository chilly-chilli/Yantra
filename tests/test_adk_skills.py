"""Actual SkillToolset/Worker path with controlled synthetic provider decisions."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field

from yantra.adk_app import run_worker
from yantra.adk_skills import FrozenSkills, NATIVE_SKILL_TOOLS, alias, compact_receipts
from yantra.configuration import ConfigStore
from yantra.engine import Engine
from yantra.extensions import ExtensionCatalog, ExtensionRuntime
from yantra.policies import OfflineDemoBackend
from yantra.research import RawBackend, RawEngine
from yantra.schemas import MainAction
from test_v2 import PRICE, TEXT, V2TestBackend


class SkillProvider(BaseLlm):
    model: str = "synthetic-skills"
    payloads: list[dict] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream=False):
        payload = json.loads(llm_request.contents[-1].parts[0].text)
        self.payloads.append(payload)
        self.instructions.append(str(llm_request.config.system_instruction))
        task, local = payload["task"], payload["local"]
        name = local["skill_catalogue"][0]["skill_name"]
        demo = OfflineDemoBackend()
        count = local["actual_calls"]
        action = (demo._tool("list_skills", {}) if count == 0 else
                  demo._tool("load_skill", {"skill_name": name}) if count == 1 else
                  demo._tool("load_skill_resource", {"skill_name": name, "file_path": "references/reference.txt"}) if count == 2 else
                  demo._result(task, local, data={"checked": True, "resource": local["tool_outputs"][-1]["response"]["data"]["result"]["content"]}))
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(action))]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=50, total_token_count=150))


def setup_skills(root):
    store = ConfigStore(root / "config.json")
    source = root / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text('---\nname: ignored\ndescription: Synthetic cash flow guide\nmetadata:\n  adk_inject_state: true\n  adk_additional_tools: [forbidden_exec]\n---\n# Guide\nFROZEN_PRIVATE_RULE {run_id}\n')
    (source / "reference.txt").write_text("frozen reference text")
    (source / "scripts").mkdir()
    (source / "scripts/run.py").write_text('raise RuntimeError("must never execute")\n')
    return store, source


class SkillIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_worker_lazy_catalogue_loading_hashes_and_single_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, source = setup_skills(root)
            catalog = ExtensionCatalog(store)
            await catalog.add("skills", "cash_flow", {"path": str(source), "roles": ["finance"], "loading": "native"})
            material = root / "input.txt"
            material.write_text(TEXT)
            backend = RawBackend("test", "https://unused.invalid/v1", "synthetic-only")
            provider = SkillProvider()
            backend._model = provider
            engine = RawEngine.create_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "600000"},
                [material], backend=backend, pricing=PRICE, settings=store)
            task = engine._make_task(MainAction(action="delegate_task", target="finance", purpose="collect"))
            result = await run_worker(engine, task.model_dump())
            self.assertTrue(result["data"]["checked"])
            self.assertEqual(result["data"]["resource"], "frozen reference text")
            self.assertEqual(engine.state["budget"]["tool_calls"], 3)
            self.assertEqual(engine.state["budget"]["model_calls"], 4)
            self.assertEqual(len(engine.state["model_ledger"]), 4)
            self.assertNotIn("FROZEN_PRIVATE_RULE", provider.instructions[0])
            self.assertIn("FROZEN_PRIVATE_RULE {run_id}", provider.instructions[2])
            loaded = provider.payloads[2]["local"]["tool_outputs"][-1]["response"]["data"]["result"]
            self.assertNotIn("instructions", loaded)
            self.assertTrue(loaded["instructions_ref"])
            self.assertNotIn("load_skill", engine.permissions["thesis"])
            self.assertNotIn("run_skill_script", engine.permissions["finance"])
            self.assertNotIn("forbidden_exec", engine.permissions["finance"])
            restored = Engine.load(root / "runs", engine.state["run_id"], backend=backend)
            self.assertEqual(restored.state["capabilities"], engine.state["capabilities"])
            self.assertIn("load_skill", restored.permissions["finance"])
            events = [json.loads(line) for line in (engine.directory / "adk-trace.jsonl").read_text().splitlines()]
            self.assertEqual(len([event for event in events if "usage" in event]), 4)
            self.assertNotIn("FROZEN_PRIVATE_RULE", json.dumps(events))

    async def test_role_hash_path_and_no_executor_even_after_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, source = setup_skills(root)
            catalog = ExtensionCatalog(store)
            await catalog.add("skills", "cash_flow", {"path": str(source), "roles": ["finance"], "loading": "native"})
            snapshot = catalog.snapshot(root / "run")
            runtime = ExtensionRuntime(snapshot)
            native = FrozenSkills(runtime, "finance")
            self.assertEqual({tool.name for tool in await native.toolset.get_tools()}, set(NATIVE_SKILL_TOOLS))
            self.assertEqual(FrozenSkills(runtime, "thesis").catalogue(), [])
            restricted = FrozenSkills(runtime, "finance", allowed_tools=[])
            self.assertEqual(restricted.catalogue(), [])
            self.assertEqual(await restricted.toolset.get_tools(), [])
            with self.assertRaises(ValueError):
                await restricted.call("load_skill", {"skill_name": alias("cash_flow")}, None)
            self.assertNotEqual(alias("cash_flow"), alias("cash-flow"))
            name = alias("cash_flow")
            with self.assertRaises(ValueError):
                await native.call("load_skill_resource", {"skill_name": name, "file_path": "references/../../outside"}, None)
            with self.assertRaises(ValueError):
                await native.call("run_skill_script", {}, None)
            frozen = Path(snapshot["skills"][0]["resource_path"])
            (frozen / "reference.txt").write_text("changed")
            with self.assertRaisesRegex(ValueError, "改变"):
                await native.call("load_skill_resource", {"skill_name": name, "file_path": "references/reference.txt"}, None)
            (frozen / "SKILL.md").write_text("changed body")
            with self.assertRaisesRegex(ValueError, "改变"):
                await native.call("load_skill", {"skill_name": name}, None)

    async def test_legacy_snapshot_does_not_gain_native_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, source = setup_skills(root)
            await ExtensionCatalog(store).add("skills", "cash_flow", {"path": str(source), "roles": ["finance"], "loading": "native"})
            material = root / "input.txt"
            material.write_text(TEXT)
            engine = RawEngine.create_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "600000"},
                [material], backend=V2TestBackend(), pricing=PRICE, settings=store)
            engine.state["capabilities"]["extensions"].pop("skill_loading")
            engine._save()
            restored = Engine.load(root / "runs", engine.state["run_id"], backend=V2TestBackend())
            self.assertNotIn("load_skill", restored.permissions["finance"])
            self.assertIn("read_skill_resource", restored.permissions["finance"])

    async def test_child_without_native_grant_receives_no_skill_body_or_catalogue(self):
        class RestrictedProvider(BaseLlm):
            model: str = "synthetic-restricted"
            captured: str = ""
            async def generate_content_async(self, request, stream=False):
                self.captured = str(request.config.system_instruction) + request.contents[-1].parts[0].text
                payload = json.loads(request.contents[-1].parts[0].text)
                action = OfflineDemoBackend()._result(payload["task"], payload["local"], data={"checked": True})
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(action))]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, source = setup_skills(root)
            await ExtensionCatalog(store).add("skills", "cash_flow", {"path": str(source), "roles": ["finance"], "loading": "native"})
            material = root / "input.txt"
            material.write_text(TEXT)
            backend = RawBackend("test", "https://unused.invalid/v1", "synthetic-only")
            provider = RestrictedProvider()
            backend._model = provider
            engine = RawEngine.create_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "600000"},
                [material], backend=backend, pricing=PRICE, settings=store)
            task = engine._make_task(MainAction(action="delegate_task", target="finance", purpose="collect"))
            task.depth = 3
            task.authorized_tools = []
            result = await run_worker(engine, task.model_dump())
            self.assertTrue(result["data"]["checked"])
            self.assertNotIn("FROZEN_PRIVATE_RULE", provider.captured)
            self.assertNotIn("Synthetic cash flow guide", provider.captured)
            self.assertEqual(engine.state["budget"]["tool_calls"], 0)

    def test_cli_explicit_native_pilot_and_default_eager_are_saved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store, source = setup_skills(root)
            prefix = [sys.executable, "-m", "yantra", "--config", str(store.path), "--output", "json", "extensions", "add", "skills"]
            for name, extra, expected in (("default", [], "eager"), ("pilot", ["--loading", "native"], "native")):
                response = subprocess.run(prefix + [name, "--path", str(source), "--roles", "finance", *extra], capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(response.stdout)["data"]["name"], name)
                self.assertEqual(store.load()["skills"][name]["loading"], expected)

    def test_exact_duplicate_receipts_keep_first_text_and_fixed_reference(self):
        receipt = {"tool": "read_materials", "arguments": {"anchor_refs": ["source:1"]}, "response": {
            "ok": True, "data": {"text": "Original bytes " * 2000, "value": 123.5}, "evidence_refs": ["source:1"], "calculation_refs": []}}
        local = {"tool_outputs": [receipt, receipt], "actual_calls": 2}
        compact = compact_receipts(local)
        self.assertEqual(compact["tool_outputs"][0], receipt)
        ref = compact["tool_outputs"][1]["response"]["data"]["receipt_ref"]
        self.assertEqual(ref["index"], 0)
        self.assertEqual(compact["tool_outputs"][ref["index"]]["response"]["data"]["value"], 123.5)
        self.assertGreater(len(json.dumps(local)), len(json.dumps(compact)))
        self.assertEqual(local["tool_outputs"][1], receipt)
