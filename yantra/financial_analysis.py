"""Fixed financial definitions and investigation signals; never infer missing inputs."""
from datetime import date
from decimal import Decimal
import re

# Exact field names; attributable profit is never substituted with group net profit.
FIELD_MAPPING = {
    "营业收入": "revenue", "营业成本": "cost_of_revenue", "归属于母公司股东的净利润": "net_profit_attributable",
    "归属于母公司股东权益": "equity_attributable", "资产总计": "total_assets", "负债合计": "total_liabilities",
    "所有者权益合计": "total_equity", "流动资产合计": "current_assets", "流动负债合计": "current_liabilities",
    "应收账款": "receivables", "存货": "inventory", "商誉": "goodwill", "销售费用": "selling_expense",
    "管理费用": "administrative_expense", "财务费用": "finance_expense", "短期借款": "short_term_borrowings",
    "长期借款": "long_term_borrowings", "应付债券": "bonds_payable", "经营活动产生的现金流量净额": "operating_cash_flow",
}
FORMULAS = {
    "gross_margin": "(revenue-cost_of_revenue)/revenue",
    "attributable_net_margin": "net_profit_attributable/revenue",
    "roe": "net_profit_attributable/average(equity_attributable)",
    "receivables_turnover": "revenue/average(receivables)",
    "inventory_turnover": "cost_of_revenue/average(inventory)",
    "receivables_days": "period_days/receivables_turnover", "inventory_days": "period_days/inventory_turnover",
    "debt_ratio": "total_liabilities/total_assets", "current_ratio": "current_assets/current_liabilities",
    "quick_ratio": "(current_assets-inventory)/current_liabilities",
    "net_cash_ratio": "operating_cash_flow/net_profit_attributable", "goodwill_ratio": "goodwill/total_equity",
    "interest_bearing_debt": "short_term_borrowings+long_term_borrowings+bonds_payable",
    **{name+"_ratio": name+"/revenue" for name in ("selling_expense", "administrative_expense", "finance_expense")},
}
THRESHOLDS = {"low_net_cash": .5, "receivables_growth": .2, "inventory_growth": .2,
              "goodwill": .3, "selling_expense_change": .05, "administrative_expense_change": .05,
              "debt_ratio_increase": .1, "quarterly_profit_change": .5}
ATTRIBUTABLE = {"net_profit_attributable", "equity_attributable", "opening_equity_attributable"}


def period_key(row):
    period = row["period"]
    match = re.fullmatch(r"FY(\d{4})", period)
    if match:
        if row.get("period_basis") not in {None, "FY"}:
            return None
        days = 365
        if row.get("period_start") and row.get("period_end"):
            days = (date.fromisoformat(row["period_end"])-date.fromisoformat(row["period_start"])).days+1
            if days not in {365, 366}:
                return None
        return "FY", int(match[1]), "FY", days
    match = re.fullmatch(r"(\d{4})Q([1-4])", period)
    if match:
        basis, year, slot = "quarter", int(match[1]), "Q"+match[2]
    else:
        match = re.fullmatch(r"(\d{4})(H[12])", period)
        if match:
            basis, year, slot = "interim", int(match[1]), match[2]
        elif row.get("period_basis") == "TTM" and row.get("period_end"):
            end = date.fromisoformat(row["period_end"])
            basis, year, slot = "TTM", end.year, end.strftime("%m-%d")
        else:
            return None
    days = None
    if row.get("period_start") and row.get("period_end"):
        days = (date.fromisoformat(row["period_end"])-date.fromisoformat(row["period_start"])).days+1
        if days <= 0:
            return None
    if row.get("period_basis") not in {None, basis}:
        return None
    return basis, year, slot, days


