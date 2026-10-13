"""Fixed offline demonstration decisions. No model inference or live retrieval."""
from __future__ import annotations

from typing import Any


class OfflineDemoBackend:
    def config_summary(self) -> dict:
        return {"backend": "scripted-offline-demo", "model": None,
                "notice": "固定材料与脚本策略；不代表真实模型推理"}

    async def main(self, view: dict) -> dict:
        a = view["artifacts"]
        if view["stop_reasons"]:
            return self._main("finalize", rationale="达到停止条件，保留成果和缺口")
        verification = a.get("verification")
        if verification:
            issues = verification["data"].get("issues", [])
            serious = [i for i in issues if i["severity"] in ("critical", "major")]
            if serious:
                issue = serious[0]
                return self._main("delegate_task", target=issue["target"], purpose="repair",
                                  question=issue["message"], rationale="依据核验问题定向修复")
            return self._main("finalize", rationale="已核验，提交八部分报告")
        if not a.get("finance"):
            return self._main("delegate_task", target="finance", purpose="collect",
                              question="建立有原始位置和口径的财务底表", rationale="先建立事实基础")
        if not a["finance"]["data"].get("financials"):
            return self._main("finalize", rationale="没有可用财务材料，明确缺口")
        if not a.get("thesis"):
            return self._main("delegate_task", target="thesis", purpose="analyze",
                              question="分析投资假说、反证和市场比较基准", rationale="依据底表分析机制")
        if not a.get("proposal"):
            return self._main("delegate_task", target="valuation", purpose="propose",
                              question="提出工业企业自由现金流折现模型及参数依据", rationale="正式估值前提出参数")
        if not view["assumptions"]["confirmed"]:
            return self._main("ask_user", question="请确认、修改或拒绝当前估值模型和参数。", rationale="必须取得明确输入")
        if not a.get("valuation"):
            return self._main("delegate_task", target="valuation", purpose="calculate",
                              question="按当前已确认版本计算情景及敏感性", rationale="用户已确认，使用程序计算")
        return self._main("delegate_task", target="verification", purpose="audit",
                          question="核验事实、引用、计算、口径和投资论证缺口", rationale="结束前核验")

    @staticmethod
    def _main(action: str, **kwargs) -> dict:
        return {"action": action, **kwargs}

    async def worker(self, task: dict, local: dict) -> dict:
        role = task["target"]
        outputs = local["tool_outputs"]
        successful = {x["tool"]: x["response"] for x in outputs if x["response"]["ok"]}
        if outputs and not outputs[-1]["response"]["ok"]:
            last = outputs[-1]
            if last["response"].get("error", {}).get("retryable"):
                return self._tool(last["tool"], last["arguments"])
            return self._result(task, local, status="blocked", unresolved=[last["response"]["error"]["message"]])
        if role == "finance":
            if "read_materials" not in successful:
                return self._tool("read_materials", {"company": task["company"]})
            if "extract_financials" not in successful:
                return self._tool("extract_financials", {"company": task["company"]})
            extracted = successful["extract_financials"]["data"]
            if not extracted.get("financials"):
                return self._result(task, local, status="partial", data=extracted,
                                    unresolved=extracted.get("gaps", ["没有财务字段"]))
            if "calculate_metrics" not in successful:
                return self._tool("calculate_metrics", {"records": extracted["financials"]})
            return self._result(task, local, data={**extracted, "metrics": successful["calculate_metrics"]["data"]},
                                unresolved=extracted.get("gaps", []))
        if role == "thesis":
            if "read_materials" not in successful:
                return self._tool("read_materials", {"company": task["company"]})
            refs = task["inputs"]["finance"]["evidence_refs"]
            data = {
                "claims": [{"type": "fact", "statement": "财务底表列示营业收入与自由现金流的期间变化。", "evidence_refs": refs}],
                "hypotheses": [{"type": "assumption", "statement": "若需求与现金转化延续，现金流增长可能支持长期价值。", "evidence_refs": refs,
                                "counter_evidence": "缺少订单、客户集中度与竞争材料，无法确认持续性。", "catalyst": "后续财报和有来源的订单披露", "condition": "收入增长与现金转化均保持"}],
                "risks": ["需求放缓", "资本开支增加挤压现金流", "折现率与永续增长率对估值影响较大", "资料不足导致假说无法充分核实"],
                "disconfirming": ["收入下降且经营现金流连续恶化", "自由现金流低于当前基础且无法解释", "资本开支长期高于计划"],
                "tracking": ["有来源的季度收入", "经营现金流与资本开支", "订单与客户集中度", "折现率及净债务"],
                "expectations": {"status": "unverified_hypothesis", "baseline": None,
                                 "statement": "没有带来源和日期的市场共识基准，无法断言市场预期差。"},
                "gaps": ["缺少客户、订单与竞争资料", "缺少带来源和日期的市场共识基准"],
            }
            return self._result(task, local, data=data, unresolved=data["gaps"])
        if role == "valuation":
            if task["purpose"] == "propose":
                metrics = task["inputs"]["finance"]["data"]["metrics"]
                fcf = metrics["fcf"]
                latest_period = sorted(fcf)[-1]
                parameters = {"base_fcf": fcf[latest_period], "growth_rate": .05, "discount_rate": .10,
                              "terminal_growth": .02, "years": 5, "net_debt": metrics["net_debt"],
                              "shares": 100, "currency": "CNY", "unit": "million"}
                records = task["inputs"]["finance"]["data"]["financials"]
                shares = [r for r in records if r["period"] == latest_period and r["field"] == "shares"]
                if not shares:
                    return self._result(task, local, status="blocked", unresolved=["缺少当前期间股数"])
                parameters["shares"] = shares[0]["value"]
                latest_refs = {r["field"]: r["source_ref"] for r in records if r["period"] == latest_period}
                basis = {"base_fcf": {"formula": "operating_cash_flow - capex", "source_refs": [latest_refs["operating_cash_flow"], latest_refs["capex"]], "calculation_ref": metrics["calculation_ref"]},
                         "net_debt": {"formula": "debt - cash", "source_refs": [latest_refs["debt"], latest_refs["cash"]], "calculation_ref": metrics["calculation_ref"]}, "shares": shares[0]["source_ref"],
                         "growth_rate": "演示假设，缺少订单依据，需用户确认",
                         "discount_rate": "演示要求回报率假设，非可观察市场数据",
                         "terminal_growth": "演示长期假设，需小于折现率", "years": "显式预测5年，演示模型范围"}
                return self._result(task, local, data={"model": "dcf", "parameters": parameters, "basis": basis,
                                    "scope": "自由现金流为正且较稳定的非金融工业企业；不适合金融、早期或强周期公司。",
                                    "gaps": ["增长与折现率缺少市场依据", "没有真实价格"]})
            if "valuation" not in successful:
                return self._tool("valuation", {"parameters": task["assumptions"]["parameters"],
                                                "assumptions_version": task["assumptions"]["version"]})
            return self._result(task, local, data=successful["valuation"]["data"])
        if role == "verification":
            if "check_consistency" not in successful:
                return self._tool("check_consistency", {"results": task["inputs"], "assumptions": task["assumptions"],
                                                        "research": local["research"]})
            data = successful["check_consistency"]["data"]
            return self._result(task, local, data=data,
                                status="complete" if data.get("passed") else "partial",
                                unresolved=[i["message"] for i in data.get("issues", [])])
        raise ValueError("unknown role")

    @staticmethod
    def _tool(name: str, arguments: dict) -> dict:
        return {"action": "call_tool", "tool": name, "arguments": arguments,
                "rationale": "用授权工具获得可检查的成果"}

    @staticmethod
    def _result(task: dict, local: dict, status="complete", data=None, unresolved=None) -> dict:
        successful = [x["response"] for x in local["tool_outputs"] if x["response"]["ok"]]
        evidence = sorted({r for x in successful for r in x["evidence_refs"]} |
                          {r for value in task["inputs"].values() if isinstance(value, dict) for r in value.get("evidence_refs", [])})
        calculations = sorted({r for x in successful for r in x["calculation_refs"]} |
                              {r for value in task["inputs"].values() if isinstance(value, dict) for r in value.get("calculation_refs", [])})
        return {"action": "return", "result": {"task_id": task["task_id"], "status": status, "data": data or {},
                "evidence_refs": evidence, "assumptions_version": task["assumptions"]["version"],
                "calculation_refs": calculations, "conflicts": [], "unresolved": unresolved or [],
                "suggested_tasks": [], "actual_calls": local["actual_calls"]}}
