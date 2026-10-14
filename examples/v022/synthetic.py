"""Explicitly SYNTHETIC offline integration fixtures; no remote model or market service."""
from pathlib import Path
import json
from unittest.mock import patch

from yantra.policies import OfflineDemoBackend
from yantra.research import RawEngine

PRICE = {"input_per_million": 2, "output_per_million": 8, "currency": "CNY", "source": "synthetic-test-only", "as_of": "2026-10-13"}
TEXT = """SYNTHETIC ONLY published 2026-10-12; FY2025 financial disclosure dated 2026-01-01 CNY A total ordinary shares consolidated attributable
600000 eps 2 bvps 8 dividend_per_share 1 ebitda 100 net_debt 20 minority_interest 5 shares 10
600001 PE 10 PB 1 EV/EBITDA 8
600002 PE 20 PB 2 EV/EBITDA 12
2026-10-12 price 15 CNY/share
ebit 100 tax_rate 0.25 depreciation 10 capex 20 delta_nwc 5
Management statement: competition and sustainability risk.
"""
TEXT = " ".join(TEXT.splitlines())


def no_search(adapter, query, purpose, max_results):
    return {"query": query, "purpose": purpose, "results": [], "errors": ["offline_fixture"], "acquired_at": "2026-10-13T00:00:00Z", "service_cost_cny": 0}


