"""Formal/demo entry and resumable securities selection, entirely offline."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from yantra.adk_app import YantraAgent
from yantra.api import create_app
from yantra.cli import format_text, parser, run_result
from yantra.configuration import ConfigStore
from yantra.engine import create_adk_engine
from yantra.research import RawEngine
from yantra.schemas import AutonomyAuthorization, ModelAssumptions, ResearchSetup, SafetyMarginSelection
from test_adk_native import send
from test_adk_structure import test_materials
import test_cli_contract as cli_test_support


PRICING = {"input_per_million": 1, "output_per_million": 2,
           "source": "controlled-synthetic-price", "as_of": "2026-10-14"}
CANDIDATES = [
    {"candidate_id": "security-1", "legal_name": "测试集团（虚构）", "market": "CN", "exchange": "SSE",
     "security_code": "600001", "trading_currency": "CNY", "share_class": "A",
     "source_ref": "controlled-identity", "quote": "虚构测试集团A股600001"},
    {"candidate_id": "security-2", "legal_name": "测试集团（虚构）", "market": "HK", "exchange": "HKEX",
     "security_code": "00001", "trading_currency": "HKD", "share_class": "H",
     "source_ref": "controlled-identity", "quote": "虚构测试集团H股00001"},
]


class UnusedBackend:
    """Any model decision is a test failure unless explicitly mocked."""
    def config_summary(self):
        return {"backend": "controlled-entry-fixture"}

    def __getattr__(self, name):
        raise AssertionError("unmocked model decision in entry fixture: " + name)


class FormalEntryTests(unittest.TestCase):
    def setUp(self):
        self.cli = cli_test_support.CLIContractTests()
        self.cli.setUp()
        self.addCleanup(self.cli.doCleanups)

    def test_name_only_start_uses_formal_factory_with_empty_inputs(self):
        bootstrap = """from types import SimpleNamespace
from unittest.mock import patch
from yantra.cli import main
state = {'run_id':'run-formal', 'mode':'test', 'raw_materials':True, 'status':'running', 'budget':{}, 'limits':{}}
def create(storage, research, inputs, **kwargs):
    state['research'] = research
    state['pending_user'] = {'inputs':inputs, 'explicit':kwargs['explicit_parameters']}
    return SimpleNamespace(state=state)
async def run(engine):
    return engine.state
with patch('yantra.research.RawEngine.create_raw', side_effect=create), patch('yantra.engine.Engine.create', side_effect=AssertionError('demo fallback')), patch('yantra.adk_app.run_engine', side_effect=run):
    raise SystemExit(main())
"""
        process = self.cli.invoke("start", "--company", "测试集团（虚构）", "--runs-dir", self.cli.runs,
                                  bootstrap=bootstrap)
        data = self.cli.response(process, command="start")["data"]
        self.assertEqual(data["pending_user"]["inputs"], [])
        self.assertEqual(data["research"]["company"], "测试集团（虚构）")
        self.assertEqual(data["research"]["horizon"], "12个月")
        self.assertNotIn("confirmation_mode", data["pending_user"]["explicit"])
        self.assertNotIn("buy_judgment", data)
        self.assertEqual(parser().parse_args(["start", "--company", "synthetic"]).mode, "real-model")

    def test_old_demo_mode_and_formal_demo_parameter_have_migration_errors(self):
        for options, expected in ((("--mode", "offline-demo"), "demo_command_required"),
                                  (("--materials-dir", self.cli.root / "fixture"), "demo_argument")):
            with self.subTest(options=options):
                response = self.cli.response(self.cli.invoke("start", "--company", "synthetic", *options), 2)
                self.assertEqual(response["error"]["code"], expected)
                self.assertIn("demo", response["error"]["message"])
                self.assertFalse(self.cli.runs.exists())

    def test_unconfigured_formal_entry_returns_actionable_model_error(self):
        response = self.cli.response(self.cli.invoke("start", "--company", "synthetic"), 2)
        self.assertEqual(response["error"]["code"], "model_configuration_required")
        self.assertIn("MODEL_API_KEY", response["error"]["message"])
        self.assertNotIn("buy_judgment", response)
        self.assertNotIn("目前无法判断", json.dumps(response, ensure_ascii=False))

    def test_formal_pricing_error_names_required_configuration_and_creates_no_run(self):
        bootstrap = """from types import SimpleNamespace
from unittest.mock import patch
from yantra.cli import main
with patch('yantra.research.RawBackend.from_env', return_value=SimpleNamespace(model_id='controlled-synthetic')):
    raise SystemExit(main())
"""
        response = self.cli.response(self.cli.invoke("start", "--company", "synthetic", "--runs-dir", self.cli.runs,
                                                     bootstrap=bootstrap), 2)
        self.assertEqual(response["error"]["code"], "model_pricing_required")
        self.assertIn("MODEL_PRICE_SOURCE", response["error"]["message"])
        self.assertIn("MODEL_PRICE_AS_OF", response["error"]["message"])
        self.assertFalse(self.cli.runs.exists())

    def test_demo_is_explicit_fictional_and_cannot_create_real_backend(self):
        inputs = test_materials(self.cli.root / "fixture")
        bootstrap = """from unittest.mock import patch
