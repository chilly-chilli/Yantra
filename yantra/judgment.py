"""Deterministic buy-price comparison over accepted research evidence.

The research engine owns evidence, authorization and independent verification.
This view adds no execution, fee or consent state and never selects a margin.
"""
from decimal import Decimal, InvalidOperation
import json

from .materials import cutoff_time, numeric, source_number_present
from .schemas import SafetyMarginSelection
from .tools import _hash


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid price") from exc
    if not number.is_finite():
        raise ValueError("non-finite price")
    return number


def valuation_scenarios(valuation):
    """Use the existing computed scenarios or its method-specific range."""
    if valuation.get("scenarios"):
        labels = {"bear": "较差", "base": "一般", "bull": "较好"}
        return [dict(row, label=labels.get(row["name"], row["name"])) for row in valuation["scenarios"]]
    value = valuation.get("per_share_value")
    if value is None:
        return []
    bounds = valuation.get("value_range") or [value, value]
    return [{"name": name, "label": label, "per_share_value": amount,
             "basis": basis, "range_rule": valuation.get("range_rule"), "status": "calculated"}
            for name, label, amount, basis in (("bear", "较差", min(bounds), "已有方法区间下沿"),
                ("base", "一般", value, "当前主模型基准"), ("bull", "较好", max(bounds), "已有方法区间上沿"))]