class SyntheticBackend(OfflineDemoBackend):
    last_usage = None

    def __init__(self, model="pe", benchmark=False, missing_bridge=False):
        self.model, self.benchmark, self.missing_bridge = model, benchmark, missing_bridge

    async def choose_framework(self, payload):
        return {"identifier": "quality", "reason": "合成流程验收，框架不绑定估值方法"}

    async def extract(self, payload):
        doc = payload["document"]
        self.source_anchors = doc["anchors"]
        rows = []
        for field, number in [("eps", 2), ("bvps", 8), ("dividend_per_share", 1), ("ebitda", 100), ("net_debt", 20), ("minority_interest", 5), ("shares", 10), ("price", 15), ("ebit", 100), ("tax_rate", .25), ("depreciation", 10), ("capex", 20), ("delta_nwc", 5)]:
            if self.missing_bridge and field == "net_debt":
                continue
            per_share = field in {"eps", "bvps", "dividend_per_share", "price"}
            anchor = next(a for a in doc["anchors"] if f"{field} {number}" in a["text"])
            rows.append({"field": field, "value": number, "raw_value": str(number), "period": "2026-10-12" if field == "price" else "FY2025",
                "unit": "CNY/share" if per_share else "ratio" if field == "tax_rate" else "million shares" if field == "shares" else "million",
                "currency": "CNY", "consolidated": True, "accounting_scope": "attributable" if per_share else "consolidated",
                "security_code": "600000", "share_class": "A", "share_scope": {"shares": "total ordinary shares", "eps": "basic EPS", "bvps": "ending book value per share"}.get(field),
                "source_ref": anchor["ref"], "source_hash": doc["source_hash"], "disclosure_date": "2026-10-12" if field == "price" else "2026-01-01"})
        anchor = next(a for a in doc["anchors"] if "Management statement:" in a["text"])
        return {"financials": rows, "evidence": [{"statement": "合成公司陈述竞争风险", "kind": "management_statement", "speaker": "合成管理层", "quote": "Management statement: competition and sustainability risk.",
            "source_ref": anchor["ref"], "source_hash": doc["source_hash"], "disclosure_date": "2026-01-01", "question": "竞争风险"}], "gaps": []}

    def proposal(self, finance):
        rows = {r["field"]: r for r in finance["financials"]}
        model = self.model
        field = {"pe": "eps", "pb": "bvps", "ev_ebitda": "ebitda", "ddm": "dividend_per_share"}[model]
        metric = rows[field]
        params = {"security_code": "600000", "share_class": "A", "currency": "CNY", "period": "FY2025", "earnings_basis": "FY", "metric": metric}
        basis = {"metric": {"source_refs": [metric["source_ref"]], "formula": "合成原始材料"}}
        if model == "ddm":
            params.update(cost_of_equity=.1, terminal_growth=.02)
            basis.update(cost_of_equity="合成股权成本假设", terminal_growth="合成长期增长假设")
        else:
            values = {"pe": [10, 20], "pb": [1, 2], "ev_ebitda": [8, 12]}[model]
            label = {"pe": "PE", "pb": "PB", "ev_ebitda": "EV/EBITDA"}[model]
            params.update(comparable_rule="同币种、同类股、同FY合成可比；同日倍数中位数", multiples=[{"multiple_type": model, "value": value, "security_code": f"60000{i+1}", "share_class": "A", "currency": "CNY", "period": "FY2025", "earnings_basis": "FY", "accounting_scope": "consolidated" if model == "ev_ebitda" else "attributable", "as_of": "2026-01-01", "source_ref": metric["source_ref"], "source_hash": metric["source_hash"], "quote": f"{label} {value}", "raw_value": str(value)} for i, value in enumerate(values)])
            for m in params["multiples"]:
                m["per_share_basis"] = metric["share_scope"] if model in {"pe", "pb"} else None
                m["source_ref"] = next(a["ref"] for a in self.source_anchors if m["security_code"] in a["text"] and m["quote"] in a["text"])
            basis["comparable_rule"] = "合成同业可比选择判断；不是统计置信区间"
        if model == "ev_ebitda":
            params.update({k: rows.get(k) for k in ["net_debt", "minority_interest", "shares"]})
        if self.benchmark:
            params["benchmark"] = {"kind": "current_price", "record": rows["price"], "as_of": "2026-10-12", "earnings_basis": "point_in_time", "provider": "synthetic"}
        return {"model": model, "parameters": params, "basis": basis, "scope": "明确标注合成材料的测试，不是真实研究", "gaps": []}

    async def main(self, view):
        a = view["artifacts"]
        for role, purpose in [("finance", "collect"), ("thesis", "analyze"), ("valuation", "propose")]:
            if not a.get("proposal" if purpose == "propose" else role):
                return self._main("delegate_task", target=role, purpose=purpose)
        if a["proposal"]["data"]["model"] != "not_applicable" and not a.get("valuation"):
            if not view["assumptions"]["confirmed"]:
                return self._main("ask_user", question="确认合成估值提案")
            return self._main("delegate_task", target="valuation", purpose="calculate")
        if not a.get("verification"):
            return self._main("delegate_task", target="verification", purpose="audit")
        return self._main("finalize")

    async def worker(self, task, local):
        successful = {o["tool"]: o["response"] for o in local["tool_outputs"] if o["response"]["ok"]}
        role = task["target"]
        if role == "finance":
            if "extract_financials" not in successful:
                return self._tool("extract_financials", {"company": task["company"]})
            if "calculate_metrics" not in successful:
                return self._tool("calculate_metrics", {"records": successful["extract_financials"]["data"]["financials"]})
            return self._result(task, local, data={"use_receipts": True})
        if role == "thesis":
            proposal = self.proposal(task["inputs"]["finance"]["data"])
            baseline = proposal["parameters"].get("benchmark")
            return self._result(task, local, data={"claims": [{"type": "management_statement", "statement": "管理层陈述竞争风险", "evidence_refs": task["inputs"]["finance"]["evidence_refs"]}], "hypotheses": [{"type": "judgment", "statement": "可比倍数迁移需核验"}], "risks": ["倍数不可迁移"], "disconfirming": ["盈利基础恶化"], "tracking": ["年度EPS"], "expectations": {"status": "unverified_hypothesis" if not baseline else "hypothesis", "baseline": baseline, "statement": "待解释指定基准差异"}, "gaps": []})
        if role == "valuation":
            if task["purpose"] == "propose":
                return self._result(task, local, data=self.proposal(task["inputs"]["finance"]["data"]))
            if "valuation" not in successful:
                return self._tool("valuation", {"model": task["assumptions"]["model"], "parameters": task["assumptions"]["parameters"], "assumptions_version": task["assumptions"]["version"]})
            return self._result(task, local, data=successful["valuation"]["data"], unresolved=successful["valuation"]["data"].get("gaps", []))
        if "check_consistency" not in successful:
            return self._tool("check_consistency", {"results": task["inputs"], "assumptions": task["assumptions"]})
        return self._result(task, local, data=successful["check_consistency"]["data"])


def create(directory, model="pe", **kwargs):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    material = directory / "synthetic.txt"
    material.write_text(TEXT)
    backend = SyntheticBackend(model, **kwargs)
    return RawEngine.create_raw(directory / "runs", {"company": "合成测试公司", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "600000", "trading_currency": "CNY", "confirmation_mode": "autonomous"}, [material], backend=backend, pricing=PRICE, framework="quality")


async def main():
    from yantra.adk_app import run_engine
    for method in ["pe", "pb", "ev_ebitda", "ddm"]:
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            engine = create(Path(__file__).parent / "output" / method, method, benchmark=True)
            state = await run_engine(engine)
            print(json.dumps({"model": method, "run_id": state["run_id"], "status": state["status"], "report": state["report_path"]}, ensure_ascii=False))


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
