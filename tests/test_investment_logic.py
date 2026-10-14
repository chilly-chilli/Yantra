"""SYNTHETIC research-contract regressions; real ADK execution, zero remote calls."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from examples.v022.synthetic import SyntheticBackend, TEXT, PRICE, no_search
from yantra.adk_app import run_engine
from yantra.cli import execute, parser
from yantra.engine import Engine, StopRun
from yantra.financial_analysis import screening, THRESHOLDS
from yantra.investment_logic import CHOICE_KINDS, structured_thesis
from yantra.knowledge.preferences import Preferences
from yantra.knowledge.store import KnowledgeStore
from yantra.replay import replay_raw
from yantra.research import RawEngine
from yantra.schemas import MainAction


class MultiModelBackend(SyntheticBackend):
    def proposal(self, finance):
        primary = super().proposal(finance)
        if self.model == "pe":
            self.model = "pb"
            auxiliary = super().proposal(finance)
            self.model = "pe"
            auxiliary["selection_reason"] = "合成净资产方法用于独立辅助比较"
            primary["auxiliary_models"] = [auxiliary]
            primary["selection_reason"] = "合成盈利证据支持PE"
            primary["unused_models"] = {"dcf": "未证明持续现金流", "ev_ebitda": "本次不采用企业倍数", "ddm": "未证明稳定分红"}
        return primary

    async def worker(self, task, local):
        if task["target"] == "valuation" and task["purpose"] != "propose":
            successful = {o["arguments"]["model"] for o in local["tool_outputs"] if o["tool"] == "valuation" and o["response"]["ok"]}
            for candidate in [task["assumptions"], *task["assumptions"].get("auxiliary_models", {}).values()]:
                if candidate["model"] not in successful:
                    return self._tool("valuation", {"model": candidate["model"], "parameters": candidate["parameters"], "assumptions_version": candidate["version"]})
            return self._result(task, local, data={"use_receipts": True})
        return await super().worker(task, local)


class ResearchFixtures:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runs = self.root/"runs"
        self.library = self.root/"knowledge"
        self.preferences = Preferences(KnowledgeStore(self.library))
        self.material = self.root/"synthetic.txt"
        self.material.write_text(TEXT)

    def tearDown(self):
        self.temp.cleanup()

    def setup(self, granted=True, scope=None, mode="autonomous"):
        return self.preferences.setup({"horizon": "18个月", "framework": "quality", "confirmation_mode": mode,
            "topics": ["现金转化"], "authorization": {"status": "granted" if granted else "refused", "scope": list(CHOICE_KINDS) if granted and scope is None else scope or []}})

    def create(self, backend=None, **kwargs):
        return RawEngine.create_raw(self.runs, {"company": "合成公司", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "600000", "share_class": "A", "trading_currency": "CNY"},
            [self.material], backend=backend or SyntheticBackend(), pricing=PRICE, knowledge_dir=self.library, explicit_parameters={}, **kwargs)

    async def run_fixture(self, engine):
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            return await run_engine(engine)

class ResearchContractTests(ResearchFixtures, unittest.IsolatedAsyncioTestCase):
    def test_first_setup_requires_explicit_grant_or_refusal(self):
        with self.assertRaisesRegex(ValueError, "preferences setup"):
            self.create()
        self.assertFalse(self.runs.exists())
        for invalid in ({"horizon": "12个月", "framework": "auto", "confirmation_mode": "autonomous"},
                        {"horizon": "12个月", "framework": "auto", "confirmation_mode": "autonomous", "authorization": {"status": "granted", "scope": []}}):
            self.assertRaises(ValueError, self.preferences.setup, invalid)
        saved = self.setup(False)
        self.assertEqual(saved["parameters"]["authorization"]["status"], "refused")
        self.assertEqual(self.create().state["preference_snapshot"]["version"], 1)

    async def test_refusal_delivers_provisional_scenario_and_later_confirmation(self):
        self.setup(False)
        engine = self.create()
        state = await self.run_fixture(engine)
        self.assertIn("valuation", state["artifacts"], state["stop_reasons"])
        self.assertTrue(state["artifacts"]["verification"]["data"]["passed"], state["issues"])
        self.assertFalse(state["assumptions"]["confirmed"])
        self.assertEqual(state["assumptions"]["authorization_source"], "provisional")
        events = [json.loads(line) for line in (engine.directory/"events.jsonl").read_text().splitlines()]
        self.assertFalse(any(e["kind"] == "autonomous_valuation_authorized" for e in events))
        self.assertTrue(any(e["kind"] == "provisional_valuation_selected" for e in events))
        memo = Path(state["report_path"]).read_text()
        front = memo.split("## 1. 投资结论摘要")[1].split("## 2.")[0]
        self.assertNotIn("每股价值", front)
        self.assertNotIn("目标价", front)
        self.assertIn("[情景计算] PE 每股价值", memo)
        first_report = (engine.directory/"report-v1.json").read_bytes()
        _, result = await replay_raw(self.runs, copy.deepcopy(state))
        self.assertTrue(result["program_audit_passed"], result)
        loaded = Engine.load(self.runs, state["run_id"], backend=SyntheticBackend())
        await self.run_fixture(loaded)
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            await loaded.respond("confirm")
        self.assertEqual(loaded.state["assumptions"]["version"], 2)
        self.assertEqual(loaded.state["assumptions"]["authorization_source"], "user")
        self.assertTrue(loaded.state["artifacts"]["verification"]["data"]["passed"])
        self.assertTrue(loaded.state["artifacts"]["thesis"]["data"]["claims"])
        self.assertEqual(first_report, (engine.directory/"report-v1.json").read_bytes())
        self.assertEqual(loaded.state["report_history"][0]["财务分析与估值"]["assumptions"]["version"], 1)
        self.assertEqual(loaded.state["report_history"][0]["财务分析与估值"]["assumptions"]["authorization_source"], "provisional")
        self.assertTrue((engine.directory/"report-v2.json").exists())

    async def test_grant_persists_but_run_override_does_not_mutate_it(self):
        self.setup()
        engine = self.create(authorization_override={"status": "refused", "scope": []})
        await self.run_fixture(engine)
        self.assertEqual(engine.state["assumptions"]["authorization_source"], "provisional")
        other = self.create()
        await self.run_fixture(other)
        self.assertEqual(other.state["assumptions"]["authorization_source"], "controller")
        self.assertEqual(other.state["research"]["horizon"], "18个月")
        self.assertEqual(self.preferences.setup_snapshot()["version"], 1)
        self.assertTrue(all(c["reason"] and c["impact"] and c["alternatives"] for c in other.state["choices"]))

    async def test_revocation_cannot_approve_new_choices_and_history_stays_fixed(self):
        self.setup()
        engine = self.create()
        await self.run_fixture(engine)
        task = engine._make_task(MainAction(action="delegate_task", target="valuation", purpose="calculate"))
        self.setup(False)
        execution = {"task": task, "local": {"actual_calls": 0}, "started": __import__('time').monotonic(), "failures": {}}
        with self.assertRaises(StopRun):
            engine._prepare_worker_tool(execution, "valuation", {"model": "pe", "parameters": task.assumptions.parameters, "assumptions_version": task.assumptions.version})
        self.assertEqual(engine.state["preference_snapshot"]["version"], 1)
        self.assertEqual(engine.state["assumptions"]["authorization_source"], "provisional")
        self.assertTrue(engine.state["assumption_history"][-1]["confirmed"])
        self.assertEqual(self.create().state["preference_snapshot"]["version"], 2)

    async def test_partial_scope_does_not_authorize_model_assumptions(self):
        self.setup(scope=["models"])
        state = await self.run_fixture(self.create())
        self.assertEqual(state["assumptions"]["authorization_source"], "provisional")
        choices = {c["kind"]: c for c in state["choices"]}
        self.assertEqual(choices["models"]["status"], "controller")
        self.assertEqual(choices["assumptions"]["status"], "provisional")

    async def test_manual_mode_also_delivers_without_waiting(self):
        self.setup(mode="manual")
        state = await self.run_fixture(self.create())
        self.assertEqual(state["assumptions"]["authorization_source"], "provisional")
        self.assertIn("verification", state["artifacts"])
        self.assertNotEqual(state["status"], "waiting")

    async def test_primary_auxiliary_models_have_separate_receipts_and_authority(self):
        self.setup()
        engine = self.create(MultiModelBackend())
        state = await self.run_fixture(engine)
        output = state["artifacts"]["valuation"]["data"]
        self.assertEqual(output["per_share_value"], 30)
        self.assertEqual(output["auxiliary_models"]["pb"]["per_share_value"], 12)
        self.assertTrue(state["artifacts"]["verification"]["data"]["passed"], state["issues"])
        self.assertEqual(output["model_differences"][0]["difference"], -18)
        self.assertNotIn("average", output)
        self.assertEqual(len(state["artifacts"]["valuation"]["calculation_refs"]), 2)
        _, result = await replay_raw(self.runs, copy.deepcopy(state))
        self.assertTrue(result["program_audit_passed"], result)
        state["assumptions"]["auxiliary_models"]["pb"]["confirmed"] = False
        state["assumptions"]["auxiliary_models"]["pb"]["authorization_source"] = "none"
        audit = engine.tools._check_consistency({"results": state["artifacts"], "assumptions": state["assumptions"], "research": state["research"]})
        self.assertFalse(audit.data["passed"])

    async def test_auxiliary_bad_source_is_rejected_without_losing_primary(self):
        class BadAuxiliary(MultiModelBackend):
            def proposal(self, finance):
                proposal = super().proposal(finance)
                proposal["auxiliary_models"][0]["parameters"]["metric"] = copy.deepcopy(proposal["auxiliary_models"][0]["parameters"]["metric"])
                proposal["auxiliary_models"][0]["parameters"]["metric"]["source_hash"] = "bad"
                return proposal
        self.setup()
        state = await self.run_fixture(self.create(BadAuxiliary()))
        self.assertIn("valuation", state["artifacts"])
        self.assertEqual(state["assumptions"]["auxiliary_models"], {})
        self.assertTrue(any("辅助模型" in g for g in state["artifacts"]["proposal"]["data"]["gaps"]))

    async def test_risk_opinion_does_not_hide_fact_or_veto(self):
        class RiskBackend(SyntheticBackend):
            async def worker(self, task, local):
                action = await super().worker(task, local)
                if task["target"] == "thesis" and action["action"] == "return":
                    action["result"]["data"]["risks"] = [{"id": "material-risk", "description": "合成竞争风险", "category": "经营", "suggested_level": "high", "evidence_refs": task["inputs"]["finance"]["evidence_refs"], "trigger": "盈利下滑", "rationale": "合成管理层风险披露"}]
                    if getattr(self, "omit_risk", False):
                        action["result"]["data"]["risks"] = []
                if task["target"] == "verification" and action["action"] == "return":
                    action["result"]["data"].update(passed=False, issues=[{"id": "veto", "severity": "major", "target": "thesis", "requirement": "review_risk", "message": "独立核验否决"}])
                return action
        self.setup()
        engine = self.create(RiskBackend())
        await self.run_fixture(engine)
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            await engine.respond("modify", {"choices": {"risk": [{"id": "material-risk", "user_level": "low", "user_opinion": "用户认为影响较低"}]}})
        risk = engine.state["report"]["风险因素"][0]
        self.assertEqual(risk["suggested_level"], "high")
        self.assertEqual(risk["user_level"], "low")
        self.assertNotIn("user_level", engine.state["report_history"][0]["风险因素"][0])
        self.assertFalse(engine.state["hard_checks"]["verification_passed"])
        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            engine._apply_research_choices({"risk": [{"id": "material-risk", "description": "无风险"}]})
        engine.backend.omit_risk = True
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            await engine.respond("modify", {"model": "pe", "parameters": engine.state["assumptions"]["parameters"], "basis": engine.state["assumptions"]["basis"]})
        retained = engine.state["report"]["风险因素"][0]
        self.assertEqual(retained["description"], "合成竞争风险")
        self.assertEqual(retained["user_level"], "low")

    async def test_setup_cli_and_missing_setup_are_observable(self):
        values = {"horizon": "12个月", "framework": "auto", "confirmation_mode": "autonomous", "authorization": {"status": "refused", "scope": []}}
        args = parser().parse_args(["preferences", "--knowledge-dir", str(self.library), "setup", "--parameters", json.dumps(values)])
        result = await execute(args)
        self.assertEqual(result["version"], 1)
        args = parser().parse_args(["preferences", "--knowledge-dir", str(self.library), "list", "--history"])
        self.assertEqual(len((await execute(args))["requirements"]), 1)
        self.preferences.revoke("research-setup")
        self.assertFalse(self.preferences.setup_snapshot()["active"])
        self.assertEqual(self.create().state["preference_snapshot"]["parameters"]["authorization"]["status"], "refused")

    async def test_business_explanations_keep_group_signal_without_allocating(self):
        self.material.write_text(TEXT+"\nSYNTHETIC ONLY 软件业务预收款与交付错期。制造业务客户信用结算周期延长。")
        self.setup(False)
        engine = self.create()
        await self.run_fixture(engine)
        signals = screening(FinancialScreeningTests().rows())["signals"]
        engine.state["artifacts"]["finance"]["data"]["metrics"]["financial_screening"]["signals"] = signals
        anchors = engine.tools.anchors()
        ref = next(r for r, pair in anchors.items() if "软件业务" in pair[0]["text"])
        thesis = structured_thesis(engine.state["artifacts"]["thesis"]["data"])
        explanations = []
        for business, cause in (("软件", "预收款与交付错期"), ("制造", "客户信用结算周期延长")):
            explanation = {"signal_id": signals[0]["id"], "business": business, "possible_causes": [cause],
                           "supporting_refs": [ref], "opposing_refs": [], "follow_up": ["核对分部现金及应收披露"]}
            explanations.append(explanation)
        thesis["anomaly_explanations"] = explanations
        task = engine._make_task(MainAction(action="delegate_task", target="thesis", purpose="analyze"))
        engine._validate_thesis_sources(thesis, task)
        engine.state["artifacts"]["thesis"]["data"] = thesis
        report = engine._report()
        self.assertEqual(report["财务分析与估值"]["financial_signals"], signals)
        self.assertNotEqual(explanations[0]["possible_causes"], explanations[1]["possible_causes"])
        self.assertTrue(all("无法定位到具体业务" in e["localization"] for e in explanations))
        self.assertTrue(all(s["business_scope"] == "group" for s in signals))

    async def test_tracking_values_and_disconfirmation_status_require_sources(self):
        self.setup(False)
        engine = self.create()
        await self.run_fixture(engine)
        thesis = copy.deepcopy(engine.state["artifacts"]["thesis"]["data"])
        thesis["tracking"] = [{"id": "sales", "name": "营业收入", "definition": "合并年度营业收入", "frequency": "年度", "current_value": 120, "evidence_refs": []}]
        task = engine._make_task(MainAction(action="delegate_task", target="thesis", purpose="analyze"))
        self.assertRaises(StopRun, engine._validate_thesis_sources, thesis, task)
        thesis["tracking"][0]["current_value"] = None
        engine._validate_thesis_sources(thesis, task)
        self.assertEqual(engine.state["artifacts"]["finance"]["data"]["metrics"]["financial_screening"]["monitoring"], "checklist_only")

    def test_real_api_setup_gate_provisional_delivery_and_restart_confirmation(self):
        from yantra.api import create_app
        def factory(company="unused", run_id=None):
            return Engine.load(self.runs, run_id, backend=SyntheticBackend()) if run_id else self.create()
        def send(client, session, message):
            response = client.post("/run_sse", json={"appName": "yantra", "userId": "synthetic", "sessionId": session,
                "newMessage": {"role": "user", "parts": [{"text": message}]}})
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
            return next(json.loads(part["text"]) for event in reversed(events) for part in event.get("content", {}).get("parts", []) if part.get("text"))
        environment = {"YANTRA_RUNS_DIR": str(self.runs), "YANTRA_KNOWLEDGE_DIR": str(self.library)}
        with patch.dict(os.environ, environment), patch("yantra.engine.create_adk_engine", factory), patch("yantra.materials.RawMaterialAdapter.search", no_search):
            with TestClient(create_app()) as client:
                session = client.post("/apps/yantra/users/synthetic/sessions", json={}).json()["id"]
                missing = send(client, session, "合成公司")
                self.assertEqual(missing["error_code"], "research_setup_required")
                values = {"horizon": "18个月", "framework": "quality", "confirmation_mode": "autonomous", "authorization": {"status": "refused", "scope": []}}
                self.assertEqual(send(client, session, "/setup "+json.dumps(values))["version"], 1)
                first = send(client, session, "合成公司")
                self.assertEqual(first["report"]["投资结论摘要"]["valuation_status"], "provisional")
                run_id = first["run_id"]
            with TestClient(create_app()) as client:
                final = send(client, session, "确认")
                self.assertEqual(final["report"]["投资结论摘要"]["valuation_status"], "confirmed")
                trace_path = f"/apps/yantra/users/synthetic/sessions/{session}/research/trace"
                trace = client.get(trace_path).json()
                self.assertEqual(trace["controller"]["preference_version"], 1)
                self.assertTrue(any(c["status"] == "user" for c in trace["controller"]["choices"]))
                self.assertEqual(client.get(trace_path.replace("synthetic", "other")).status_code, 404)
            self.assertTrue((self.runs/run_id/"report-v1.json").exists())
            self.assertTrue((self.runs/run_id/"report-v2.json").exists())

    async def test_explicit_preference_application_invalidates_authority(self):
        self.setup()
        engine = self.create()
        await self.run_fixture(engine)
        self.setup(False)
        (engine.directory/"preferences.request.json").write_text('{}')
        engine._apply_requirements()
        self.assertFalse(engine.state["assumptions"]["confirmed"])
        self.assertEqual(engine.state["preference_snapshot"]["version"], 2)
        self.assertNotIn("valuation", engine.state["artifacts"])

    async def test_replay_rejects_rewritten_financial_signal_and_structured_sources(self):
        self.setup()
        engine = self.create()
        await self.run_fixture(engine)
        state = copy.deepcopy(engine.state)
        state["artifacts"]["finance"]["data"]["metrics"]["financial_screening"]["signals"] = [{"rule": "invented"}]
        state["artifacts"]["thesis"]["data"]["risks"][0]["suggested_level"] = "high"
        _, audit = await replay_raw(self.runs, state)
        self.assertFalse(audit["program_audit_passed"])


class FinancialScreeningTests(unittest.TestCase):
    def rows(self, business="group"):
        rows = []
        for year, revenue, receivables, inventory in [(2023, 100, 20, 20), (2024, 110, 30, 30), (2025, 121, 45, 45)]:
            values = {"revenue": revenue, "cost_of_revenue": revenue*.6, "net_profit_attributable": 20, "equity_attributable": 100,
                      "operating_cash_flow": 8, "receivables": receivables, "inventory": inventory, "total_assets": 200,
                      "total_liabilities": 100 if year < 2025 else 125, "total_equity": 100 if year < 2025 else 75,
                      "goodwill": 31, "selling_expense": 5 if year < 2025 else 15, "administrative_expense": 5 if year < 2025 else 15,
                      "finance_expense": 2, "current_assets": 120, "current_liabilities": 50, "short_term_borrowings": 10, "long_term_borrowings": 20, "bonds_payable": 5}
            for field, value in values.items():
                rows.append({"field": field, "value": value, "normalized_value": value, "normalized_unit": "million", "unit": "million", "period": f"FY{year}",
                    "currency": "CNY", "security_code": "600000", "share_class": "A", "business_scope": business, "consolidated": True,
                    "accounting_scope": "attributable" if field in {"net_profit_attributable", "equity_attributable"} else "consolidated",
                    "source_ref": f"src-synthetic:A{year}", "source_hash": "synthetic"})
        return rows

    def test_four_categories_averages_and_all_annual_thresholds(self):
        result = screening(self.rows())
        last = result["matrix"][-1]
        self.assertEqual(last["metrics"]["gross_margin"], .4)
        self.assertAlmostEqual(last["metrics"]["receivables_turnover"], 121/37.5)
        self.assertAlmostEqual(last["metrics"]["receivables_days"], 365/(121/37.5))
        self.assertEqual(last["metrics"]["net_cash_ratio"], .4)
        self.assertEqual(last["metrics"]["interest_bearing_debt"], 35)
        self.assertEqual(last["metrics"]["roe"], .2)
        self.assertEqual({s["rule"] for s in result["signals"]}, set(THRESHOLDS)-{"quarterly_profit_change"})
        self.assertTrue(all(s["source_refs"] and s["meaning"] == "investigation_signal" for s in result["signals"]))

    def test_group_signals_not_allocated_and_thresholds_do_not_vary(self):
        group, segment = screening(self.rows()), screening(self.rows("software"))
        self.assertEqual(group["thresholds"], segment["thresholds"])
        self.assertEqual([s["observed"] for s in group["signals"]], [s["observed"] for s in segment["signals"]])
        self.assertTrue(all(s["business_localization"] == "无法定位到具体业务" for s in group["signals"]))
        self.assertTrue(all(s["business_scope"] == "software" for s in segment["signals"]))

    def test_invalid_denominators_missing_openings_and_balance_failure(self):
        rows = self.rows()
        for row in rows:
            if row["period"] == "FY2025" and row["field"] == "net_profit_attributable":
                row["normalized_value"] = -1
            if row["period"] == "FY2025" and row["field"] == "total_assets":
                row["normalized_value"] = 210
        result = screening(rows)
        first, last = result["matrix"][0], result["matrix"][-1]
        self.assertNotIn("receivables_turnover", first["metrics"])
        self.assertNotIn("net_cash_ratio", last["metrics"])
        self.assertNotIn("debt_ratio", last["metrics"])
        self.assertNotIn("goodwill_ratio", last["metrics"])
        self.assertTrue(last["data_quality"])
        self.assertIn("gross_margin", last["metrics"])

    def test_conflicting_records_and_periods_are_not_silently_selected(self):
        rows = self.rows()
        bad = copy.deepcopy(rows[0]); bad["normalized_value"] = 999
        rows.append(bad)
        bad = copy.deepcopy(rows[0]); bad["period"] = "2024H1"; rows.append(bad)
        result = screening(rows)
        first = next(r for r in result["matrix"] if r["period"] == "FY2023")
        self.assertNotIn("gross_margin", first["metrics"])
        self.assertTrue(any("conflicting" in g for g in result["gaps"]))

    def test_single_quarter_yoy_threshold_never_uses_cumulative_interim(self):
        rows = [r for r in self.rows() if r["field"] == "net_profit_attributable" and r["period"] != "FY2023"]
        for row in rows:
            year = int(row["period"][2:]); row["period"] = f"{year}Q2"; row["period_basis"] = "quarter"
            row["normalized_value"] = 20 if year == 2024 else 31
        result = screening(rows)
        self.assertEqual(result["signals"][0]["rule"], "quarterly_profit_change")
        for row in rows:
            row["period"] = row["period"].replace("Q2", "H1"); row["period_basis"] = "interim"
        self.assertFalse(screening(rows)["signals"])

    def test_quarterly_consecutive_cash_signals_and_exact_threshold_boundaries(self):
        rows = []
        for period, revenue, ocf, expense in (("2025Q1", 100, 8, 10), ("2025Q2", 100, 8, 15)):
            for field, value in {"revenue": revenue, "net_profit_attributable": 20, "operating_cash_flow": ocf, "selling_expense": expense}.items():
                row = copy.deepcopy(self.rows()[0])
                row.update(field=field, period=period, period_basis="quarter", normalized_value=value,
                           accounting_scope="attributable" if field == "net_profit_attributable" else "consolidated")
                rows.append(row)
        result = screening(rows)
        self.assertEqual([s["rule"] for s in result["signals"]], ["low_net_cash"])


class ExpectationsTests(ResearchFixtures, unittest.IsolatedAsyncioTestCase):
    def expectation_inputs(self, engine, kind="consensus"):
        entry = engine.tools.adapter.add(b"SYNTHETIC ONLY 2026-10-12 ExampleBroker consensus sample 3 forward2027 revenue 120", ".txt", "synthetic-forecast")
        row = {"field": "revenue", "value": 120, "raw_value": "120", "period": "forward2027", "unit": "million", "currency": "CNY", "consolidated": True,
               "accounting_scope": "consolidated", "security_code": "600000", "share_class": "A", "source_ref": entry["chunks"][0]["ref"], "source_hash": entry["source_hash"], "disclosure_date": "2026-10-12", "information_kind": "prediction"}
        existing = json.loads(engine.tools.extraction_path.read_text()) if engine.tools.extraction_path.exists() else {"financials": [], "evidence": []}
        engine.tools.accept_extraction({"financials": existing["financials"]+[row], "evidence": existing["evidence"]}, "2026-10-13")
        own = {"field": "revenue", "value": 130, "period": "forward2027", "earnings_basis": "forward", "currency": "CNY", "unit": "million", "accounting_scope": "consolidated", "security_code": "600000", "share_class": "A", "basis": "有来源的测试增长假设", "evidence_refs": [row["source_ref"]], "realization_conditions": ["增长假设兑现"]}
        external = {"kind": kind, "record": row, "as_of": "2026-10-12", "provider": "ExampleBroker", "sample_size": 3, "sample_scope": "合成三项样本"}
        return {"own_forecasts": [own], "external_forecasts": [external]}

    def test_consensus_research_sample_and_no_external_baseline(self):
        self.setup()
        engine = self.create()
        args = self.expectation_inputs(engine)
        result = engine.tools._compare_expectations(args)
        self.assertEqual(result.data["status"], "consensus")
        self.assertAlmostEqual(result.data["comparisons"][0]["relative_difference"], 130/120-1)
        args["external_forecasts"][0]["kind"] = "research_sample"
        result = engine.tools._compare_expectations(args)
        self.assertEqual(result.data["status"], "research_sample")
        self.assertIn("不代表完整一致预期", result.data["statement"])
        args["external_forecasts"] = []
        result = engine.tools._compare_expectations(args)
        self.assertEqual(result.data["status"], "no_external_baseline")
        self.assertEqual(result.data["comparisons"], [])
        self.assertEqual(len(result.data["own_forecasts"]), 1)
        self.assertNotIn("implied", str(result.data))

    def test_forecast_mismatch_fabrication_and_bad_date_rejected(self):
        self.setup()
        engine = self.create()
        args = self.expectation_inputs(engine)
        for key, value in [("period", "forward2028"), ("currency", "HKD"), ("accounting_scope", "attributable"), ("unit", "ratio")]:
            bad = copy.deepcopy(args); bad["own_forecasts"][0][key] = value
            self.assertRaises(ValueError, engine.tools._compare_expectations, bad)
        bad = copy.deepcopy(args); bad["external_forecasts"][0]["sample_size"] = 9
        self.assertRaises(ValueError, engine.tools._compare_expectations, bad)
        bad = copy.deepcopy(args); bad["external_forecasts"][0]["as_of"] = "2027-01-01"
        self.assertRaises(ValueError, engine.tools._compare_expectations, bad)
