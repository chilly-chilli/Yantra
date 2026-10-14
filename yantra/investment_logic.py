"""Research contract helpers; business state remains owned by RawEngine."""
from decimal import Decimal

from .schemas import ExpectationArguments, ThesisData
from .tools import _convert

CHOICE_KINDS = ("perspective", "models", "assumptions", "risk", "disconfirmation", "tracking", "expectations")


def structured_thesis(data):
    """Migrate textual lists without inventing a risk level, current value or status."""
    result = ThesisData.model_validate(data).model_dump(exclude_none=True)
    for key in ("risks", "disconfirming", "tracking"):
        converted = []
        for index, row in enumerate(result[key], 1):
            if isinstance(row, str):
                base = {"id": f"{key}-{index}", "evidence_refs": []}
                if key == "risks":
                    row = base | {"description": row, "category": "未分类", "suggested_level": "unknown", "trigger": "待核查", "rationale": "旧式文本未给出分级依据"}
                elif key == "disconfirming":
                    row = base | {"thesis": "待对应论点", "condition": row, "frequency": "待确定", "status": "unknown"}
                else:
                    row = base | {"name": row, "definition": "待明确口径", "frequency": "待确定", "threshold": None, "current_value": None}
            converted.append(row)
        result[key] = converted
    return result


def compare_expectations(arguments):
    args = ExpectationArguments.model_validate(arguments)
    comparisons = []
    def quantity(value, unit):
        return Decimal(str(value)) if unit == "ratio" or unit.endswith("/share") or unit == "元/股" else _convert(value, unit, "million")
    matched = set()
    for own in args.own_forecasts:
        if not own.period.startswith({"FY": "FY", "TTM": "TTM", "forward": "forward"}[own.earnings_basis]):
            raise ValueError("forecast period and earnings basis disagree")
        for index, external in enumerate(args.external_forecasts):
            row = external.record
            keys = ("field", "period", "currency", "accounting_scope", "security_code", "share_class")
            if any(getattr(own, k) != getattr(row, k) for k in keys) or own.business_scope != (row.business_scope or "group"):
                continue
            if (own.unit == "ratio") != (row.unit == "ratio") or (own.unit.endswith("/share")) != (row.unit.endswith("/share")):
                raise ValueError("forecast units do not match")
            matched.add(index)
            ours, theirs = quantity(own.value, own.unit), quantity(row.value, row.unit)
            comparisons.append({"field": own.field, "period": own.period, "kind": external.kind, "provider": external.provider,
                "sample_size": external.sample_size, "sample_scope": external.sample_scope, "as_of": external.as_of,
                "own_value": float(ours), "external_value": float(theirs), "unit": own.unit if own.unit == "ratio" or own.unit.endswith("/share") else "million",
                "absolute_difference": float(ours-theirs), "relative_difference": float(ours/theirs-1) if theirs > 0 else None,
                "source_refs": own.evidence_refs+[row.source_ref], "basis": own.basis, "realization_conditions": own.realization_conditions})
    if len(matched) != len(args.external_forecasts):
        raise ValueError("external and own forecast periods/identity/accounting scope do not match")
    kinds = {r["kind"] for r in comparisons}
    status = "consensus" if kinds == {"consensus"} else "research_sample" if kinds == {"research_sample"} else "mixed" if kinds else "no_external_baseline"
    statements = {"consensus": "与已核对的一致预期比较，差异依赖本方预测及兑现条件。", "research_sample": "比较已取得的研报样本，不代表完整一致预期。",
                  "mixed": "一致预期与部分研报逐项标注样本范围。", "no_external_baseline": "本次未取得可比的外部预测基准"}
    return {"status": status, "statement": statements[status], "own_forecasts": [f.model_dump() for f in args.own_forecasts],
            "comparisons": comparisons, "baseline": [f.model_dump() for f in args.external_forecasts] or None}


def model_differences(primary, auxiliaries):
    result = []
    rows = [primary] + list(auxiliaries.values())
    for index, left in enumerate(rows):
        for right in rows[index+1:]:
            lv, rv = left.get("per_share_value"), right.get("per_share_value")
            if lv is None or rv is None or left.get("currency") != right.get("currency"):
                continue
            result.append({"left_model": left["model"], "right_model": right["model"], "left_value": lv, "right_value": rv,
                "difference": rv-lv, "relative_to_left": rv/lv-1 if lv > 0 else None,
                "left_basis": left.get("basis", {}), "right_basis": right.get("basis", {}),
                "left_formula": left.get("formula"), "right_formula": right.get("formula"),
                "left_parameters": left.get("inputs", {}), "right_parameters": right.get("inputs", {}),
                "higher_model": left["model"] if lv > rv else right["model"] if rv > lv else None,
                "explanation": f"{left['model']}按左侧公式及参数计算为{lv}，{right['model']}按右侧公式及参数计算为{rv}；参数和来源分别列示，差异不是统一倍数或平均值。",
                "notice": "分别依赖各自公式、参数与来源；不自动平均，不以差异证明某模型正确"})
    return result