from yantra.cli import main
with patch('yantra.backend.RealModelBackend.from_env', side_effect=AssertionError('real backend in demo')), patch('yantra.research.RawEngine.create_raw', side_effect=AssertionError('formal factory in demo')):
    raise SystemExit(main())
"""
        data = self.cli.response(self.cli.invoke("demo", "--company", "DEMO", "--materials-dir", inputs,
                                                 "--runs-dir", self.cli.runs, bootstrap=bootstrap))["data"]
        self.assertEqual(data["mode"], "offline-demo")
        self.assertEqual(data["status"], "waiting")
        self.assertNotIn("buy_judgment", data)
        manifest = json.loads((inputs / "manifest.json").read_text())
        manifest["fictional"] = False
        (inputs / "manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(self.cli.response(self.cli.invoke("demo", "--materials-dir", inputs), 2)["error"]["code"],
                         "demo_materials_invalid")
        self.assertEqual(self.cli.response(self.cli.invoke("demo", "--materials-dir", inputs / "absent"), 2)["error"]["code"],
                         "demo_materials_missing")

    def test_waiting_identity_exposes_resume_actions_without_input(self):
        name = self.cli.state("waiting", mode="real-model", raw_materials=True,
                              pending_user={"kind": "identity", "question": "请选择上市市场", "candidates": CANDIDATES})
        data = self.cli.response(self.cli.invoke("status", name, "--runs-dir", self.cli.runs))["data"]
        selections = [action for action in data["next_actions"] if action["action"] == "select_identity"]
        self.assertEqual([action["candidate_id"] for action in selections], ["security-1", "security-2"])
        self.assertFalse(any(action["action"] == "confirm" for action in data["next_actions"]))
        for action in selections:
            self.assertEqual(action["argv"][-2:], ["--select-identity", action["candidate_id"]])
        text = self.cli.invoke("status", name, "--runs-dir", self.cli.runs, output="text").stdout
        self.assertIn("等待选择", text)
        self.assertIn("600001", text)
        self.assertIn("00001", text)
        self.assertNotIn("目前无法判断", text)
        error = self.cli.response(self.cli.invoke("resume", name, "--runs-dir", self.cli.runs,
                                                   "--decision", "confirm"), 2)["error"]
        self.assertEqual(error["code"], "identity_choice_required")

    def test_formal_result_starts_with_saved_deterministic_statement(self):
        statement = "暂不值得买：当前价格高于本次安全边际对应的买入阈值。"
        state = {"run_id": "run-answer", "raw_materials": True, "mode": "real-model", "status": "partial",
                 "budget": {"tool_calls": 0, "model_calls": 0, "repair_rounds": 0},
                 "limits": {"total_tools": 20, "max_models": 60},
                 "report": {"投资结论摘要": {"buy_judgment": {"label": "暂不值得买", "statement": statement}}},
                 "report_path": "/controlled/report.md"}
        args = parser().parse_args(["status", "run-answer", "--runs-dir", str(self.cli.runs)])
        result = run_result(state, args, ConfigStore(self.cli.config))
        self.assertEqual(result["buy_judgment"]["statement"], statement)
        self.assertEqual(format_text("status", result).splitlines()[0], statement)
        state["raw_materials"] = False
        state["mode"] = "offline-demo"
        demo = run_result(state, args, ConfigStore(self.cli.config))
        self.assertNotIn("buy_judgment", demo)
        self.assertTrue(format_text("status", demo).startswith("虚构材料离线演示"))


class IdentitySelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {"YANTRA_CONFIG": str(self.root / "config.json"),
                                          "YANTRA_RUNS_DIR": str(self.root / "runs"),
                                          "LITELLM_LOCAL_MODEL_COST_MAP": "True"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        network = patch("socket.socket.connect", side_effect=AssertionError("unmocked external network"))
        network.start()
        self.addCleanup(network.stop)

    def engine(self):
        engine = RawEngine.create_raw(self.root / "runs", {"company": "测试集团（虚构）", "horizon": "12个月",
                                      "cutoff": "2026-10-14T00:00:00+08:00"}, [],
                                      backend=UnusedBackend(), pricing=PRICING,
                                      knowledge_dir=self.root / "knowledge")
        engine.state.update(status="waiting", identity_candidates=copy.deepcopy(CANDIDATES),
                            pending_user={"kind": "identity", "question": "请选择上市市场",
                                          "candidates": copy.deepcopy(CANDIDATES)})
        engine._save()
        engine.advance = AsyncMock(side_effect=lambda: engine.state)
        return engine

    async def test_candidate_id_index_and_code_resume_same_budget_and_deadline(self):
        for selector in ("security-2", "2", "00001"):
            with self.subTest(selector=selector):
                engine = self.engine()
                budget, original_deadline, run_id = copy.deepcopy(engine.state["budget"]), engine.state["deadline"], engine.state["run_id"]
                state = await engine.select_identity(selector)
                self.assertEqual(state["run_id"], run_id)
                self.assertEqual(state["research"]["security_code"], "00001")
                self.assertEqual(state["research"]["market"], "HK")
                self.assertEqual(state["research"]["trading_currency"], "HKD")
                self.assertEqual(state["budget"], budget)
                self.assertEqual(state["deadline"], original_deadline)
                self.assertIsNone(state["pending_user"])
                engine.advance.assert_awaited_once()

    async def test_invalid_or_ambiguous_choice_keeps_saved_pending(self):
        for selector in ("unknown", "0", "600001"):
            with self.subTest(selector=selector):
                engine = self.engine()
                if selector == "600001":
                    engine.state["pending_user"]["candidates"][1]["security_code"] = "600001"
                    engine._save()
                before = json.loads((engine.directory / "state.json").read_text())
                with self.assertRaisesRegex(ValueError, "identity selection not found or ambiguous"):
                    await engine.select_identity(selector)
                self.assertEqual(json.loads((engine.directory / "state.json").read_text()), before)
                engine.advance.assert_not_awaited()

    async def test_cli_resume_selects_saved_security_without_stdin_or_new_run(self):
        fixture = cli_test_support.CLIContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        engine = self.engine()
        budget, original_deadline = copy.deepcopy(engine.state["budget"]), engine.state["deadline"]
        bootstrap = """from unittest.mock import patch