def screening(normalized):
    groups, gaps, signals, matrix = {}, [], [], []
    for original in normalized:
        row = dict(original)
        row["field"] = FIELD_MAPPING.get(row["field"], row["field"])
        try:
            period = period_key(row)
        except ValueError:
            period = None
        if row.get("information_kind") not in {None, "fact"} or not period:
            gaps.append(f"{row['period']}/{row['field']}: unsupported or nonhistorical period")
            continue
        if not row.get("security_code") or not row.get("share_class") or not row.get("accounting_scope") or not row["consolidated"]:
            gaps.append(f"{row['period']}/{row['field']}: identity or accounting scope missing")
            continue
        expected_scope = "attributable" if row["field"].removesuffix("_cross_statement") in ATTRIBUTABLE else "consolidated"
        if row["accounting_scope"] != expected_scope:
            gaps.append(f"{row['period']}/{row['field']}: requires {expected_scope}")
            continue
        if row["normalized_unit"] != "million":
            continue
        key = (row["security_code"], row["share_class"], row["currency"], row.get("business_scope") or "group", row["period"])
        group = groups.setdefault(key, {"period": period, "fields": {}})
        if group["period"] != period:
            group["incomparable"] = True
        group["fields"].setdefault(row["field"], []).append(row)
    series = {}
    for key, group in sorted(groups.items()):
        if group.get("incomparable"):
            gaps.append(f"{key[-1]}: inconsistent period dates; calculations suspended")
            continue
        fields = {}
        for field, rows in group["fields"].items():
            if len({Decimal(str(r["normalized_value"])) for r in rows}) != 1:
                gaps.append(f"{key[-1]}/{field}: conflicting inputs; affected calculations suspended")
            else:
                fields[field] = rows[0]
        group["fields"] = fields
        basis, year, slot, days = group["period"]
        series.setdefault((*key[:4], basis, slot), {})[year] = (key, group)
    for series_key, periods in sorted(series.items()):
        for year, (key, group) in sorted(periods.items()):
            fields = group["fields"]
            previous = periods.get(year-1)
            prev = previous[1]["fields"] if previous else {}
            values, refs, unavailable, quality = {}, {}, {}, []
            suspended = set()
            if {"total_assets", "total_liabilities", "total_equity"} <= fields.keys():
                amounts = {k: Decimal(str(fields[k]["normalized_value"])) for k in ("total_assets", "total_liabilities", "total_equity")}
                if abs(amounts["total_assets"]-amounts["total_liabilities"]-amounts["total_equity"]) > Decimal("0.000001"):
                    suspended = {"total_assets", "total_liabilities", "total_equity", "equity_attributable"}
                    quality.append("balance_sheet_mismatch: asset-based and equity-based calculations suspended")
            # Optional duplicate cross-statement lines must agree before cash/profit ratios.
            for field in ("net_profit_attributable", "operating_cash_flow"):
                cross = fields.get(field+"_cross_statement")
                if cross and field in fields and cross["normalized_value"] != fields[field]["normalized_value"]:
                    suspended.add(field)
                    quality.append(field+": cross-statement mismatch")
            def number(field):
                if field in suspended:
                    raise ValueError("data quality mismatch: "+field)
                if field not in fields:
                    raise ValueError("missing "+field)
                return Decimal(str(fields[field]["normalized_value"]))
            def average(field):
                closing = number(field)
                opening_name = "opening_"+field
                if opening_name in fields:
                    opening = number(opening_name)
                    return (opening+closing)/2, [fields[opening_name], fields[field]]
                if group["period"][0] == "FY" and field in prev:
                    prior_fields = previous[1]["fields"]
                    if {"total_assets", "total_liabilities", "total_equity"} <= prior_fields.keys() and abs(prior_fields["total_assets"]["normalized_value"]-prior_fields["total_liabilities"]["normalized_value"]-prior_fields["total_equity"]["normalized_value"]) > .000001:
                        raise ValueError("opening balance data quality mismatch")
                    return (Decimal(str(prev[field]["normalized_value"]))+closing)/2, [prev[field], fields[field]]
                raise ValueError("missing comparable opening "+field)
            def metric(name, calculate):
                try:
                    result, sources = calculate()
                    values[name] = float(result)
                    refs[name] = sorted({r["source_ref"] for r in sources})
                except (ValueError, ZeroDivisionError) as exc:
                    unavailable[name] = str(exc)
            def ratio(numerator, denominator, source_fields):
                den = denominator()
                if den <= 0:
                    raise ValueError("zero or negative denominator")
                return numerator()/den, [fields[f] for f in source_fields]
            definitions = {
                "gross_margin": (lambda: number("revenue")-number("cost_of_revenue"), "revenue", ["revenue", "cost_of_revenue"]),
                "attributable_net_margin": (lambda: number("net_profit_attributable"), "revenue", ["net_profit_attributable", "revenue"]),
                "net_cash_ratio": (lambda: number("operating_cash_flow"), "net_profit_attributable", ["operating_cash_flow", "net_profit_attributable"]),
                "debt_ratio": (lambda: number("total_liabilities"), "total_assets", ["total_liabilities", "total_assets"]),
                "current_ratio": (lambda: number("current_assets"), "current_liabilities", ["current_assets", "current_liabilities"]),
                "quick_ratio": (lambda: number("current_assets")-number("inventory"), "current_liabilities", ["current_assets", "inventory", "current_liabilities"]),
                "goodwill_ratio": (lambda: number("goodwill"), "total_equity", ["goodwill", "total_equity"]),
            }
            for field in ("selling_expense", "administrative_expense", "finance_expense"):
                definitions[field+"_ratio"] = (lambda f=field: number(f), "revenue", [field, "revenue"])
            for name, (num, den, sources) in definitions.items():
                metric(name, lambda n=num, d=den, s=sources: ratio(n, lambda: number(d), s))
            for name, num, den in (("roe", "net_profit_attributable", "equity_attributable"), ("receivables_turnover", "revenue", "receivables"), ("inventory_turnover", "cost_of_revenue", "inventory")):
                def calc(n=num, d=den):
                    avg, sources = average(d)
                    if avg <= 0:
                        raise ValueError("zero or negative average balance")
                    return number(n)/avg, sources+[fields[n]]
                metric(name, calc)
            debt_fields = ["short_term_borrowings", "long_term_borrowings", "bonds_payable"]
            metric("interest_bearing_debt", lambda: (sum(number(f) for f in debt_fields), [fields[f] for f in debt_fields]))
            days = group["period"][3]
            for field in ("receivables", "inventory"):
                rate = values.get(field+"_turnover")
                if rate is not None and rate > 0 and days:
                    values[field+"_days"] = float(Decimal(days)/Decimal(str(rate)))
                    refs[field+"_days"] = refs[field+"_turnover"]
                else:
                    unavailable[field+"_days"] = "missing duration or positive turnover"
            for field in ("revenue", "receivables", "inventory", "net_profit_attributable"):
                name = field+"_growth"
                if field in fields and field in prev and field not in suspended:
                    prior = Decimal(str(prev[field]["normalized_value"]))
                    if prior > 0:
                        values[name] = float((number(field)-prior)/prior)
                        refs[name] = [prev[field]["source_ref"], fields[field]["source_ref"]]
                    else:
                        unavailable[name] = "zero or negative prior denominator"
                else:
                    unavailable[name] = "missing comparable prior period or suspended input"
            entry = {"security_code": key[0], "share_class": key[1], "currency": key[2], "business_scope": key[3], "period": key[4],
                     "period_basis": group["period"][0], "period_days": days, "metrics": values, "source_refs": refs,
                     "unavailable": unavailable, "data_quality": quality, "amount_unit": "million", "ratio_unit": "ratio"}
            matrix.append(entry)
    matrix.sort(key=lambda r: (r["security_code"], r["share_class"], r["currency"], r["business_scope"], r["period_basis"], tuple(int(x) for x in re.findall(r"\d+", r["period"]))))
    previous_entries = {}
    for entry in matrix:
        identity = tuple(entry[k] for k in ("security_code", "share_class", "currency", "business_scope", "period_basis"))
        prior = previous_entries.get(identity)
        previous_entries[identity] = entry
        values, refs = entry["metrics"], entry["source_refs"]
        def signal(name, observed, source_refs):
            signals.append({"id": "/".join((*identity[:4], entry["period"], name)), "rule": name, "threshold": THRESHOLDS[name],
                            "observed": [float(v) for v in observed], "period": entry["period"], "business_scope": entry["business_scope"], "source_refs": sorted(set(source_refs)),
                            "meaning": "investigation_signal", "business_localization": "无法定位到具体业务" if entry["business_scope"] == "group" else entry["business_scope"]})
        def d(value):
            return Decimal(str(value))
        if "goodwill_ratio" in values and d(values["goodwill_ratio"]) > d(THRESHOLDS["goodwill"]):
            signal("goodwill", [values["goodwill_ratio"]], refs["goodwill_ratio"])
        if entry["period_basis"] == "quarter" and abs(d(values.get("net_profit_attributable_growth", 0))) > d(THRESHOLDS["quarterly_profit_change"]):
            signal("quarterly_profit_change", [values["net_profit_attributable_growth"]], refs["net_profit_attributable_growth"])
        if not prior:
            continue
        def ordinal(period, basis):
            numbers = [int(x) for x in re.findall(r"\d+", period)]
            if basis == "FY":
                return numbers[0]
            if basis in {"quarter", "interim"}:
                return numbers[0]*(4 if basis == "quarter" else 2)+numbers[1]
            return None
        before, current = ordinal(prior["period"], entry["period_basis"]), ordinal(entry["period"], entry["period_basis"])
        if before is None or current != before+1:
            continue  # Unspecified TTM frequency and missing periods cannot establish consecutive observations.
        prior_values = prior["metrics"]
        for field in ("selling_expense", "administrative_expense"):
            name = field+"_ratio"
            if name in values and name in prior_values:
                change = d(values[name])-d(prior_values[name])
                if abs(change) > d(THRESHOLDS[field+"_change"]):
                    signal(field+"_change", [change], refs[name]+prior["source_refs"][name])
        if entry["period_basis"] == "FY" and "debt_ratio" in values and "debt_ratio" in prior_values:
            change = d(values["debt_ratio"])-d(prior_values["debt_ratio"])
            if change > d(THRESHOLDS["debt_ratio_increase"]):
                signal("debt_ratio_increase", [change], refs["debt_ratio"]+prior["source_refs"]["debt_ratio"])
        if "net_cash_ratio" in values and "net_cash_ratio" in prior_values and max(d(values["net_cash_ratio"]), d(prior_values["net_cash_ratio"])) < d(THRESHOLDS["low_net_cash"]):
            signal("low_net_cash", [prior_values["net_cash_ratio"], values["net_cash_ratio"]], refs["net_cash_ratio"]+prior["source_refs"]["net_cash_ratio"])
        for field in ("receivables", "inventory"):
            name = field+"_growth"
            if all(k in v for v in (values, prior_values) for k in (name, "revenue_growth")):
                differences = [d(prior_values[name])-d(prior_values["revenue_growth"]), d(values[name])-d(values["revenue_growth"])]
                if min(differences) > d(THRESHOLDS[field+"_growth"]):
                    signal(field+"_growth", differences, refs[name]+refs["revenue_growth"]+prior["source_refs"][name]+prior["source_refs"]["revenue_growth"])
    return {"policy_version": 1, "formulas": FORMULAS, "thresholds": THRESHOLDS, "matrix": matrix, "signals": signals,
            "gaps": sorted(set(gaps)), "qualitative_only": ["大额减值", "费用变化是否有业务解释"], "monitoring": "checklist_only"}
