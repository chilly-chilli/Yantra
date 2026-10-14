"""Real LOCAL supplied bytes + manually reviewed extraction; scripted workflow, no LLM.

Does not authenticate documents against an exchange or obtain live prices.
No source files are rewritten. Values must match exact existing tokens/anchors.
"""
import asyncio
import json
from pathlib import Path
import re
from unittest.mock import patch

from yantra.adk_app import run_engine
from yantra.replay import replay_raw
from yantra.research import RawEngine
from .synthetic import SyntheticBackend, PRICE, no_search

ROOT = Path(__file__).resolve().parents[2]


class LocalBackend(SyntheticBackend):
    def __init__(self, reject=False):
        super().__init__("pe", benchmark=True)
        self.extractions = {}
        self.reject = reject

    async def extract(self, payload):
        return self.extractions[payload["document"]["source_hash"]]

    def proposal(self, finance):
        rows = finance["financials"]
        metric = next(r for r in rows if r["field"] == "eps" and r["security_code"] == "000858")
        peer = next(r for r in rows if r["field"] == "eps" and r["security_code"] == "600809")
        prices = {r["security_code"]: r for r in rows if r["field"] == "price"}
        peer_price = prices.get("600809")
        multiples = []
        if peer_price:
            multiples = [{"multiple_type": "pe", "value": peer_price["value"] / peer["value"], "security_code": "600809", "share_class": "A", "currency": "CNY", "period": "FY2025", "earnings_basis": "FY", "accounting_scope": "attributable", "as_of": peer_price["period"], "source_ref": peer_price["source_ref"], "source_hash": peer_price["source_hash"], "quote": peer_price["raw_value"], "raw_value": peer_price["raw_value"], "price_record": peer_price, "fundamental_record": peer}]
        params = {"security_code": "000858", "share_class": "A", "currency": "CNY", "period": "FY2025", "earnings_basis": "FY", "metric": metric, "multiples": multiples,
            "comparable_rule": "本地白酒A股样本：山西汾酒；同CNY、FY2025归母基本EPS、2026-10-12价格重建PE。单一可比只提供条件性点值，不代表全行业分布。"}
        if prices.get("000858"):
            params["benchmark"] = {"kind": "reference_price", "record": prices["000858"], "as_of": "2026-10-12", "earnings_basis": "point_in_time", "provider": "用户提供本地表，未联网验证行情商"}
        for m in params["multiples"]:
            m["per_share_basis"] = "basic EPS"
        return {"model": "pe", "parameters": params, "basis": {"metric": {"source_refs": [metric["source_ref"]], "formula": "FY2025归母基本EPS"}, "comparable_rule": {"source_refs": [peer["source_ref"]] + ([peer_price["source_ref"]] if peer_price else []), "formula": params["comparable_rule"]}},
            "scope": "本地真实文件的数学与控制流程验证；不包含真实LLM推理或外部真实性确认", "gaps": ["单一可比、盈利变化巨大，倍数迁移和投研结论未验证"]}

    async def worker(self, task, local):
        if task["target"] == "thesis":
            p = self.proposal(task["inputs"]["finance"]["data"])
            baseline = p["parameters"].get("benchmark")
            return self._result(task, local, data={"claims": [{"type": "fact", "statement": "FY2025基本每股收益引用本地财报原文", "evidence_refs": [p["parameters"]["metric"]["source_ref"]]}], "hypotheses": [{"type": "judgment", "statement": "同业PE可以迁移是待核验的假说"}], "risks": ["单一可比与盈利大幅变化"], "disconfirming": ["盈利基础无法比较"], "tracking": ["同口径年度EPS与可比集"], "expectations": {"status": "hypothesis" if baseline else "unverified_hypothesis", "baseline": baseline, "statement": "本地参考价格，不是市场一致预期"}, "summary": "本地材料支持条件性相对估值计算，独立经济判断及外部真实性尚未验证。", "gaps": ["未验证真实LLM投研与市场行情真实性"]})
        return await super().worker(task, local)


