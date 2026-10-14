"""Integrated v0.2.2 method gates, authority, receipts, reports and offline replay."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from examples.v022.synthetic import create, no_search
from yantra.adk_app import run_engine
from yantra.engine import StopRun
from yantra.replay import replay_raw
from yantra.schemas import Assumptions, MainAction
from yantra.tools import _dcf, _hash
from yantra.valuation import calculate, fcff_from_ebit, validate_inputs


class IntegratedValuationTests(unittest.IsolatedAsyncioTestCase):
    async def run_method(self, directory, model="pe", **kwargs):
        engine = create(directory, model, **kwargs)
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            await run_engine(engine)
        self.assertIn("verification", engine.state["artifacts"], engine.state["stop_reasons"])
        return engine

    async def test_each_method_full_existing_workflow_and_report(self):
        for method, price in [("pe", 30), ("pb", 12), ("ev_ebitda", 97.5), ("ddm", 12.75)]:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as d:
                engine = await self.run_method(d, method, benchmark=True)
                s = engine.state
                self.assertEqual(s["assumptions"]["model"], method)
                self.assertTrue(s["assumptions"]["confirmed"])
                self.assertIn("valuation", s["artifacts"], s["artifacts"].get("proposal"))
                v = s["artifacts"]["valuation"]["data"]
                self.assertAlmostEqual(v["per_share_value"], price)
                self.assertEqual(v["calculation"]["kind"], "valuation_method")
                self.assertTrue(s["artifacts"]["verification"]["data"]["passed"], s["issues"])
                self.assertEqual(v["market_comparison"]["baseline"], s["report"]["财务分析与估值"]["valuation_price_comparison"]["baseline"])
                self.assertAlmostEqual(v["market_comparison"]["difference"], price / 15 - 1, places=5)
                self.assertIn("管理层陈述", Path(s["report_path"]).read_text())
                self.assertIn("EV/EBITDA" if method == "ev_ebitda" else method.upper(), Path(s["report_path"]).read_text())
                _, comparison = await replay_raw(Path(d)/"runs", s)
                self.assertTrue(comparison["calculation_equal"], comparison)
                self.assertTrue(comparison["program_audit_passed"])

    async def test_missing_bridge_retains_enterprise_never_equity_price(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d, "ev_ebitda", missing_bridge=True)
            v = engine.state["artifacts"]["valuation"]["data"]
            self.assertEqual(v["enterprise_value"], 1000)
            self.assertIsNone(v["equity_value"])
            self.assertIsNone(v["per_share_value"])
            self.assertEqual(v["method_status"], "enterprise_only")
            self.assertNotEqual(engine.state["status"], "completed")

    async def test_unknown_and_explicit_zero_net_cash_are_distinct(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d, "ev_ebitda")
            p = copy.deepcopy(engine.state["assumptions"]["parameters"])
            p["net_debt"]["value"] = 0
            self.assertEqual(calculate("ev_ebitda", p)["per_share_value"], 99.5)
            p["net_debt"]["value"] = -20
            self.assertEqual(calculate("ev_ebitda", p)["per_share_value"], 101.5)
            p["net_debt"] = None
            self.assertIsNone(calculate("ev_ebitda", p)["per_share_value"])

    async def test_source_hash_anchor_value_cutoff_and_method_gates(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d, "pe")
            original = engine.state["assumptions"]["parameters"]
            for key, value in [("source_hash", "missing"), ("source_ref", "src-missing:A1"), ("value", 99), ("disclosure_date", "2027-01-01"), ("security_code", "999999"), ("share_class", "H"), ("accounting_scope", "parent"), ("unit", "HKD/share")]:
                with self.subTest(key=key):
                    p = copy.deepcopy(original); p["metric"][key] = value
                    with self.assertRaises(ValueError):
                        engine.tools.validate_method_sources("pe", p, engine.state["research"])
            for key, value in [("multiple_type", "pb"), ("period", "FY2024"), ("earnings_basis", "TTM"), ("per_share_basis", "diluted EPS"), ("as_of", ""), ("source_hash", "missing")]:
                with self.subTest(key=key):
                    p = copy.deepcopy(original); p["multiples"][0][key] = value
                    with self.assertRaises(ValueError):
                        engine.tools.validate_method_sources("pe", p, engine.state["research"])
            p = copy.deepcopy(original); p["force_method"] = "pe"
            self.assertRaises(ValueError, validate_inputs, "pe", p)
            p = copy.deepcopy(original)
            for m in p["multiples"]:
                m["as_of"] = "2026-01-02"
            self.assertRaises(ValueError, engine.tools.validate_method_sources, "pe", p, engine.state["research"])
            row = copy.deepcopy(original["metric"])
            row.update(value=26, raw_value="26")  # Digits inside 2026 are not original EPS evidence.
            rejected = engine.tools.accept_extraction({"financials": [row]}, engine.state["research"]["cutoff"])
            self.assertFalse(rejected["financials"])

    async def test_illegal_numeric_inputs(self):
        for model in ["pe", "ev_ebitda", "ddm"]:
            with tempfile.TemporaryDirectory() as d:
                engine = await self.run_method(d, model)
                original = engine.state["assumptions"]["parameters"]
                if model == "pe":
                    forward = copy.deepcopy(original)
                    forward.update(period="forward2027", earnings_basis="forward")
                    forward["metric"]["period"] = "forward2027"
                    for m in forward["multiples"]:
                        m.update(period="forward2027", earnings_basis="forward")
                    self.assertRaises(ValueError, calculate, "pe", forward)
                    forward["metric"]["information_kind"] = "prediction"
                    self.assertEqual(calculate("pe", forward)["per_share_value"], 30)
                for bad in [float('nan'), float('inf'), -1, 0, True]:
                    p = copy.deepcopy(original); p["metric"]["value"] = bad
                    self.assertRaises(ValueError, calculate, model, p)
                if model == "ev_ebitda":
                    p = copy.deepcopy(original); p["shares"]["value"] = -10
                    self.assertRaises(ValueError, calculate, model, p)
                if model == "ddm":
                    p = copy.deepcopy(original); p["terminal_growth"] = p["cost_of_equity"]
                    self.assertRaises(ValueError, calculate, model, p)

    async def test_authorized_method_parameters_version_cannot_be_substituted(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d)
            task = engine._make_task(MainAction(action="delegate_task", target="valuation", purpose="calculate"))
            execution = {"task": task, "local": {"actual_calls": 0}, "started": time.monotonic(), "failures": {}}
            args = {"model": "pe", "parameters": task.assumptions.parameters, "assumptions_version": task.assumptions.version}
            engine._prepare_worker_tool(execution, "valuation", args)
            for changed in [args | {"model": "pb"}, args | {"assumptions_version": 999}, args | {"parameters": {}}]:
                self.assertRaises(StopRun, engine._prepare_worker_tool, execution, "valuation", changed)
            engine.state["assumptions"]["confirmed"] = False
            self.assertRaises(StopRun, engine._prepare_worker_tool, execution, "valuation", args)

    async def test_method_switch_and_manual_change_invalidate_results(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d)
            old = engine.state["artifacts"]["valuation"]["task_id"]
            engine.backend.model = "pb"
            proposal = engine.backend.proposal(engine.state["artifacts"]["finance"]["data"])
            (engine.directory/"parameters.request.json").write_text(json.dumps({k: proposal[k] for k in ["model", "parameters", "basis"]}))
            engine._apply_pending_parameters()
            self.assertEqual(engine.state["assumptions"]["model"], "pb")
            self.assertEqual(engine.state["assumptions"]["version"], 2)
            self.assertIn(old, engine.state["invalidated"])
            self.assertNotIn("valuation", engine.state["artifacts"])
            self.assertNotIn("verification", engine.state["artifacts"])
            with patch("yantra.materials.RawMaterialAdapter.search", no_search):
                state = await engine.respond("modify", {"model": "pb", "parameters": proposal["parameters"], "basis": proposal["basis"]})
            self.assertEqual(state["assumptions"]["version"], 3)
            self.assertEqual(state["artifacts"]["valuation"]["data"]["per_share_value"], 12)
            bad = copy.deepcopy(proposal["parameters"]); bad["metric"]["source_hash"] = "made-up"
            with self.assertRaises(ValueError):
                await engine.respond("modify", {"model": "pb", "parameters": bad, "basis": proposal["basis"]})

    async def test_missing_market_baseline_is_unresolved_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d)
            c = engine.state["report"]["财务分析与估值"]["valuation_price_comparison"]
            self.assertEqual(c["status"], "unresolved")
            self.assertIsNone(c["difference"])
            self.assertIsNone(c["baseline"])
            self.assertNotEqual(engine.state["status"], "completed")

    async def test_tampering_and_unknown_receipts_fail_recomputation(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d)
            s = copy.deepcopy(engine.state)
            ref = next(r for r in s["artifacts"]["valuation"]["calculation_refs"] if s["calculations"][r]["kind"] == "valuation_method")
            s["calculations"][ref]["results"]["per_share_value"] = 999
            _, comparison = await replay_raw(Path(d)/"runs", s)
            self.assertFalse(comparison["calculation_equal"])
            self.assertFalse(comparison["program_audit_passed"])
            # Even a recomputed content hash cannot turn false mathematics into a valid receipt.
            s = copy.deepcopy(engine.state)
            receipt = s["calculations"].pop(ref)
            receipt["results"]["per_share_value"] = 999
            new_ref = f"calc:{_hash(receipt)[:24]}"
            s["calculations"][new_ref] = receipt
            s["artifacts"]["valuation"]["calculation_refs"] = [new_ref]
            s["artifacts"]["valuation"]["data"]["calculation"] = receipt
            _, comparison = await replay_raw(Path(d)/"runs", s)
            self.assertFalse(comparison["calculation_equal"])
            self.assertFalse(comparison["program_audit_passed"])
            s = copy.deepcopy(engine.state)
            unknown = {"kind": "unknown", "inputs": {}, "results": {"price": 9}}
            s["calculations"][f"calc:{_hash(unknown)[:24]}"] = unknown
            _, comparison = await replay_raw(Path(d)/"runs", s)
            self.assertFalse(comparison["calculation_equal"])

    async def test_manual_method_confirmation_and_verifier_veto(self):
        with tempfile.TemporaryDirectory() as d:
            engine = create(d, "pb")
            engine.state["research"]["confirmation_mode"] = "manual"
            engine._save()
            with patch("yantra.materials.RawMaterialAdapter.search", no_search):
                await run_engine(engine)
                self.assertEqual(engine.state["status"], "partial")
                self.assertEqual(engine.state["assumptions"]["authorization_source"], "provisional")
                self.assertIn("valuation", engine.state["artifacts"])
                await engine.respond("confirm")
            self.assertEqual(engine.state["artifacts"]["valuation"]["data"]["per_share_value"], 12)
            engine.state["artifacts"]["verification"]["data"]["passed"] = False
            engine._finish()
            self.assertFalse(engine.state["hard_checks"]["verification_passed"])
            replay, comparison = await replay_raw(Path(d)/"runs", engine.state)
            self.assertTrue(comparison["calculation_equal"])
            self.assertFalse(replay.state["hard_checks"]["verification_passed"])

    async def test_market_types_dates_and_sources(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d, benchmark=True)
            original = engine.state["assumptions"]["parameters"]
            alias = copy.deepcopy(original)
            alias["metric"]["unit"] = "元/股"
            alias["benchmark"]["record"]["unit"] = "元/股"
            self.assertEqual(calculate("pe", alias)["per_share_value"], 30)
            self.assertEqual(calculate("pe", alias)["market_comparison"]["difference"], 1)
            for change in [{"kind": "consensus_target", "sample_size": 10}, {"as_of": "2027-01-01"}, {"record": original["metric"]}]:
                p = copy.deepcopy(original); p["benchmark"].update(change)
                self.assertRaises(ValueError, engine.tools.validate_method_sources, "pe", p, engine.state["research"])
            p = copy.deepcopy(original)
            p["benchmark"]["record"]["source_hash"] = "no-original"
            self.assertRaises(ValueError, engine.tools.validate_method_sources, "pe", p, engine.state["research"])

    async def test_sourced_consensus_and_operating_expectation_are_distinct(self):
        from examples.v022 import synthetic
        with tempfile.TemporaryDirectory() as d, patch.object(synthetic, "TEXT", synthetic.TEXT + " Consensus synthetic sample_size 3"):
            engine = await self.run_method(d, benchmark=True)
            p = copy.deepcopy(engine.state["assumptions"]["parameters"])
            p["benchmark"].update(kind="consensus_target", sample_size=3)
            engine.tools.validate_method_sources("pe", p, engine.state["research"])
            self.assertEqual(calculate("pe", p)["market_comparison"]["kind"], "consensus_target")
            p["benchmark"].update(kind="earnings_expectation", record=p["metric"], as_of="2026-01-01", earnings_basis="FY", own_value=3, own_basis="显式合成盈利预测")
            engine.tools.validate_method_sources("pe", p, engine.state["research"])
            c = calculate("pe", p)["market_comparison"]
            self.assertEqual(c["kind"], "earnings_expectation")
            self.assertEqual(c["our_value"], 3)
            self.assertEqual(c["difference"], .5)

    async def test_manual_comparable_change_recalculates_same_method(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d)
            old = engine.state["artifacts"]["valuation"]["task_id"]
            p = copy.deepcopy(engine.state["assumptions"]["parameters"])
            with patch("yantra.materials.RawMaterialAdapter.search", no_search):
                await engine.respond("modify", {"multiples": [p["multiples"][1]]})
            self.assertEqual(engine.state["assumptions"]["version"], 2)
            self.assertEqual(engine.state["artifacts"]["valuation"]["data"]["per_share_value"], 40)
            self.assertIn(old, engine.state["invalidated"])

    async def test_rejected_method_proposal_invalidates_previous_authority(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d)
            old = engine.state["artifacts"]["valuation"]["task_id"]
            valid = engine.backend.proposal
            def unsupported(finance):
                proposal = valid(finance)
                proposal["parameters"]["metric"]["value"] = -2
                return proposal
            with patch.object(engine.backend, "proposal", unsupported):
                await engine._delegate(MainAction(action="delegate_task", target="valuation", purpose="propose"))
            self.assertEqual(engine.state["artifacts"]["proposal"]["data"]["model"], "not_applicable")
            self.assertFalse(engine.state["assumptions"]["confirmed"])
            self.assertNotIn("valuation", engine.state["artifacts"])
            self.assertIn(old, engine.state["invalidated"])

    async def test_unit_conversion_and_ebit_bridge_against_reference(self):
        with tempfile.TemporaryDirectory() as d:
            engine = await self.run_method(d, "ev_ebitda")
            original = engine.state["assumptions"]["parameters"]
            p = copy.deepcopy(original)
            for key in ["metric", "net_debt", "minority_interest", "shares"]:
                p[key]["value"] *= 1_000_000
                p[key]["unit"] = "shares" if key == "shares" else "元"
            self.assertEqual(calculate("ev_ebitda", p), calculate("ev_ebitda", original))
            self.assertEqual(fcff_from_ebit(100, .25, 10, 20, 5), 60)
            self.assertEqual(engine.state["artifacts"]["finance"]["data"]["metrics"]["fcff"], 60)
            self.assertRaises(ValueError, fcff_from_ebit, 100, 1.2, 10, 20, 5)

    def test_legacy_missing_method_and_dcf_algorithm(self):
        self.assertEqual(Assumptions.model_validate({"version": 1}).model, "dcf")
        p = {"base_fcf": 120, "growth_rate": .05, "discount_rate": .1, "terminal_growth": .02, "years": 5, "net_debt": 100, "shares": 100, "currency": "CNY", "unit": "million"}
        cash = [120*1.05**year for year in range(1, 6)]
        expected = (sum(f/1.1**(i+1) for i, f in enumerate(cash)) + cash[-1]*1.02/(.1-.02)/1.1**5 - 100)/100
        self.assertAlmostEqual(_dcf(p)["per_share_value"], expected, places=5)
        for years in [0, -1, 1.5, float('nan')]:
            self.assertRaises(ValueError, _dcf, p | {"years": years})