def investment_judgment(state, anchors):
    research, assumptions, artifacts = state["research"], state["assumptions"], state["artifacts"]
    finance = artifacts.get("finance", {}).get("data", {})
    valuation_artifact = artifacts.get("valuation", {})
    valuation = valuation_artifact.get("data", {})
    verification = artifacts.get("verification", {}).get("data", {})
    quote = state.get("quote")
    gaps, notices = [], []

    def source_valid(ref, digest):
        return ref in anchors and digest == anchors[ref][1].get("source_hash")

    if not all(research.get(key) for key in ("security_code", "market", "exchange", "share_class", "trading_currency")):
        gaps.append("证券身份或上市市场尚未确定")
    if not finance.get("financials"):
        gaps.append("缺少可追溯的财务事实")
    for row in finance.get("financials", []):
        if not source_valid(row.get("source_ref"), row.get("source_hash")):
            gaps.append("财务原始来源或哈希无法核对")
        try:
            if not row.get("disclosure_date") or cutoff_time(row["disclosure_date"]) > cutoff_time(research["cutoff"]):
                gaps.append("财务披露日期缺失或晚于研究截止")
        except (ValueError, TypeError):
            gaps.append("财务披露日期无法核对")
    for artifact in artifacts.values():
        artifact_data = artifact.get("data", {})
        conflicts = artifact.get("conflicts", []) + artifact_data.get("conflicts", [])
        resolutions = artifact_data.get("conflict_resolutions", [])
        for conflict in conflicts:
            if isinstance(conflict, str):
                gaps.append("未解释的资料冲突：" + conflict)
            elif isinstance(conflict, list):
                resolved = next((row for row in resolutions if row.get("conflict_hash") == _hash(conflict)
                    and row.get("selected_source_ref") in {entry.get("source_ref") for entry in conflict}
                    and row.get("selected_source_ref") in {entry.get("source_ref") for entry in finance.get("financials", [])}
                    and row.get("explanation") and row.get("evidence_refs")
                    and all(ref in anchors for ref in row["evidence_refs"])), None)
                if resolved:
                    notices.append(resolved["explanation"])
                elif verification.get("passed") and any(row.get("conflict_hash") == _hash(conflict) and row.get("reason")
                     and {entry.get("source_ref") for entry in conflict} <= set(row.get("evidence_refs", []))
                     and all(ref in anchors for ref in row.get("evidence_refs", [])) for row in artifact_data.get("conflict_assessments", [])):
                    notices.append("独立核验认定为非关键财务差异，候选原值保留。")
                else:
                    fields = sorted({row.get("field", "未知字段") for row in conflict})
                    gaps.append("可能改变买入判断的财务冲突尚未解释：" + "、".join(fields))
            elif conflict.get("resolved") and conflict.get("explanation") and conflict.get("evidence_refs") and all(ref in anchors for ref in conflict["evidence_refs"]):
                notices.append(conflict["explanation"])
            elif conflict.get("critical", True):
                gaps.append("可能改变买入判断的资料冲突尚未解释：" + conflict.get("message", "口径冲突"))
            else:
                notices.append("非关键差异：" + conflict.get("message", "资料差异"))
    if not verification.get("passed") or any(issue.get("severity") in {"critical", "major"} for issue in state.get("issues", [])):
        gaps.append("独立核验未通过，或存在关键来源／数学问题")
    if not valuation or valuation_artifact.get("assumptions_version") != assumptions["version"]:
        gaps.append("缺少当前版本的每股估值")
    refs = valuation_artifact.get("calculation_refs", [])
    receipts = state.get("calculations", {})
    if not refs or any(ref not in receipts or ref != "calc:" + _hash(receipts[ref])[:24] for ref in refs):
        gaps.append("估值计算凭证缺失或损坏")
    elif not any(receipts[ref].get("results", {}).get("per_share_value") == valuation.get("per_share_value")
                 and receipts[ref].get("assumptions_version") == assumptions["version"] for ref in refs):
        gaps.append("每股估值与当前版本计算凭证不一致")

    price = value = None
    if quote:
        try:
            price = _number(quote.get("price"))
            if price <= 0:
                raise ValueError("nonpositive price")
        except ValueError:
            gaps.append("行情价格无效")
        identity = ("security_code", "share_class", "market", "exchange")
        if any(quote.get(key) != research.get(key) or not quote.get(key) for key in identity):
            gaps.append("行情与研究证券身份不一致")
        if quote.get("currency") != research.get("trading_currency") or quote.get("unit") != quote.get("currency", "") + "/share":
            gaps.append("行情币种或每股单位不匹配")
        if quote.get("adjustment") != "unadjusted":
            gaps.append("当前报价必须明确为未复权价格")
        if not source_valid(quote.get("source_ref"), quote.get("source_hash")):
            gaps.append("行情缺少可回读的原始响应及哈希")
        else:
            raw_value = quote.get("raw_value", str(quote.get("price")))
            text = anchors[quote["source_ref"]][0]["text"]
            try:
                source_quote = json.loads(text)
            except json.JSONDecodeError:
                source_quote = None
            try:
                located = _number(source_quote.get("price")) == price if isinstance(source_quote, dict) else source_number_present(raw_value, text)
                if _number(numeric(raw_value)) != price or not located:
                    gaps.append("行情原始响应无法定位该价格")
            except (ValueError, TypeError):
                gaps.append("行情原始价格无法核对")
            if isinstance(source_quote, dict) and any(source_quote.get(key) != quote.get(key)
                for key in ("price_time", "currency", "unit", "security_code", "share_class", "market", "exchange", "adjustment")):
                gaps.append("行情身份、时间或口径与原始记录不一致")
            raw_time = quote.get("raw_price_time") or quote.get("price_time")
            if not raw_time or raw_time not in text:
                gaps.append("行情原始响应无法定位实际报价时间")
            elif quote.get("price_time"):
                try:
                    original_time, actual_time = cutoff_time(raw_time), cutoff_time(quote["price_time"])
                    same_time = (len(quote["price_time"]) <= 10 and original_time.date() == actual_time.date()) if len(raw_time) <= 10 else original_time == actual_time
                    if not same_time:
                        gaps.append("报价时间与原始响应不一致")
                except (ValueError, TypeError):
                    gaps.append("原始报价时间无法核对")
        try:
            if not quote.get("price_time") or cutoff_time(quote["price_time"]) > cutoff_time(research["cutoff"]):
                gaps.append("报价时间缺失或晚于研究截止；获取时间不能替代报价时间")
        except (ValueError, TypeError):
            gaps.append("报价时间无法核对")
    else:
        gaps.append("缺少证券、币种及报价时间明确的实际股价")
    try:
        value = _number(valuation.get("per_share_value"))
        if value <= 0:
            raise ValueError("nonpositive valuation")
    except ValueError:
        gaps.append("缺少有效的每股估值")
    if valuation and valuation.get("currency") != research.get("trading_currency"):
        gaps.append("估值与交易币种不一致")
    margin = None
    try:
        margin = SafetyMarginSelection.model_validate(assumptions.get("safety_margin")).model_dump()
        if any(ref not in anchors for ref in margin["evidence_refs"]):
            gaps.append("安全边际依据不能定位")
    except ValueError:
        gaps.append("缺少本次模型选择的安全边际比例及理由")
    authority = assumptions.get("authorization_source", "none")
    conditional = not (authority == "default_policy" or (assumptions.get("confirmed") and authority in {"user", "controller"}))
    if conditional:
        gaps.append("估值选择尚未正式确认，仅能作条件性情景比较")
    threshold = available_margin = candidate_label = None
    if margin and value is not None and value > 0:
        ratio = _number(margin["ratio"])
        threshold = value * (1 - ratio)
        if price is not None and price > 0:
            available_margin = 1 - price / value
            candidate_label = "值得买" if price <= threshold else "暂不值得买"
        margin.update(definition="(基准每股价值−实际股价)/基准每股价值", benchmark_value=float(value),
                      buy_price_ceiling=float(threshold), available_ratio=float(available_margin) if available_margin is not None else None,
                      formula="buy_price_ceiling = base_per_share_value * (1 - safety_margin_ratio)")
    label = "目前无法判断" if gaps else candidate_label
    reason = "；".join(dict.fromkeys(gaps)) if gaps else (
        f"实际股价满足本次{margin['ratio']:.2%}安全边际" if label == "值得买" else f"实际股价未满足本次{margin['ratio']:.2%}安全边际")
    quote_statement = (f"实际股价{quote.get('price', '缺失')} {quote.get('currency', '')}，报价时间{quote.get('price_time') or '缺失'}。" if quote else "实际股价缺失。")
    statement = f"{label}：{reason}。{quote_statement}"
    if not gaps:
        statement += f"初步判断依赖{research['horizon']}情景与假设，估值空间不保证未来收益。"
    return {"label": label, "statement": statement, "quote": quote, "horizon": research["horizon"],
            "safety_margin": margin, "value_range": valuation.get("value_range") or
                ([min(row["per_share_value"] for row in valuation.get("scenarios", []) if "per_share_value" in row),
                  max(row["per_share_value"] for row in valuation.get("scenarios", []) if "per_share_value" in row)]
                 if any("per_share_value" in row for row in valuation.get("scenarios", [])) else []),
            "scenarios": valuation_scenarios(valuation), "conditional": conditional,
            "authorization_source": authority, "candidate_label": candidate_label,
            "gaps": list(dict.fromkeys(gaps)), "notices": list(dict.fromkeys(notices)),
            "conditions": artifacts.get("thesis", {}).get("data", {}).get("disconfirming", []),
            "calculation_refs": refs}


def apply_judgment(state, report, anchors):
    """Keep historical and explicitly fictional runs on their original semantics."""
    if state.get("research_flow_version", 0) < 2 or state.get("mode") == "offline-demo" or state.get("input_snapshot", {}).get("fictional"):
        return report
    judgment = investment_judgment(state, anchors)
    summary = report["投资结论摘要"]
    summary.update(statement=judgment["statement"], buy_judgment=judgment)
    summary["gaps"] = list(dict.fromkeys(summary.get("gaps", []) + judgment["gaps"]))
    report["财务分析与估值"].update(safety_margin=judgment["safety_margin"], scenarios=judgment["scenarios"])
    return report
