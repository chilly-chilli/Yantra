"""Pure mathematics; authorization and original-source checks belong to the caller."""
from decimal import Decimal
from statistics import median

from ..schemas import MethodInputs, MethodOutput
from ..tools import _convert, _number, _out

METHODS = {"dcf", "pe", "pb", "ev_ebitda", "ddm"}
PER_SHARE_UNITS = {"CNY/share", "HKD/share", "USD/share", "元/股"}


def per_share_unit_matches(unit, currency):
    return unit == f"{currency}/share" or (unit == "元/股" and currency == "CNY")


def quantity(record):
    if record.unit in PER_SHARE_UNITS:
        return _number(record.value)
    return _convert(record.value, record.unit, "million shares" if record.field == "shares" else "million")


def validate_inputs(model, parameters):
    if model not in METHODS - {"dcf"}:
        raise ValueError("unsupported valuation method")
    def numeric_guards(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in {"value", "cost_of_equity", "terminal_growth", "own_value", "sample_size"} and isinstance(v, bool):
                    raise ValueError("boolean is not a numeric parameter")
                numeric_guards(v)
        elif isinstance(value, list):
            for v in value:
                numeric_guards(v)
    numeric_guards(parameters)
    p = MethodInputs.model_validate(parameters)
    if not p.period.startswith({"FY": "FY", "TTM": "TTM", "forward": "forward", "point_in_time": "20"}[p.earnings_basis]):
        raise ValueError("fiscal period label does not match earnings basis")
    if p.earnings_basis == "forward" and p.metric.information_kind != "prediction":
        raise ValueError("forward financial inputs must be classified as predictions")
    expected = {"pe": "eps", "pb": "bvps", "ev_ebitda": "ebitda", "ddm": "dividend_per_share"}[model]
    if p.metric.field != expected or quantity(p.metric) <= 0:
        raise ValueError(f"{model} requires positive {expected}")
    if model != "ev_ebitda" and p.metric.unit not in PER_SHARE_UNITS:
        raise ValueError("EPS/BVPS/dividend require per-share units; never scale by a million")
    if model == "pe" and p.metric.share_scope not in {"basic EPS", "diluted EPS"}:
        raise ValueError("PE requires explicit basic/diluted EPS basis")
    if model == "pb" and p.metric.share_scope != "ending book value per share":
        raise ValueError("PB requires ending attributable book value per share basis")
    for name in ["net_debt", "minority_interest", "shares"]:
        r = getattr(p, name)
        if r is not None and r.field != name:
            raise ValueError(f"{name} bridge slot must contain the matching financial field")
    if model != "ddm":
        if not p.multiples or not p.comparable_rule:
            raise ValueError("dated typed comparable set and selection rule required")
        for m in p.multiples:
            if (m.multiple_type, m.period, m.earnings_basis, m.currency, m.share_class) != (model, p.period, p.earnings_basis, p.currency, p.share_class):
                raise ValueError("multiple type, period, earnings basis, currency or share class mismatch")
            if m.accounting_scope != ("consolidated" if model == "ev_ebitda" else "attributable"):
                raise ValueError("multiple accounting scope mismatch")
            if model in {"pe", "pb"} and m.per_share_basis != p.metric.share_scope:
                raise ValueError("target and comparable per-share denominator basis mismatch")
        if len({m.as_of for m in p.multiples}) != 1 or len({m.security_code for m in p.multiples}) != len(p.multiples):
            raise ValueError("comparable dates must agree and securities must be unique")
    else:
        if p.cost_of_equity is None or p.terminal_growth is None or p.cost_of_equity <= p.terminal_growth:
            raise ValueError("single-stage DDM requires 0 <= growth < cost of equity < 1")
        if p.earnings_basis != "FY":
            raise ValueError("single-stage DDM requires an annual dividend base")
    for r in [p.metric, p.net_debt, p.minority_interest, p.shares]:
        if r is None:
            continue
        if (r.security_code, r.share_class, r.currency, r.period) != (p.security_code, p.share_class, p.currency, p.period):
            raise ValueError("financial identity, share class, currency or fiscal period mismatch")
        required_scope = "consolidated" if model == "ev_ebitda" else "attributable"
        if r.accounting_scope != required_scope or r.consolidated is not True:
            raise ValueError("consolidated / attributable scope mismatch")
        if r.unit in PER_SHARE_UNITS and not per_share_unit_matches(r.unit, p.currency):
            raise ValueError("per-share unit currency mismatch")
        if r.field in {"net_debt", "minority_interest", "ebitda"} and r.unit in PER_SHARE_UNITS:
            raise ValueError("enterprise bridge requires amount units")
        if r.field == "shares" and (quantity(r) <= 0 or r.share_scope != "total ordinary shares"):
            raise ValueError("positive matching total ordinary shares required")
        if r.field == "minority_interest" and quantity(r) < 0:
            raise ValueError("negative minority interest unsupported by this bridge")
    return p


def compare_market(p, our_value):
    b = p.benchmark
    if b is None:
        return {"status": "unresolved", "baseline": None, "difference": None, "statement": "缺少有来源、有日期且口径匹配的市场基准"}
    r = b.record
    if (r.security_code, r.share_class, r.currency) != (p.security_code, p.share_class, p.currency):
        raise ValueError("benchmark identity/currency/share class mismatch")
    if b.kind in {"consensus_target", "earnings_expectation"} and not b.sample_size:
        raise ValueError("consensus requires provider and sample size")
    if b.kind == "earnings_expectation":
        if r.field != p.metric.field or r.period != p.period or r.accounting_scope != p.metric.accounting_scope or b.earnings_basis != p.earnings_basis or b.own_value is None or not b.own_basis:
            raise ValueError("operating expectation comparison requires matching own forecast and basis")
        own = _number(b.own_value)
        if r.unit != p.metric.unit:
            raise ValueError("operating expectation unit mismatch")
        baseline = quantity(r)
        if r.unit not in PER_SHARE_UNITS:
            own = _convert(own, r.unit, "million")
    else:
        if r.field != "price" or not per_share_unit_matches(r.unit, p.currency) or b.earnings_basis != "point_in_time" or r.period != b.as_of:
            raise ValueError("price benchmark requires dated per-share price")
        own = None if our_value is None else _number(our_value)
        baseline = _number(r.value)
    if baseline <= 0:
        raise ValueError("benchmark must be positive")
    return {"status": "resolved" if own is not None else "unresolved", "kind": b.kind, "baseline": b.model_dump(),
            "our_value": None if own is None else _out(own), "difference": None if own is None else _out(own / baseline - 1),
            "statement": "估值或经营假设相对指定基准的差异；不是预期收益率"}


def calculate(model, parameters):
    p = validate_inputs(model, parameters)
    metric = quantity(p.metric)
    gaps, sensitivity, value_range = [], [], []
    enterprise = equity = price = None
    if model == "ddm":
        k, g = _number(p.cost_of_equity), _number(p.terminal_growth)
        price = metric * (1 + g) / (k - g)
        for dk in [Decimal("-.01"), Decimal(0), Decimal(".01")]:
            for dg in [Decimal("-.005"), Decimal(0), Decimal(".005")]:
                kk, gg = k + dk, g + dg
                cell = {"cost_of_equity": float(kk), "terminal_growth": float(gg)}
                if 0 <= gg < kk < 1:
                    cell.update(status="calculated", per_share_value=_out(metric * (1 + gg) / (kk - gg)))
                else:
                    cell.update(status="invalid", reason="growth/discount boundary")
                sensitivity.append(cell)
        value_range = [min(c["per_share_value"] for c in sensitivity if c["status"] == "calculated"), max(c["per_share_value"] for c in sensitivity if c["status"] == "calculated")]
        rule = "单阶段 DDM：D1=D0×(1+g)；k±1个百分点、g±0.5个百分点有效敏感性包络；不是置信区间"
    else:
        multiples = sorted(_number(m.value) for m in p.multiples)
        values = [metric * m for m in multiples]
        central = metric * median(multiples)
        if model == "ev_ebitda":
            enterprise = central
            if p.net_debt is None or p.minority_interest is None or p.shares is None:
                gaps.append("权益桥接缺少净债务、少数股东权益或匹配总股数；仅保留企业价值")
            else:
                debt, minority, shares = quantity(p.net_debt), quantity(p.minority_interest), quantity(p.shares)
                equity = central - debt - minority
                price = equity / shares
                values = [(v - debt - minority) / shares for v in values]
        else:
            price = central
        if price is not None:
            value_range = [_out(min(values)), _out(max(values))]
        sensitivity = [{"multiple": float(m), "enterprise_value" if model == "ev_ebitda" else "per_share_value": _out(metric * m)} for m in multiples]
        rule = "同类、同期间、同日筛选可比倍数：中位数为中心，min/max形成可比区间；不是统计置信区间"
    comparison = compare_market(p, None if price is None else _out(price))
    if comparison["status"] == "unresolved":
        gaps.append(comparison["statement"])
    return MethodOutput(model=model, method_status="enterprise_only" if price is None else "calculated", currency=p.currency,
        per_share_unit=f"{p.currency}/share", per_share_value=None if price is None else _out(price),
        enterprise_value=None if enterprise is None else _out(enterprise), equity_value=None if equity is None else _out(equity),
        value_range=value_range, range_rule=rule, sensitivity=sensitivity, market_comparison=comparison, gaps=gaps).model_dump()


def fcff_from_ebit(ebit, tax_rate, depreciation, capex, delta_nwc):
    e, t, d, c, w = map(_number, [ebit, tax_rate, depreciation, capex, delta_nwc])
    if not 0 <= t <= 1 or d < 0 or c < 0:
        raise ValueError("tax rate must be in [0,1]; depreciation and capex are positive amounts")
    return _out(e * (1 - t) + d - c - w)


def receipt(model, parameters, version, basis):
    results = calculate(model, parameters)
    return {"kind": "valuation_method", "schema_version": 1, "model": model, "inputs": parameters,
            "basis": basis, "assumptions_version": version,
            "formula": {"pe": "EPS * median(PE)", "pb": "BVPS * median(PB)", "ev_ebitda": "EV=EBITDA * median(EV/EBITDA); Equity=EV-net_debt-minority_interest; price=Equity/shares", "ddm": "D0*(1+g)/(k-g)"}[model],
            "results": results}