def prepare_reviewed_extractions(engine, backend):
    documents = engine.tools.snapshot()["manifest"]["materials"]
    for doc in documents:
        if doc["file"].endswith(".pdf"):
            cover = doc["chunks"][0]["text"]
            if "五粮液" in cover:
                code, raw, page, day = "000858", "2.3068", 7, "2026-04-30"
                assert "2026 年 4 月 30 日" in cover
            elif "汾酒" in cover:
                code, raw, page, day = "600809", "10.0383", 5, "2026-04-30"
                assert "二〇二六年四月" in cover
                # Month-only date is represented by its conservative latest bound, not invented publication day.
            else:
                raise ValueError("unexpected report")
            anchor = next(c for c in doc["chunks"] if c["location"].get("page") == page and "基本每股收益" in c["text"] and raw in c["text"])
            identity = next(c for c in doc["chunks"] if code in c["text"] and ("股票代码" in c["text"] or "A股" in c["text"] or "A 股" in c["text"]))
            row = {"field": "eps", "value": float(raw), "raw_value": raw, "period": "FY2025", "unit": "CNY/share", "currency": "CNY", "consolidated": True, "security_code": code, "share_class": "A", "accounting_scope": "attributable", "source_ref": anchor["ref"], "source_hash": doc["source_hash"], "disclosure_date": day,
                "disclosure_source_ref": doc["chunks"][0]["ref"], "disclosure_quote": cover,
                "share_scope": "basic EPS", "normalization_note": "封面仅披露2026年4月时，取该月末为保守可获得日期上界；不是精确披露日" if code == "600809" else None}
            backend.extractions[doc["source_hash"]] = {"financials": [row], "evidence": [{"kind": "fact", "statement": f"A股身份 {code}", "quote": identity["text"], "source_ref": identity["ref"], "source_hash": doc["source_hash"], "disclosure_date": day, "question": "证券身份"}], "gaps": []}
            if code == "000858":
                share_class = next(c for c in doc["chunks"] if "人民币普通股" in c["text"])
                backend.extractions[doc["source_hash"]]["evidence"].append({"kind": "fact", "statement": "五粮液人民币普通股类别", "quote": share_class["text"], "source_ref": share_class["ref"], "source_hash": doc["source_hash"], "disclosure_date": day, "question": "股本类别"})
        elif doc["file"].endswith(".xlsx"):
            rows = []
            for code in ["000858", "600809"]:
                if backend.reject and code == "600809":
                    continue
                anchor = next(c for c in doc["chunks"] if f"A{3 if code == '000858' else 5}={code}" in c["text"])
                raw = re.search(r"C\d+=([\d.]+)", anchor["text"]).group(1)
                assert "2026-10-12" in anchor["text"]
                rows.append({"field": "price", "value": float(raw), "raw_value": raw, "period": "2026-10-12", "unit": "CNY/share", "currency": "CNY", "consolidated": True, "security_code": code, "share_class": "A", "accounting_scope": "attributable", "source_ref": anchor["ref"], "source_hash": doc["source_hash"], "disclosure_date": "2026-10-12"})
            backend.extractions[doc["source_hash"]] = {"financials": rows, "evidence": [], "gaps": []}


async def validate(storage=ROOT / "examples/v022/output/local"):
    from yantra.knowledge.preferences import Preferences
    from yantra.knowledge.store import KnowledgeStore
    preferences = Preferences(KnowledgeStore(Path(storage) / "knowledge"))
    if not any(p["id"] == "research-setup" for p in preferences.list(history=True)):
        preferences.setup({"horizon": "12个月", "framework": "quality", "confirmation_mode": "autonomous",
                           "authorization": {"status": "refused", "scope": []}})
    summaries = []
    for reject in [False, True]:
        backend = LocalBackend(reject)
        with patch("yantra.materials.RawMaterialAdapter.search", no_search):
            engine = RawEngine.create_raw(storage, {"company": "五粮液", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "000858", "trading_currency": "CNY", "confirmation_mode": "autonomous"},
                [ROOT/"公开财报及数据"/name for name in ["五粮液2025年年度报告.PDF", "山西汾酒2025年年度报告.PDF", "东财口径.xlsx"]], backend=backend, pricing=PRICE, framework="quality")
            prepare_reviewed_extractions(engine, backend)
            engine.state["mode"] = "local-material-validation"
            engine._save()
            state = await run_engine(engine)
        proposal = state["artifacts"].get("proposal", {}).get("data", {})
        value = state["artifacts"].get("valuation", {}).get("data", {})
        if reject:
            assert proposal.get("model") == "not_applicable", state["stop_reasons"]
            assert not value
        else:
            assert value.get("per_share_value") is not None, (state["stop_reasons"], proposal)
            assert state["artifacts"]["verification"]["data"]["passed"], state["issues"]
            expected = 2.3068 * 114.8 / 10.0383
            assert abs(value["per_share_value"] - expected) < .000001
        replay, comparison = await replay_raw(storage, state)
        assert comparison["calculation_equal"] and comparison["program_audit_passed"], comparison
        summary = {"case": "missing-comparable-refusal" if reject else "local-real-PE", "run_id": state["run_id"], "status": state["status"], "model": proposal.get("model"), "per_share_value": value.get("per_share_value"), "value_range": value.get("value_range"), "market_comparison": value.get("market_comparison"), "program_verification": state["artifacts"]["verification"]["data"]["passed"], "replay": comparison, "report": state["report_path"], "remote_model_calls": 0,
            "scope": "本地用户材料、人工核对提取、注入脚本后端；没有真实LLM推理、行情联网或原文件外部认证"}
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
    Path(storage).mkdir(parents=True, exist_ok=True)
    (Path(storage)/"validation.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2)+"\n")
    return summaries


if __name__ == "__main__":
    asyncio.run(validate())
