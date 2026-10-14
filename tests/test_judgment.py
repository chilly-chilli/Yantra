"""Controlled offline judgment fixtures; no external services or real prices."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yantra.judgment import apply_judgment, investment_judgment, valuation_scenarios
from yantra.reporting import memo_markdown
from yantra.tools import _hash


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        # Any accidental external transport makes this offline test fail.
        for target in ("socket.create_connection", "socket.socket.connect"):
            blocker = patch(target, side_effect=AssertionError("unmocked external network in offline fixture"))
            blocker.start()
            self.addCleanup(blocker.stop)
        ref, digest = "src-synthetic:A1", "a" * 64
        self.anchors = {ref: ({"text": "SYNTHETIC ONLY price 60.0 quoted 2026-10-13T15:00:00+08:00; financial value 10.0", "location": {}},
                              {"source_hash": digest})}
        receipt = {"kind": "valuation_method", "assumptions_version": 1,
                   "results": {"per_share_value": 100.0}}
        calc = "calc:" + _hash(receipt)[:24]
        self.state = {"research_flow_version": 2, "investment_logic_version": 1,
            "mode": "real-model", "raw_materials": True, "run_id": "synthetic-judgment", "status": "partial",
            "research": {"company": "SYNTHETIC ONLY", "horizon": "12个月", "cutoff": "2026-10-13",
                "market": "CN", "exchange": "SSE", "security_code": "600000", "share_class": "A", "trading_currency": "CNY",
                "reference_price": None, "price_time": None, "price_source": None},
            "quote": {"price": 60.0, "raw_value": "60.0", "price_time": "2026-10-13T15:00:00+08:00",
                "acquired_at": "2026-10-14T09:00:00+08:00", "source_ref": ref, "source_hash": digest,
                "currency": "CNY", "unit": "CNY/share", "security_code": "600000", "market": "CN", "exchange": "SSE",
                "share_class": "A", "adjustment": "unadjusted"},
            "assumptions": {"model": "pe", "version": 1, "confirmed": False, "parameters": {}, "basis": {},
                "authorization_source": "default_policy", "safety_margin": {"ratio": .25, "rationale": "合成经营风险和预测误差需要余量"}},
            "artifacts": {"finance": {"data": {"financials": [{"field": "eps", "value": 10, "period": "FY2025",
                "unit": "CNY/share", "currency": "CNY", "consolidated": True, "source_ref": ref,
                "source_hash": digest, "disclosure_date": "2026-04-01"}]}},
                "valuation": {"assumptions_version": 1, "calculation_refs": [calc], "data": {
                    "model": "pe", "per_share_value": 100.0, "currency": "CNY", "value_range": [80.0, 120.0],
                    "range_rule": "合成可比倍数 min/max 区间"}},
                "verification": {"data": {"passed": True}}},
            "issues": [], "calculations": {calc: receipt}, "stop_reasons": ["合成补查预算耗尽"],
            "input_snapshot": {"fictional": False, "manifest": {"materials": [
                {"id": "src-synthetic", "file": "synthetic.json", "chunks": [{"ref": ref, "location": {}}]}]}},
            "preference_snapshot": {"id": "run-default", "version": 0, "source": "run_default",
                "parameters": {"authorization": {"status": "unspecified"}}},
            "hard_checks": {"verification_passed": True}, "deadline": "2026-10-14T09:00:00+08:00",
            "budget": {"model_cost_cny": 0}, "framework": {"version": "value:v1"}, "standard_version": 1}
        self.report = {"投资结论摘要": {"statement": "旧摘要", "gaps": [], "valuation_status": "confirmed"},
            "投资逻辑": {}, "事实与数据基础": {}, "财务分析与估值": {}, "风险因素": [],
            "反向验证条件": [], "持续跟踪指标": [], "市场预期差": {}}

    def judge(self):
        return investment_judgment(self.state, self.anchors)

    def test_single_reliable_source_sufficient_even_when_run_partial(self):
        judgment = self.judge()
        self.assertEqual(judgment["label"], "值得买", judgment["gaps"])
        self.assertEqual(self.state["status"], "partial")
        self.assertEqual(judgment["authorization_source"], "default_policy")
        self.assertFalse(judgment["conditional"])
        self.assertEqual(judgment["safety_margin"]["buy_price_ceiling"], 75)
        self.assertEqual(judgment["safety_margin"]["available_ratio"], .4)
        self.assertEqual([row["per_share_value"] for row in judgment["scenarios"]], [80, 100, 120])
        self.assertIn("2026-10-13T15:00:00+08:00", judgment["statement"])
        self.assertNotIn("2026-10-14T09:00:00+08:00", judgment["statement"])

    def test_price_boundary_is_deterministic(self):
        for price, expected in ((75, "值得买"), (75.000001, "暂不值得买")):
            with self.subTest(price=price):
                self.state["quote"].update(price=price, raw_value=str(price))
                self.anchors["src-synthetic:A1"][0]["text"] += " SYNTHETIC price " + str(price)
                self.assertEqual(self.judge()["label"], expected)

    def test_any_model_selected_margin_has_same_definition(self):
        # The system imposes no conventional 20-40% selection band.
        for ratio, threshold in ((0.0, 100), (.9, 10)):
            self.state["assumptions"]["safety_margin"]["ratio"] = ratio
            self.assertEqual(self.judge()["safety_margin"]["buy_price_ceiling"], threshold)

    def test_missing_or_invalid_margin_does_not_invent_default(self):
        for margin in (None, {"ratio": .2}, {"ratio": -1, "rationale": "invalid"},
                       {"ratio": 1, "rationale": "invalid"}, {"ratio": True, "rationale": "invalid"}):
            with self.subTest(margin=margin):
                self.state["assumptions"]["safety_margin"] = margin
                judgment = self.judge()
                self.assertEqual(judgment["label"], "目前无法判断")
                self.assertIsNone(judgment["safety_margin"])

    def test_manual_and_refused_scenarios_stay_conditional(self):
        self.state["assumptions"].update(authorization_source="provisional", confirmed=False)
        judgment = self.judge()
        self.assertEqual(judgment["label"], "目前无法判断")
        self.assertEqual(judgment["candidate_label"], "值得买")
        self.assertTrue(judgment["conditional"])
        self.assertIn("条件性情景", judgment["statement"])

    def test_source_identity_currency_adjustment_and_cutoff_veto(self):
        mutations = ({"source_hash": "bad"}, {"source_ref": "not-found"}, {"security_code": "600001"},
                     {"market": "HK"}, {"share_class": "H"}, {"exchange": "SZSE"},
                     {"currency": "HKD"}, {"unit": "million"}, {"adjustment": "back_adjusted"},
                     {"adjustment": None}, {"price_time": None}, {"price_time": "2026-10-14T09:00:00+08:00"},
                     {"price": 500, "raw_value": "500"})
        original = copy.deepcopy(self.state["quote"])
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.state["quote"] = original | mutation
                self.assertEqual(self.judge()["label"], "目前无法判断")
        self.state["quote"] = original

    def test_financial_date_and_source_veto(self):
        record = self.state["artifacts"]["finance"]["data"]["financials"][0]
        for change in ({"disclosure_date": "2026-10-14"}, {"source_hash": "bad"}):
            before = dict(record)
            record.update(change)
            self.assertEqual(self.judge()["label"], "目前无法判断")
            record.update(before)

    def test_original_quote_price_and_time_cannot_be_replaced_by_fetch_time(self):
        quote = self.state["quote"]
        quote["price_time"] = "2026-10-13T18:00:00+08:00"
        self.assertEqual(self.judge()["label"], "目前无法判断")
        quote.update(price_time="2026-10-13T15:00:00+08:00", price=500)
        self.assertEqual(self.judge()["label"], "目前无法判断")
        quote.update(price=60, price_time="2026-10-13", raw_price_time="20261013")
        self.anchors["src-synthetic:A1"][0]["text"] += " SYNTHETIC quoted date 20261013"
        self.assertEqual(self.judge()["label"], "值得买")
        quote["price_time"] = "2026-10-13T15:00:00+08:00"
        self.assertEqual(self.judge()["label"], "目前无法判断")

    def test_key_conflict_blocks_and_explained_or_minor_difference_preserves_analysis(self):
        finance = self.state["artifacts"]["finance"]["data"]
        finance["conflicts"] = [{"critical": True, "message": "单位无法解释"}]
        self.assertEqual(self.judge()["label"], "目前无法判断")
        finance["conflicts"] = [{"critical": False, "message": "非关键舍入差异"}]
        self.assertEqual(self.judge()["label"], "值得买")
        self.assertIn("非关键舍入差异", self.judge()["notices"][0])
        finance["conflicts"] = [{"critical": True, "resolved": True, "explanation": "官方最新修订替代旧报告",
                                  "evidence_refs": ["src-synthetic:A1"]}]
        self.assertEqual(self.judge()["label"], "值得买")
        finance["conflicts"][0]["evidence_refs"] = ["invented"]
        self.assertEqual(self.judge()["label"], "目前无法判断")

    def test_verification_and_calculation_veto_preserve_values(self):
        self.state["artifacts"]["verification"]["data"]["passed"] = False
        judgment = self.judge()
        self.assertEqual(judgment["label"], "目前无法判断")
        self.assertTrue(judgment["scenarios"])
        self.assertEqual(judgment["quote"]["price"], 60)
        self.state["artifacts"]["verification"]["data"]["passed"] = True
        next(iter(self.state["calculations"].values()))["results"]["per_share_value"] = 1000
        self.assertEqual(self.judge()["label"], "目前无法判断")

    def test_raw_financial_conflict_and_restated_resolution_keep_history(self):
        finance = self.state["artifacts"]["finance"]["data"]
        record = finance["financials"][0]
        conflict = [copy.deepcopy(record), dict(record, value=15, raw_value="15")]
        finance["conflicts"] = [conflict]
        original = copy.deepcopy(conflict)
        self.assertEqual(self.judge()["label"], "目前无法判断")
        finance["conflict_resolutions"] = [{"conflict_hash": _hash(conflict), "selected_source_ref": record["source_ref"],
            "explanation": "合成官方修订表解释差异", "evidence_refs": [record["source_ref"]]}]
        self.assertEqual(self.judge()["label"], "值得买")
        self.assertEqual(finance["conflicts"][0], original)
        finance["conflict_resolutions"][0]["conflict_hash"] = "different group"
        self.assertEqual(self.judge()["label"], "目前无法判断")

    def test_missing_quote_or_no_financial_data_never_produces_buy(self):
        self.state["quote"] = None
        self.assertEqual(self.judge()["label"], "目前无法判断")
        self.state["artifacts"]["finance"]["data"]["financials"] = []
        self.assertEqual(self.judge()["label"], "目前无法判断")

    def test_dcf_scenarios_keep_existing_values_and_inputs(self):
        rows = [{"name": "bear", "per_share_value": 70, "inputs": {"growth_rate": .01}},
                {"name": "base", "per_share_value": 100, "inputs": {"growth_rate": .03}},
                {"name": "bull", "per_share_value": 130, "inputs": {"growth_rate": .05}}]
        result = valuation_scenarios({"scenarios": rows})
        self.assertEqual([row["label"] for row in result], ["较差", "一般", "较好"])
        self.assertEqual([row["inputs"] for row in result], [row["inputs"] for row in rows])
        self.assertNotIn("label", rows[0])

    def test_report_starts_with_answer_and_keeps_eight_sections(self):
        report = apply_judgment(self.state, self.report, self.anchors)
        self.assertEqual(len(report), 8)
        self.state["report"] = report
        with tempfile.TemporaryDirectory() as directory:
            memo = memo_markdown(self.state, Path(directory))
        self.assertIn("值得买", memo.splitlines()[2])
        self.assertIn("本次采用默认自主研究策略", memo)
        self.assertIn("报价时间：2026-10-13", memo)
        self.assertIn("获取时间：2026-10-14", memo)
        self.assertIn("价格上限", memo)
        self.assertIn("较差", memo)

    def test_historical_demo_and_fictional_runs_do_not_receive_labels(self):
        original = copy.deepcopy(self.report)
        for changed in ({"research_flow_version": 0}, {"mode": "offline-demo"}, {"input_snapshot": {"fictional": True}}):
            state = self.state | changed
            self.assertEqual(apply_judgment(state, copy.deepcopy(original), self.anchors), original)


if __name__ == "__main__":
    unittest.main()
