"""Actual processes/protocols with fictional materials; no paid service calls."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from yantra.cli_contract import CLIError
from yantra.engine import Engine
from yantra.host_contract import SubmitInput
from yantra.host_service import HostService, state_revision
from yantra.host_worker import execute_job
from test_adk_structure import test_materials

ROOT = Path(__file__).resolve().parents[1]


class HostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.materials = test_materials(self.root / "fictional")
        self.service = HostService(self.root, config=self.root / ".yantra/config.json", runs_dir=self.root / "runs", knowledge_dir=self.root / ".yantra/knowledge")
        self.request = {"company": "DEMO", "mode": "offline-demo", "demo_materials_dir": str(self.materials), "idempotency_key": "synthetic-first"}
        guard = self.root / "guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text('''import ipaddress, os, socket
original = socket.socket.connect
def connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        try: local = ipaddress.ip_address(address[0]).is_loopback
        except ValueError: local = address[0] == "localhost"
        if not local:
            with open(os.environ["YANTRA_TEST_NETWORK_LOG"], "a") as out: out.write("rejected\\n")
            raise RuntimeError("external network forbidden in offline fixture")
    return original(self, address)
socket.socket.connect = connect
''')
        self.environment = patch.dict(os.environ, {"PYTHONPATH": str(guard) + os.pathsep + str(ROOT), "YANTRA_RUNS_DIR": str(self.root / "runs"),
            "YANTRA_CONFIG": str(self.root / ".yantra/config.json"),
            "YANTRA_KNOWLEDGE_DIR": str(self.root / ".yantra/knowledge"),
            "YANTRA_TEST_NETWORK_LOG": str(self.root / "network-attempts"), "LITELLM_LOCAL_MODEL_COST_MAP": "True"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.assert_no_network)

    def assert_no_network(self):
        self.assertFalse((self.root / "network-attempts").exists(), "offline subprocess attempted external network")

    def wait(self, run_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = self.service.call("status", {"run_id": run_id})
            if result["execution"]["state"] not in {"starting", "active", "accepted"}:
                return result
            time.sleep(.03)
        self.fail("local executor did not settle")

    def submit(self, **changes):
        receipt = self.service.call("submit", self.request | changes)
        result = self.wait(receipt["run_id"])
        self.assertEqual(result["execution"]["state"], "idle", result["execution"])
        return result

    def respond(self, result, decision="confirm", **changes):
        request = {"run_id": result["run_id"], "operation_id": "operation-" + decision,
            "expected_revision": result["revision"], "pending_id": result["pending_id"], "decision": decision, **changes}
        self.assertEqual(self.service.call("respond", request)["receipt"], "accepted")
        updated = self.wait(result["run_id"])
        self.assertEqual(updated["operations"][0]["receipt"], "applied", updated["operations"])
        return updated, request

    def test_concurrent_idempotency_lost_response_conflict_and_context(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.service.call("submit", self.request), range(6)))
        self.assertEqual(len({item["run_id"] for item in results}), 1)
        self.assertEqual(sum(not item["reused"] for item in results), 1)
        run_id = results[0]["run_id"]
        self.wait(run_id)
        self.assertEqual(self.service.call("submit", self.request)["run_id"], run_id)
        with self.assertRaises(CLIError) as conflict:
            self.service.call("submit", self.request | {"company": "OTHER"})
        self.assertEqual(conflict.exception.error["code"], "idempotency_conflict")
        with self.assertRaises(CLIError):
            HostService(self.root, config=self.root / "other.json").call("submit", self.request)
        self.assertEqual(len(list(self.service.runs.glob("run-*/state.json"))), 1)

    def test_actual_cli_exit_does_not_terminate_executor_and_cross_directory(self):
        marker, release = self.root / "executor-ready", self.root / "executor-release"
        self.addCleanup(release.touch)
        with (self.root / "guard/sitecustomize.py").open("a") as handle:
            handle.write(f'''
from yantra import adk_app
original_run = adk_app.run_engine
async def held_run(engine):
    __import__("pathlib").Path({str(marker)!r}).touch()
    for _ in range(100):
        if __import__("pathlib").Path({str(release)!r}).exists(): break
        await __import__("asyncio").sleep(.05)
    return await original_run(engine)
adk_app.run_engine = held_run
''')
        process = subprocess.run([sys.executable, "-m", "yantra", "--output", "json", "host", "submit", "--workspace", str(self.root),
            "--input", json.dumps(self.request)], cwd="/", text=True, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        receipt = json.loads(process.stdout)["data"]
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(marker.exists())
        os.kill(self.service.row(receipt["run_id"])["pid"], 0)
        self.assertEqual(self.service.call("status", {"run_id": receipt["run_id"]})["execution"]["state"], "active")
        release.touch()
        result = self.wait(receipt["run_id"])
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(result["execution"]["state"], "idle")
        self.assertEqual(len(self.service.call("list", {})["runs"]), 1)

    def test_status_exposes_observed_progress_and_action_constraints(self):
        initial = self.submit()
        self.assertEqual(initial["progress"]["phase"], "waiting_confirmation")
        self.assertGreater(initial["progress"]["result_count"], 0)
        self.assertEqual(initial["progress"]["active_tasks"], [])
        response = next(action for action in initial["next_actions"] if action["action"] == "respond")
        self.assertEqual(response["expected_revision"], initial["revision"])
        self.assertEqual(response["pending_id"], initial["pending_id"])
        self.assertIn("confirm", response["decisions"])
        self.assertIn("operation_id", response["required_input"])
        from yantra.host_service import progress
        observed = self.service.state(initial["run_id"])
        observed.update(status="running", in_flight=None)
        observed["tasks"].append({"task_id": "task-unfinished", "target": "finance", "purpose": "collect", "depth": 2})
        self.assertEqual(progress(observed, {"state": "active"})["active_tasks"], [])
        observed["in_flight_tool"] = {"task_id": "task-unfinished"}
        self.assertEqual(progress(observed, {"state": "active"})["phase"], "finance")
        engine = Engine.load(self.service.runs, initial["run_id"])
        engine.state["assumptions"]["parameters"] = {}
        engine._save()
        no_parameters = self.service.call("status", {"run_id": initial["run_id"]})
        response = next(action for action in no_parameters["next_actions"] if action["action"] == "respond")
        self.assertEqual(response["decisions"], ["reject"])

    def test_qualitative_partial_report_matches_fixed_output_qualification(self):
        from test_formal_entry import UnusedBackend, PRICING
        from yantra.research import RawEngine
        from yantra.host_outputs import index
        engine = RawEngine.create_raw(self.service.runs, {"company": "SYNTHETIC", "horizon": "12个月", "cutoff": "2025-12-31"}, [],
            backend=UnusedBackend(), pricing=PRICING)
        engine.state["host_contract_version"] = 1
        engine.state["artifacts"]["finance"] = {"task_id": "task-qualitative", "data": {"financials": [], "evidence": [{"kind": "fact", "statement": "SYNTHETIC qualitative disclosure", "source_ref": "synthetic:L1"}]},
            "evidence_refs": [], "calculation_refs": [], "unresolved": ["SYNTHETIC financial data missing"], "conflicts": [], "assumptions_version": 1}
        engine._save()
        with engine._lock():
            engine._finish()
        self.assertEqual(engine.state["status"], "partial")
        self.assertEqual(index(engine.directory)[-1]["qualification"]["research_status"], "partial")
        self.assertIn("**partial**", Path(engine.state["report_path"]).read_text())

    def test_cancel_before_first_state_blocks_preparation_fee_reservation(self):
        from test_formal_entry import UnusedBackend, PRICING
        from yantra.knowledge.runtime import ResearchKnowledge
        from yantra.research import RawEngine
        formal = {"company": "SYNTHETIC", "idempotency_key": "preparation-cancel"}
        with patch.object(self.service, "launch"):
            receipt = self.service.call("submit", formal)
        with self.service.database() as db:
            db.execute("UPDATE submissions SET job='initial',execution='active' WHERE run_id=?", (receipt["run_id"],))
            db.commit()
        original_initialize = ResearchKnowledge.initialize
        def cancel_before_initialization(knowledge):
            if knowledge.engine.state["run_id"] == receipt["run_id"]:
                self.assertEqual(self.service.call("cancel", {"run_id": receipt["run_id"]})["cancellation"], "request_accepted")
                knowledge.engine.run_budget.reserve_fixed(.25, "synthetic-preparation", {"source": "controlled-fixture"})
            return original_initialize(knowledge)
        original_create = RawEngine.create_raw
        def create_with_fixture(*args, **kwargs):
            return original_create(*args, **(kwargs | {"backend": UnusedBackend(), "pricing": PRICING}))
        with self.service.gate(receipt["run_id"]), patch.object(ResearchKnowledge, "initialize", cancel_before_initialization), patch.object(RawEngine, "create_raw", create_with_fixture):
            asyncio.run(execute_job(self.service, self.service.row(receipt["run_id"])))
        result = self.service.call("status", {"run_id": receipt["run_id"]})
        self.assertEqual(result["status"], "cancelled")
        path = self.service.runs / receipt["run_id"] / "state.json"
        if path.exists():
            self.assertEqual(json.loads(path.read_text())["model_ledger"], [])

    def test_versions_duplicate_modification_receipts_hashes_and_bounded_read(self):
        initial = self.submit()
        delivered, confirm = self.respond(initial)
        version = delivered["outputs"][-1]["version"]
        old = self.service.call("read", {"run_id": initial["run_id"], "artifact": "report.md", "version": version, "length": 65536})
        self.assertTrue(old["complete"])
        self.assertEqual(hashlib.sha256(base64.b64decode(old["content"])).hexdigest(), old["sha256"])
        self.assertEqual(self.service.call("respond", confirm)["receipt"], "applied")
        modified, request = self.respond(delivered, "modify", parameters={"growth_rate": .04})
        self.assertEqual(modified["assumptions"]["version"], delivered["assumptions"]["version"] + 1)
        self.assertEqual(modified["operations"][0]["parameter_changes"]["growth_rate"], {"before": .05, "after": .04})
        self.assertEqual(self.service.call("respond", request)["receipt"], "applied")
        self.assertEqual(self.service.call("read", {"run_id": initial["run_id"], "version": version, "length": 65536})["sha256"], old["sha256"])
        piece = self.service.call("read", {"run_id": initial["run_id"], "version": version, "offset": 1, "length": 17})
        self.assertFalse(piece["complete"])
        self.assertEqual(piece["next_offset"], 18)
        self.assertEqual(base64.b64decode(piece["content"]), base64.b64decode(old["content"])[1:18])
        evidence = next(key for key in modified["outputs"][-1]["files"] if key.startswith("evidence/"))
        self.assertTrue(self.service.call("read", {"run_id": initial["run_id"], "artifact": evidence})["locator"])

    def test_stale_pending_revision_and_operation_conflicts(self):
        initial = self.submit()
        common = {"run_id": initial["run_id"], "operation_id": "one", "decision": "confirm", "expected_revision": initial["revision"]}
        with self.assertRaises(CLIError) as pending:
            self.service.call("respond", common)
        self.assertEqual(pending.exception.error["code"], "pending_conflict")
        updated, request = self.respond(initial)
        with self.assertRaises(CLIError) as stale:
            self.service.call("respond", common | {"pending_id": initial["pending_id"]})
        self.assertEqual(stale.exception.error["code"], "revision_conflict")
        with self.assertRaises(CLIError) as operation:
            self.service.call("respond", request | {"decision": "reject"})
        self.assertEqual(operation.exception.error["code"], "operation_conflict")

    def test_cancel_waiting_settles_and_cannot_resume(self):
        initial = self.submit()
        receipt = self.service.call("cancel", {"run_id": initial["run_id"]})
        self.assertEqual(receipt["cancellation"], "request_accepted")
        result = self.wait(initial["run_id"])
        self.assertEqual(result["status"], "cancelled")
        with self.assertRaises(CLIError):
            self.service.call("resume", {"run_id": initial["run_id"], "operation_id": "resume", "expected_revision": result["revision"]})

    def test_cancel_persisted_intent_without_executor_does_not_wait_forever(self):
        with patch.object(self.service, "launch"):
            receipt = self.service.call("submit", self.request)
        result = self.service.call("cancel", {"run_id": receipt["run_id"]})
        self.assertEqual(result["cancellation"], "cancelled")
        self.assertEqual(self.service.call("status", {"run_id": receipt["run_id"]})["status"], "cancelled")
        self.assertFalse((self.service.runs / receipt["run_id"] / "state.json").exists())

    def test_active_cancellation_settles_when_engine_reaches_waiting(self):
        with patch.object(self.service, "launch"):
            receipt = self.service.call("submit", self.request)
        with self.service.database() as db:
            db.execute("UPDATE submissions SET job='initial',execution='active' WHERE run_id=?", (receipt["run_id"],))
            db.commit()
        async def cancel_during_run(engine):
            self.assertEqual(self.service.call("cancel", {"run_id": receipt["run_id"]})["cancellation"], "request_accepted")
            return await engine.advance()
        with self.service.gate(receipt["run_id"]), patch("yantra.adk_app.run_engine", cancel_during_run):
            asyncio.run(execute_job(self.service, self.service.row(receipt["run_id"])))
        self.assertEqual(self.service.call("status", {"run_id": receipt["run_id"]})["status"], "cancelled")

    def test_missing_tampered_and_traversal_artifacts_and_scoped_adk(self):
        result, _ = self.respond(self.submit())
        request = {"run_id": result["run_id"], "version": result["outputs"][-1]["version"]}
        with self.assertRaises(CLIError):
            self.service.call("read", request | {"artifact": "../../.env"})
        entry = result["outputs"][-1]["files"]["report.md"]
        target = self.service.runs / result["run_id"] / entry["source"]
        target.write_bytes(b"tampered")
        with self.assertRaises(CLIError) as tampered:
            self.service.call("read", request)
        self.assertEqual(tampered.exception.error["code"], "artifact_hash_mismatch")
        target.unlink()
        with self.assertRaises(CLIError) as missing:
            self.service.call("read", request)
        self.assertEqual(missing.exception.error["code"], "artifact_unavailable")
        outside = self.root / "elsewhere"
        outside.mkdir()
        (self.service.runs / "run-escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(CLIError):
            self.service.call("status", {"run_id": "run-escape"})
        legacy = Engine.create(self.service.runs, {"company": "DEMO", "horizon": "12个月", "cutoff": "2025-12-31"}, materials_dir=self.materials)
        (legacy.directory / "adk-scopes.json").write_text('[{"app_name":"yantra","user_id":"owner","session_id":"private"}]')
        with self.assertRaises(CLIError) as denied:
            self.service.call("status", {"run_id": legacy.state["run_id"]})
        self.assertEqual(denied.exception.error["code"], "session_scope_required")

    def test_executor_launch_failure_is_not_running_and_can_explicitly_resume(self):
        with patch("yantra.host_service.subprocess.Popen", side_effect=PermissionError("PRIVATE_SECRET")):
            receipt = self.service.call("submit", self.request)
        status = self.service.call("status", {"run_id": receipt["run_id"]})
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["research"]["company"], "DEMO")
        self.assertIsNone(status["budget"])
        self.assertEqual(self.service.call("list", {})["runs"][0]["research"]["company"], "DEMO")
        self.assertEqual(status["execution"]["error"]["code"], "execution_permission_denied")
        self.assertNotIn("PRIVATE_SECRET", json.dumps(status))
        self.service.call("resume", {"run_id": receipt["run_id"], "operation_id": "retry", "expected_revision": status["revision"]})
        result = self.wait(receipt["run_id"])
        self.assertEqual(result["status"], "waiting", result)

    def test_executor_death_before_start_acknowledgement_can_resume(self):
        with patch.object(self.service, "launch"):
            receipt = self.service.call("submit", self.request)
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=10)
        with self.service.database() as db:
            db.execute("UPDATE submissions SET execution='starting',pid=? WHERE run_id=?", (child.pid, receipt["run_id"]))
            db.commit()
        result = self.service.call("status", {"run_id": receipt["run_id"]})
        self.assertEqual(result["status"], "interrupted")
        self.service.call("resume", {"run_id": receipt["run_id"], "operation_id": "restart-before-ack", "expected_revision": result["revision"]})
        self.assertEqual(self.wait(receipt["run_id"])["status"], "waiting")

    def test_interrupted_executor_and_unknown_operation_do_not_reset_budget(self):
        initial = self.submit()
        engine = Engine.load(self.service.runs, initial["run_id"])
        engine.state["in_flight_tool"] = {"name": "unknown-paid-service"}
        engine._save()
        with self.service.database() as db:
            db.execute("UPDATE submissions SET execution='active' WHERE run_id=?", (initial["run_id"],))
            db.commit()
        result = self.service.call("status", {"run_id": initial["run_id"]})
        self.assertEqual(result["execution"]["state"], "interrupted")
        before = engine.state["budget"].copy()
        with self.assertRaises(CLIError) as unknown:
            self.service.call("resume", {"run_id": initial["run_id"], "operation_id": "retry", "expected_revision": result["revision"]})
        self.assertEqual(unknown.exception.error["code"], "external_outcome_unknown")
        self.assertEqual(self.service.state(initial["run_id"])["budget"], before)

    def test_real_executor_kill_is_interrupted_and_resume_keeps_run(self):
        guard = self.root / "guard/sitecustomize.py"
        marker = self.root / "paused"
        once = self.root / "pause-once"
        once.touch()
        with guard.open("a") as handle:
            handle.write(f'''
if __import__("pathlib").Path({str(once)!r}).exists():
    from yantra import adk_app
    original_run = adk_app.run_engine
    async def paused_run(engine):
        __import__("pathlib").Path({str(once)!r}).unlink(missing_ok=True)
        __import__("pathlib").Path({str(marker)!r}).touch()
        await __import__("asyncio").sleep(30)
        return await original_run(engine)
    adk_app.run_engine = paused_run
''')
        receipt = self.service.call("submit", self.request)
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertTrue(marker.exists())
        row = self.service.row(receipt["run_id"])
        before = self.service.state(receipt["run_id"])
        os.kill(row["pid"], signal.SIGKILL)
        result = self.wait(receipt["run_id"])
        self.assertEqual(result["status"], "interrupted")
        self.service.call("resume", {"run_id": receipt["run_id"], "operation_id": "after-kill", "expected_revision": result["revision"]})
        result = self.wait(receipt["run_id"])
        self.assertEqual(result["status"], "waiting")
        after = self.service.state(receipt["run_id"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["limits"], before["limits"])

    def test_formal_expired_resume_retains_deadline_and_ledger(self):
        from test_formal_entry import UnusedBackend, PRICING
        from yantra.research import RawEngine
        with patch.object(self.service, "launch"):
            receipt = self.service.call("submit", self.request)
        # A controlled raw controller replaces the initial demo only in this fixture.
        engine = RawEngine.create_raw(self.service.runs, {"company": "SYNTHETIC", "horizon": "12个月", "cutoff": "2025-12-31"}, [],
            backend=UnusedBackend(), pricing=PRICING, run_id=receipt["run_id"])
        engine.state.update(host_contract_version=1, deadline="2000-01-01T00:00:00+00:00", status="partial")
        engine.state["budget"]["model_cost_cny"] = .25
        engine.state["model_ledger"] = [{"id": "synthetic-reserved", "reserved_cny": .25, "status": "reserved"}]
        engine._save()
        self.service.finish_job(receipt["run_id"], "idle")
        result = self.service.call("status", {"run_id": receipt["run_id"]})
        with patch.object(self.service, "launch"):
            self.service.call("resume", {"run_id": receipt["run_id"], "operation_id": "expired", "expected_revision": result["revision"], "continue_research": True})
        # Execute the accepted job through the same worker, with no model calls permitted.
        with self.service.database() as db:
            db.execute("UPDATE submissions SET job='op:expired' WHERE run_id=?", (receipt["run_id"],))
            db.commit()
        asyncio.run(execute_job(self.service, self.service.row(receipt["run_id"])))
        after = self.service.state(receipt["run_id"])
        self.assertEqual(after["deadline"], engine.state["deadline"])
        self.assertEqual(after["budget"], engine.state["budget"])
        self.assertEqual(after["model_ledger"], engine.state["model_ledger"])
        self.assertEqual(self.service.call("status", {"run_id": receipt["run_id"]})["operations"][0]["receipt"], "rejected")

    def test_formal_creation_uses_original_submission_time_and_business_factory(self):
        from types import SimpleNamespace
        from yantra.host_worker import create_engine
        request = SubmitInput.model_validate({"company": "SYNTHETIC", "idempotency_key": "formal"})
        row = {"run_id": "run-controlled-formal", "payload": request.model_dump_json(), "created_at": 1000.0}
        with patch("yantra.research.RawEngine.create_raw", return_value=SimpleNamespace()) as factory:
            create_engine(self.service, row)
        self.assertEqual(factory.call_args.kwargs["started_at"], 1000.0)
        self.assertEqual(factory.call_args.kwargs["run_id"], row["run_id"])
        self.assertEqual(factory.call_args.args[2], [])
        self.assertIsNone(factory.call_args.kwargs["authorization_override"])
        from test_formal_entry import UnusedBackend, PRICING
        from yantra.research import RawEngine
        from yantra.budget import BudgetExceeded
        with self.assertRaises(BudgetExceeded):
            RawEngine.create_raw(self.service.runs, {"company": "SYNTHETIC", "horizon": "12个月", "cutoff": "2025-12-31"}, [],
                backend=UnusedBackend(), pricing=PRICING, started_at=1000.0)

    def test_host_identity_response_uses_current_pending_and_original_budget(self):
        from test_formal_entry import UnusedBackend, PRICING, CANDIDATES
        from yantra.research import RawEngine
        with patch.object(self.service, "launch"):
            receipt = self.service.call("submit", {"company": "SYNTHETIC", "idempotency_key": "identity-response"})
        raw = RawEngine.create_raw(self.service.runs, {"company": "SYNTHETIC", "horizon": "12个月", "cutoff": "2025-12-31"}, [],
            backend=UnusedBackend(), pricing=PRICING, run_id=receipt["run_id"], knowledge_dir=self.service.knowledge)
        raw.state.update(host_contract_version=1, status="waiting", pending_user={"kind": "identity", "question": "SYNTHETIC choice", "candidates": CANDIDATES})
        raw._save()
        self.service.finish_job(receipt["run_id"], "idle")
        initial = self.service.call("status", {"run_id": receipt["run_id"]})
        self.assertEqual(initial["progress"]["phase"], "waiting_identity")
        async def finish_without_model(engine):
            with engine._lock():
                engine._finish()
            return engine.state
        with patch.object(self.service, "launch"):
            self.service.call("respond", {"run_id": receipt["run_id"], "operation_id": "select", "decision": "select_identity",
                "selector": "security-2", "expected_revision": initial["revision"], "pending_id": initial["pending_id"]})
        with self.service.database() as db:
            db.execute("UPDATE submissions SET job='op:select' WHERE run_id=?", (receipt["run_id"],))
            db.commit()
        with patch.object(RawEngine, "advance", finish_without_model):
            asyncio.run(execute_job(self.service, self.service.row(receipt["run_id"])))
        result = self.service.call("status", {"run_id": receipt["run_id"]})
        self.assertEqual(result["operations"][0]["receipt"], "applied")
        self.assertEqual(result["research"]["security_code"], "00001")
        self.assertEqual(result["deadline"], raw.state["deadline"])
        self.assertEqual(result["budget"]["model_cost_cny"], 0)
        self.assertEqual(result["budget"]["model_calls"], 0)
        self.assertIsNone(result["pending_user"])

    def test_engine_lock_rechecks_revision_after_acceptance(self):
        initial = self.submit()
        engine = Engine.load(self.service.runs, initial["run_id"])
        engine.expected_host_revision = initial["revision"]
        engine.state["assumptions"]["version"] += 1
        engine._save()
        with self.assertRaises(CLIError) as conflict:
            asyncio.run(engine.respond("confirm"))
        self.assertEqual(conflict.exception.error["code"], "revision_conflict")

    def test_failed_operation_with_saved_changes_is_not_reported_rejected(self):
        initial = self.submit()
        with patch.object(self.service, "launch"):
            self.service.call("respond", {"run_id": initial["run_id"], "operation_id": "partial-write", "decision": "confirm",
                "expected_revision": initial["revision"], "pending_id": initial["pending_id"]})
        with self.service.database() as db:
            db.execute("UPDATE submissions SET job='op:partial-write' WHERE run_id=?", (initial["run_id"],))
            db.commit()
        async def write_then_fail(engine, *args, **kwargs):
            engine.state["assumptions"]["confirmed"] = True
            engine._save()
            raise OSError("PRIVATE_SECRET")
        with patch.object(Engine, "respond", write_then_fail):
            asyncio.run(execute_job(self.service, self.service.row(initial["run_id"])))
        result = self.service.call("status", {"run_id": initial["run_id"]})
        self.assertEqual(result["operations"][0]["receipt"], "accepted")
        self.assertEqual(result["operations"][0]["application"], "outcome_unknown")
        self.assertNotIn("PRIVATE_SECRET", json.dumps(result))
        with self.assertRaises(CLIError) as unknown:
            self.service.call("resume", {"run_id": initial["run_id"], "operation_id": "do-not-replay", "expected_revision": result["revision"]})
        self.assertEqual(unknown.exception.error["code"], "operation_outcome_unknown")

    def test_doctor_sanitized_local_only_non_tty_and_paths(self):
        configured = self.root / "selected/config.json"
        with patch.dict(os.environ, {"YANTRA_CONFIG": str(configured)}):
            self.assertEqual(HostService(self.root).settings.path, configured)
            self.assertEqual(HostService(self.root, config=self.service.settings.path).settings.path, self.service.settings.path)
        with patch.dict(os.environ, {"YANTRA_KNOWLEDGE_DIR": str(self.root / "selected/knowledge")}):
            self.assertEqual(HostService(self.root).knowledge, self.root / "selected/knowledge")
            self.assertEqual(HostService(self.root, knowledge_dir=self.service.knowledge).knowledge, self.service.knowledge)
        from yantra.cli_contract import exception_error
        missing = exception_error(ModuleNotFoundError("PRIVATE_SECRET", name="mcp"))
        self.assertEqual(missing.error["code"], "dependency_missing")
        self.assertNotIn("PRIVATE_SECRET", json.dumps(missing.error))
        process = subprocess.run([sys.executable, "-S", "-m", "yantra", "--output", "json", "host", "doctor", "--workspace", str(self.root)],
            cwd="/", text=True, capture_output=True, timeout=10)
        self.assertEqual(json.loads(process.stdout)["error"]["code"], "dependency_missing")
        self.assertEqual(process.returncode, 1)
        from yantra.cli import format_text
        self.assertIsNone(format_text("host submit", {"run_id": "run-synthetic", "receipt": "persisted"}))
        result = self.service.call("doctor", {"command_tool": "denied", "mcp_available": True, "materials": ["missing"]})
        self.assertEqual(result["route"], "skill_mcp")
        self.assertEqual(result["network"], "not_probed")
        self.assertIn("material_path_invisible", {item["code"] for item in result["checks"]})
        with self.assertRaises(CLIError):
            self.service.call("submit", self.request | {"demo_materials_dir": "missing"})
        self.assertEqual(self.service.call("list", {})["runs"], [])

    def test_real_stdio_reconnection_shared_cli_mcp_and_business_errors(self):
        async def run():
            parameters = StdioServerParameters(command=sys.executable, args=["-m", "yantra", "host", "mcp", "--workspace", str(self.root)])
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 9)
                    import jsonschema
                    for tool in tools.tools:
                        jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                    receipt = await session.call_tool("yantra_submit", {"request": self.request})
                    self.assertFalse(receipt.isError)
                    run_id = receipt.structuredContent["data"]["run_id"]
            initial = await asyncio.to_thread(self.wait, run_id)
            # CLI modifies the same controller state while MCP is disconnected.
            args = [sys.executable, "-m", "yantra", "--output", "json", "resume", run_id,
                    "--runs-dir", str(self.service.runs), "--decision", "confirm"]
            process = await asyncio.to_thread(subprocess.run, args, text=True, capture_output=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("yantra_status", {"request": {"run_id": run_id}})
                    status = result.structuredContent["data"]
                    self.assertIn(status["status"], {"partial", "completed"})
                    self.assertEqual(status["budget"], json.loads(process.stdout)["data"]["budget"])
                    error = await session.call_tool("yantra_read", {"request": {"run_id": run_id, "artifact": "../.env"}})
                    self.assertTrue(error.isError)
                    self.assertEqual(error.structuredContent["error"]["code"], "artifact_not_found")
                    unknown = await session.call_tool("yantra_status", {"request": {"run_id": "run-missing"}})
                    self.assertTrue(unknown.isError)
                    failed = await session.call_tool("yantra_submit", {"request": self.request | {"idempotency_key": "failed-research",
                        "limits": {"total_tools": 0, "max_models": 1, "max_main_loops": 1}}})
                    failed_id = failed.structuredContent["data"]["run_id"]
                    await asyncio.to_thread(self.wait, failed_id)
                    queried = await session.call_tool("yantra_status", {"request": {"run_id": failed_id}})
                    self.assertFalse(queried.isError)
                    self.assertEqual(queried.structuredContent["data"]["status"], "failed")
                    invalid = await session.call_tool("yantra_submit", {"request": {"company": "PRIVATE_SECRET"}})
                    self.assertTrue(invalid.isError)
                    self.assertNotIn("PRIVATE_SECRET", json.dumps(invalid.structuredContent))
        asyncio.run(run())
