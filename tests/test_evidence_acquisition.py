"""Controlled original-data and public-access acceptance checks; no remote calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from yantra.materials import RawMaterialAdapter, source_number_present
from yantra.v2_tools import RawToolRegistry


class EvidenceAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        RawMaterialAdapter.create(self.directory, "合成公司", [])
        self.registry = RawToolRegistry(self.directory)
        self.addCleanup(patch.stopall)
        patch("socket.create_connection", side_effect=AssertionError("external transport forbidden")).start()

    def record(self, value, unit="million", date="2026-01-01", text=None, channel=None, restatement=None):
        source = self.registry.adapter.add((text or f"{date}披露 Revenue {value} {unit}").encode(), ".txt", "controlled original", disclosure_date=date, channel=channel)
        return {"field": "revenue", "value": value, "raw_value": str(value), "period": "FY2025", "unit": unit,
                "currency": "CNY", "consolidated": True, "accounting_scope": "consolidated", "security_code": "600000",
                "share_class": "A", "disclosure_date": date, "source_ref": source["chunks"][0]["ref"],
                "source_hash": source["source_hash"], "restatement": restatement}

    def accept(self, *records):
        return self.registry.accept_extraction({"financials": records, "evidence": [], "gaps": []}, "2026-10-14")

    def test_single_original_source_is_sufficient_for_financial_acceptance(self):
        accepted = self.accept(self.record(100))
        self.assertEqual(len(accepted["financials"]), 1)
        self.assertEqual(accepted["conflicts"], [])

    def test_equivalent_units_preserve_conflict_and_explanation(self):
        accepted = self.accept(self.record(100, "million"), self.record(100_000_000, "unit"))
        self.assertEqual(len(accepted["conflicts"]), 1)
        self.assertEqual(len(accepted["conflict_resolutions"]), 1)
        self.assertNotIn("存在未调节", " ".join(accepted["gaps"]))
        metrics = self.registry._calculate_metrics({"records": accepted["financials"]})
        self.assertEqual([r["normalized_value"] for r in metrics.data["calculation"]["inputs"]], [100, 100])
        self.assertNotIn("未解决冲突", " ".join(metrics.data["metrics"]["gaps"]))

    def test_later_dated_raw_correction_selects_new_value_preserves_old(self):
        earlier = self.record(100)
        later = self.record(120, date="2026-02-01", text="2026-02-01披露 更正 Revenue 120 million", restatement="更正")
        accepted = self.accept(earlier, later)
        self.assertEqual([r["value"] for r in accepted["financials"]], [120])
        self.assertEqual([r["value"] for r in accepted["conflicts"][0]], [100, 120])
        self.assertEqual(accepted["conflict_resolutions"][0]["selected_source_ref"], later["source_ref"])

    def test_unexplained_conflict_or_fabricated_correction_is_not_selected(self):
        accepted = self.accept(self.record(100), self.record(120, date="2026-02-01", restatement="更正"))
        self.assertEqual(len(accepted["financials"]), 2)
        self.assertEqual(accepted["conflict_resolutions"], [])

    def test_discovery_snippets_and_yahoo_aggregates_require_original(self):
        for channel in ({"evidence_kind": "discovery"}, {"adapter_id": "yahoo", "original_disclosure_required": True}):
            with self.subTest(channel=channel):
                accepted = self.accept(self.record(99, channel=channel))
                self.assertEqual(accepted["financials"], [])

    def test_json_record_anchor_preserves_price_and_actual_time_together(self):
        source = self.registry.adapter.add(json.dumps({"quote": {"price": 20, "price_time": "2026-10-13T07:00:00Z"}}).encode(), ".json", "controlled quote")
        chunk = next(c for c in source["chunks"] if c["location"].get("json_pointer") == "/quote")
        self.assertIn("20", chunk["text"])
        self.assertIn("2026-10-13T07:00:00Z", chunk["text"])
        self.assertTrue(source_number_present("20", chunk["text"]))
        self.assertFalse(source_number_present("2", chunk["text"]))

    def test_new_formal_fetch_failure_does_not_retry_identical_url(self):
        original = httpx.Client
        self.registry.adapter.no_retry = True
        with patch("yantra.materials.httpx.Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kw)), patch("primp.Client.get", side_effect=AssertionError("must not retry")):
            self.assertRaises(ValueError, self.registry.adapter.fetch_raw, "https://example.test/report")

    def test_login_document_is_not_added_as_financial_evidence(self):
        original = httpx.Client
        self.registry.adapter.no_retry = True
        with patch("yantra.materials.httpx.Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text='<input type="password">')), **kw)):
            self.assertRaisesRegex(ValueError, "登录", self.registry.adapter.fetch_raw, "https://example.test/login")
        self.assertEqual(self.registry.load_manifest()["materials"], [])

    def test_independent_review_retains_noncritical_unused_conflict_as_notice(self):
        rows = [self.record(100), self.record(120)]
        first = rows[0]
        source = self.registry.anchors()[first["source_ref"]][0]["text"]
        accepted = self.registry.accept_extraction({"financials": rows, "evidence": [{"statement": "合成原始披露",
            "kind": "fact", "quote": source, "source_ref": first["source_ref"], "source_hash": first["source_hash"],
            "disclosure_date": "2026-01-01", "question": "原始记录"}], "gaps": []}, "2026-10-14")
        conflict = accepted["conflict_refs"][0]
        assessment = {"conflict_hash": conflict["conflict_hash"], "evidence_refs": conflict["evidence_refs"],
                      "reason": "独立审查：此历史字段未用于估值或论点，本次不选取该冲突数值"}
        args = {"results": {"finance": {"data": accepted}}, "assumptions": {}, "conflict_assessments": [assessment]}
        checked = self.registry._check_consistency(args).data
        self.assertTrue(checked["passed"], checked)
        self.assertEqual(checked["issues"][0]["severity"], "minor")
        self.assertEqual(checked["conflict_assessments"], [assessment])
        self.assertEqual(accepted["conflicts"][0], self.registry._extract_financials({"company": "合成公司"}).data["conflicts"][0])
        for assumptions in ({"parameters": {"metric": {"source_ref": first["source_ref"]}}},
                            {"basis": {"metric": {"source_refs": [first["source_ref"]]}}}):
            args["assumptions"] = assumptions
            rejected = self.registry._check_consistency(args).data
            self.assertFalse(rejected["passed"])
            self.assertEqual(rejected["conflict_assessments"], [])
        args["assumptions"] = {}
        assessment["conflict_hash"] = "invented"
        self.assertFalse(self.registry._check_consistency(args).data["passed"])


if __name__ == "__main__":
    unittest.main()