from yantra.cli import main
async def advance(self):
    return self.state
with patch('yantra.research.RawEngine.advance', new=advance), patch('builtins.input', side_effect=AssertionError('JSON cannot read stdin')):
    raise SystemExit(main())
"""
        result = fixture.response(fixture.invoke("resume", engine.state["run_id"], "--runs-dir", engine.directory.parent,
                                                 "--select-identity", "security-2", bootstrap=bootstrap))["data"]
        self.assertEqual(result["run_id"], engine.state["run_id"])
        self.assertEqual(result["research"]["security_code"], "00001")
        self.assertEqual(result["budget"], budget)
        self.assertEqual(result["deadline"], original_deadline)
        self.assertIsNone(result["pending_user"])
        self.assertEqual(len(list(engine.directory.parent.glob("run-*/state.json"))), 1)

    async def test_adk_factory_default_is_formal_and_legacy_demo_is_explicit(self):
        sentinel = object()
        with patch.object(RawEngine, "create_raw", return_value=sentinel) as create:
            self.assertIs(create_adk_engine(company="synthetic"), sentinel)
        self.assertEqual(create.call_args.args[2], [])
        self.assertEqual(create.call_args.args[1]["horizon"], "12个月")
        inputs = test_materials(self.root / "demo-input")
        with patch.dict(os.environ, {"YANTRA_MODE": "offline-demo", "YANTRA_MATERIALS_DIR": str(inputs)}), \
                patch.object(RawEngine, "create_raw", side_effect=AssertionError("formal model in demo")):
            self.assertEqual(create_adk_engine(company="DEMO").state["mode"], "offline-demo")

    async def test_native_api_returns_pending_choice_and_resumes_without_stdin(self):
        engine = self.engine()
        app = create_app(root_agent=YantraAgent(engine=engine), session_service=InMemorySessionService())
        with patch("builtins.input", side_effect=AssertionError("API cannot read stdin")), TestClient(app) as client:
            session = client.post("/apps/yantra/users/owner/sessions", json={}).json()["id"]
            waiting = send(client, session, "继续")
            self.assertEqual(waiting["status"], "waiting")
            self.assertEqual(waiting["pending_user"]["kind"], "identity")
            self.assertIn("/select-identity", waiting["resume_instruction"])
            self.assertEqual(len(waiting["next_actions"]), 2)
            budget, original_deadline = copy.deepcopy(engine.state["budget"]), engine.state["deadline"]
            selected = send(client, session, "/select-identity security-2")
            self.assertEqual(selected["research"]["market"], "HK")
            self.assertIsNone(selected["pending_user"])
            self.assertEqual(engine.state["budget"], budget)
            self.assertEqual(engine.state["deadline"], original_deadline)
            trace = client.get(f"/apps/yantra/users/owner/sessions/{session}/research/trace").json()
            self.assertEqual(trace["controller"]["identity"]["security_code"], "00001")

    async def test_snapshot_unspecified_is_not_explicit_setup_or_false_user_authority(self):
        unspecified = AutonomyAuthorization(status="unspecified", scope=[])
        with self.assertRaises(ValueError):
            ResearchSetup(horizon="12个月", framework="auto", confirmation_mode="autonomous", authorization=unspecified)
        self.assertTrue(ModelAssumptions(authorization_source="default_policy").calculation_permitted)
        for invalid in (-0.01, 1, True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SafetyMarginSelection(ratio=invalid, rationale="controlled rationale")


if __name__ == "__main__":
    unittest.main()
