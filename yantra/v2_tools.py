"""Raw-source extraction receipts and conservative deterministic metrics."""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal
from pathlib import Path

from .materials import RawMaterialAdapter, cutoff_time, numeric, research_chunks, source_number_present
from .schemas import CheckArguments, ChildArguments, CollaborationArguments, Extraction, FetchArguments, MetricsArguments, ReadArguments, SearchArguments, ToolResponse, ValuationArguments
from .tools import ToolRegistry, _convert, _hash, _out, _number


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [canonical(x) for x in value]
    return value


class RawToolRegistry(ToolRegistry):
    names = ToolRegistry.names | {"search_public", "fetch_source", "compare_expectations"}

    def __init__(self, directory):
        super().__init__(directory, RawMaterialAdapter(directory))
        self.extraction_path = self.adapter.materials_dir / "extraction.json"
        self.extension_contracts = {}

    def tool_contracts(self):
        contracts = super().tool_contracts()
        contracts.update({"search_public": {"description": "Discover public sources and read original responses. Search snippets are not evidence.", "arguments_schema": SearchArguments.model_json_schema()},
                          "fetch_source": {"description": "Fetch and parse an original public HTTP(S) source, retaining bytes and anchors.", "arguments_schema": FetchArguments.model_json_schema()}})
        contracts.update({"spawn_subtask": {"description": "Create a third-layer role within the parent task's granted tools and source references. No fourth layer.", "arguments_schema": ChildArguments.model_json_schema()},
                          "collaborate": {"description": "Read or message only explicitly shared tasks. request_transfer creates an approval request, not a task.", "arguments_schema": CollaborationArguments.model_json_schema()}})
        contracts["read_materials"]["description"] = "Read raw-source text and tables with original page/cell/slide/image anchors and hashes."
        from .schemas import MethodInputs
        contracts["valuation"]["description"] = "Execute authorized model DCF/PE/PB/EV-EBITDA/single-stage DDM. Historical DCF parameters are unchanged. New raw DCF also requires minority_interest (million), reviewed EBIT FCFF bridge and structured source_refs in basis for base_fcf/net_debt/shares/minority_interest. All bridge records must carry security_code, share_class and accounting_scope. New non-DCF parameters schema: " + json.dumps(MethodInputs.model_json_schema(), ensure_ascii=False)
        contracts.update(self.extension_contracts)
        from .schemas import ExpectationArguments
        contracts["compare_expectations"] = {"description": "Compare sourced operating forecasts with dated consensus, a limited research sample, or no external baseline. No price-implied assumptions or pricing claims.", "arguments_schema": ExpectationArguments.model_json_schema()}
        return contracts

    def _compare_expectations(self, args):
        from .schemas import ExpectationArguments
        from .investment_logic import compare_expectations
        value = ExpectationArguments.model_validate(args)
        anchors = self.anchors()
        original = json.loads(self.extraction_path.read_text()) if self.extraction_path.exists() else {"financials": []}
        context = getattr(self, "research_context", {})
        for forecast in value.own_forecasts:
            from .valuation import PER_SHARE_UNITS, per_share_unit_matches
            if forecast.unit in PER_SHARE_UNITS and not per_share_unit_matches(forecast.unit, forecast.currency):
                raise ValueError("own forecast per-share unit/currency mismatch")
            if (forecast.security_code, forecast.share_class, forecast.currency) != (context.get("security_code"), context.get("share_class"), context.get("trading_currency")):
                raise ValueError("own forecast identity does not match research")
            if any(ref not in anchors for ref in forecast.evidence_refs):
                raise ValueError("own forecast basis requires original anchors")
        for forecast in value.external_forecasts:
            row = forecast.record
            if row.unit in PER_SHARE_UNITS and not per_share_unit_matches(row.unit, row.currency):
                raise ValueError("external forecast per-share unit/currency mismatch")
            if canonical(row.model_dump()) not in [canonical(r) for r in original["financials"]]:
                raise ValueError("external forecast is not an accepted original extraction")
            if row.information_kind != "prediction" or row.source_ref not in anchors or anchors[row.source_ref][1]["source_hash"] != row.source_hash:
                raise ValueError("external forecast must be a sourced prediction")
            text = anchors[row.source_ref][0]["text"]
            if not source_number_present(row.raw_value, text) or numeric(row.raw_value) != row.value or forecast.provider not in text or forecast.as_of not in text:
                raise ValueError("external forecast number/provider/date does not resolve")
            if not row.disclosure_date or cutoff_time(forecast.as_of) > cutoff_time(context["cutoff"]) or row.disclosure_date != forecast.as_of:
                raise ValueError("external forecast date mismatch or after cutoff")
            if forecast.kind == "consensus" and (not re.search(r"一致|consensus", text, re.I) or not source_number_present(str(forecast.sample_size), text)):
                raise ValueError("consensus requires original provider/sample evidence")
        results = compare_expectations(value.model_dump())
        payload = {"kind": "expectation_comparison", "schema_version": 1, "inputs": value.model_dump(),
                   "formula": {"absolute_difference": "own-external", "relative_difference": "own/external-1; positive denominator required"}, "results": results}
        ref = self._record_calculation(payload)
        return ToolResponse(ok=True, data={**results, "calculation": payload, "calculation_ref": ref}, calculation_refs=[ref],
                            evidence_refs=sorted({r for f in value.own_forecasts for r in f.evidence_refs} | {f.record.source_ref for f in value.external_forecasts}))

    def snapshot(self):
        index = self.load_manifest()
        documents = self.adapter.read_materials(ReadArguments(company=index["company"]))["documents"]
        return {"version": index["version"], "fictional": False, "manifest_hash": _hash(index), "manifest": index,
                "materials": {x["id"]: {"source_hash": x["source_hash"], "file_path": x["file_path"], "text": x["text"], "chunks": x["chunks"], "url": x["url"]} for x in documents}}

    def _search_public(self, args):
        value = SearchArguments.model_validate(args)
        receipt = self.adapter.search(**value.model_dump())
        refs = [chunk["ref"] for doc in self.adapter.read_materials(ReadArguments(company=self.load_manifest()["company"]))["documents"]
                if doc["id"] in {r.get("source_id") for r in receipt["results"]} for chunk in doc["chunks"]]
        return ToolResponse(ok=True, data=receipt, evidence_refs=refs)

    def _fetch_source(self, args):
        entry = self.adapter.fetch(FetchArguments.model_validate(args).url)
        chunks = [{**x, "text": x["text"][:9000]} for x in research_chunks(entry)]
        data = {k: v for k, v in entry.items() if k != "chunks"} | {"chunks": chunks}
        return ToolResponse(ok=True, data=data, evidence_refs=[x["ref"] for x in chunks])

    def anchors(self):
        return {chunk["ref"]: (chunk, doc) for doc in self.adapter.read_materials(ReadArguments(company=self.load_manifest()["company"]))["documents"] for chunk in doc["chunks"]}

    def validate_method_sources(self, model, parameters, research):
        from .valuation import validate_inputs, quantity, compare_market, per_share_unit_matches
        p = validate_inputs(model, parameters)
        if p.security_code != research["security_code"] or p.currency != research["trading_currency"]:
            raise ValueError("valuation security/currency does not match research")
        anchors = self.anchors()
        accepted = json.loads(self.extraction_path.read_text()) if self.extraction_path.exists() else {"financials": []}
        records = accepted["financials"]

        def source(row, date, raw, quote=None):
            pair = anchors.get(row.source_ref)
            if not pair or pair[1]["source_hash"] != row.source_hash:
                raise ValueError("source anchor/hash does not resolve in this run")
            if not source_number_present(raw, pair[0]["text"]) or (quote and (quote not in pair[0]["text"] or not source_number_present(raw, quote))):
                raise ValueError("source numeric token/quote missing")
            if not date or cutoff_time(date) > cutoff_time(research["cutoff"]):
                raise ValueError("source is undated or after cutoff")
            if pair[1].get("date") and cutoff_time(pair[1]["date"]) > cutoff_time(research["cutoff"]):
                raise ValueError("original source publication after cutoff")

        def financial(row, accepted_required=True):
            source(row, row.disclosure_date, row.raw_value)
            if numeric(row.raw_value) != row.value:
                raise ValueError("financial input disagrees with raw number")
            if accepted_required and canonical(row.model_dump()) not in [canonical(r) for r in records]:
                raise ValueError("financial input is not an accepted original extraction")

        def observation_date(row, as_of):
            doc = anchors[row.source_ref][1]
            if doc.get("date") != as_of and as_of not in doc["text"]:
                raise ValueError("observation date does not resolve to the original source")

        for row in [p.metric, p.net_debt, p.minority_interest, p.shares]:
            if row:
                financial(row)
        for m in p.multiples:
            source(m, m.as_of, m.raw_value, m.quote)
            observation_date(m, m.as_of)
            if m.price_record or m.fundamental_record:
                if not (m.price_record and m.fundamental_record) or model not in {"pe", "pb"}:
                    raise ValueError("derived comparable requires price and EPS/BVPS records")
                price, fundamental = m.price_record, m.fundamental_record
                financial(price); financial(fundamental)
                if (price.security_code, price.share_class, price.currency, price.field, price.period) != (m.security_code, m.share_class, m.currency, "price", m.as_of) or not per_share_unit_matches(price.unit, m.currency):
                    raise ValueError("comparable price identity/date/unit mismatch")
                if (fundamental.security_code, fundamental.share_class, fundamental.currency, fundamental.period, fundamental.field, fundamental.accounting_scope) != (m.security_code, m.share_class, m.currency, m.period, "eps" if model == "pe" else "bvps", "attributable") or not fundamental.consolidated or not per_share_unit_matches(fundamental.unit, m.currency) or quantity(fundamental) <= 0:
                    raise ValueError("comparable fundamental identity/period/scope mismatch")
                if not math.isclose(m.value, float(quantity(price) / quantity(fundamental)), rel_tol=1e-10):
                    raise ValueError("derived multiple disagrees with independently sourced price/fundamental")
                if fundamental.share_scope != m.per_share_basis:
                    raise ValueError("comparable fundamental per-share basis mismatch")
            else:
                if _number(numeric(m.raw_value)) != _number(m.value):
                    raise ValueError("multiple differs from original numeric token")
        if p.benchmark:
            b = p.benchmark
            financial(b.record)
            source(b.record, b.as_of, b.record.raw_value)
            observation_date(b.record, b.as_of)
            if b.record.disclosure_date != b.as_of:
                raise ValueError("benchmark observation and source dates disagree")
            if b.kind in {"consensus_target", "earnings_expectation"}:
                text = anchors[b.record.source_ref][0]["text"]
                if not re.search(r"一致|consensus", text, re.I) or b.provider not in text or str(b.sample_size) not in text:
                    raise ValueError("consensus benchmark lacks original provider/sample evidence")
        compare_market(p, None)
        return p

    def _valuation(self, args):
        from .valuation import receipt
        a = ValuationArguments.model_validate(args)
        if a.model == "dcf":
            if "minority_interest" in a.parameters:
                legacy = {k: v for k, v in a.parameters.items() if k != "minority_interest"}
                legacy["net_debt"] = _out(_number(legacy["net_debt"]) + _number(a.parameters["minority_interest"]))
                existing_refs = set(self.calculations)
                result = super()._valuation({"parameters": legacy, "assumptions_version": a.assumptions_version})
                payload = result.data["calculation"]
                if result.calculation_refs[0] not in existing_refs:
                    self.calculations.pop(result.calculation_refs[0], None)
                payload.update(kind="dcf_bridge", schema_version=1, model="dcf", inputs=a.parameters, basis=a.basis)
                payload["scope"] = "Source-checked FCFF bridge; equity deducts explicit net debt and minority interest. Conditional stable positive cash flow model."
                payload["formula"]["equity_value"] = "EV - net_debt - minority_interest"
                ref = self._record_calculation(payload)
                return ToolResponse(ok=True, data={**result.data, "model": "dcf", "inputs": a.parameters, "basis": a.basis,
                    "formula": payload["formula"], "calculation": payload, "calculation_ref": ref}, calculation_refs=[ref],
                    evidence_refs=sorted({r for b in a.basis.values() if isinstance(b, dict) for r in b.get("source_refs", [])}))
            return super()._valuation(args)
        context = getattr(self, "research_context", None)
        if context is None:
            raise ValueError("current research identity/cutoff required")
        p = self.validate_method_sources(a.model, a.parameters, context)
        payload = receipt(a.model, a.parameters, a.assumptions_version, a.basis)
        ref = self._record_calculation(payload)
        rows = [p.metric, p.net_debt, p.minority_interest, p.shares]
        rows += [m for m in p.multiples]
        rows += [r for m in p.multiples for r in [m.price_record, m.fundamental_record] if r]
        if p.benchmark:
            rows.append(p.benchmark.record)
        return ToolResponse(ok=True, data={**payload["results"], "inputs": a.parameters, "formula": payload["formula"],
            "basis": a.basis, "assumptions_version": a.assumptions_version, "calculation": payload, "calculation_ref": ref},
            evidence_refs=sorted({r.source_ref for r in rows if r}), calculation_refs=[ref])

    def validate_dcf_sources(self, assumptions, research):
        from .schemas import FinancialRecord
        anchors = self.anchors()
        records = json.loads(self.extraction_path.read_text())["financials"]
        metrics = self._calculate_metrics({"records": records}).data
        if metrics.get("fcff_bridge", {}).get("method") != "ebit":
            raise ValueError("new DCF receipts require the reviewed EBIT bridge")
        refs = {"net_debt": {"debt", "cash"}, "shares": {"shares"}, "minority_interest": {"minority_interest"},
                "base_fcf": {"ebit", "tax_rate", "depreciation", "capex", "delta_nwc"} if metrics.get("fcff_bridge", {}).get("method") == "ebit" else {"operating_cash_flow", "capex"}}
        for parameter, fields in refs.items():
            basis = assumptions["basis"].get(parameter)
            if not isinstance(basis, dict) or not basis.get("source_refs"):
                raise ValueError(f"DCF {parameter} requires structured source basis")
            selected = [FinancialRecord.model_validate(r) for r in records if r["field"] in fields and r["period"] == metrics["latest_period"]]
            if {r.field for r in selected} != fields or any(r.source_ref not in basis["source_refs"] for r in selected):
                raise ValueError("DCF bridge basis lacks all current fiscal source references")
            for r in selected:
                pair = anchors.get(r.source_ref)
                if not pair or pair[1]["source_hash"] != r.source_hash or not source_number_present(r.raw_value, pair[0]["text"]) or numeric(r.raw_value) != r.value:
                    raise ValueError("DCF original numeric/hash basis invalid")
                if r.security_code != research["security_code"] or r.currency != research["trading_currency"] or not r.consolidated or r.accounting_scope != "consolidated" or r.business_scope not in {None,"group"}:
                    raise ValueError("DCF bridge identity/currency/scope mismatch")
                if not r.share_class or r.share_class != metrics.get("share_class"):
                    raise ValueError("DCF bridge share class is missing or does not match total shares")
                if not r.disclosure_date or cutoff_time(r.disclosure_date) > cutoff_time(research["cutoff"]):
                    raise ValueError("DCF bridge date exceeds cutoff")
        if metrics.get("net_debt") != assumptions["parameters"]["net_debt"] or metrics.get("shares") != assumptions["parameters"]["shares"] or metrics.get("fcff") != assumptions["parameters"]["base_fcf"]:
            raise ValueError("DCF inputs differ from source bridge")

    def _read_materials(self, args):
        parsed = ReadArguments.model_validate(args)
        data = self.adapter.read_materials(parsed)
        refs = []
        for doc in data["documents"]:
            chunks = research_chunks(doc, parsed.max_chunks, parsed.query, parsed.anchor_refs)
            doc["lines"] = [{**c, "text": c["text"][:9000]} for c in chunks]
            doc["text"] = "\n".join(c["text"] for c in doc["lines"])
            doc.pop("chunks", None)
            refs.extend(c["ref"] for c in chunks)
        data["documents"] = data["documents"][:12]
        return ToolResponse(ok=True, data=data, evidence_refs=refs)

    def accept_extraction(self, extracted, cutoff):
        value = Extraction.model_validate(extracted)
        anchors = self.anchors()
        records, evidence, gaps = [], [], list(value.gaps)
        for row in value.financials:
            pair = anchors.get(row.source_ref)
            if not pair or row.source_hash != pair[1]["source_hash"]:
                gaps.append(f"拒绝无原始出处财务字段：{row.field}")
                continue
            text = pair[0]["text"]
            if any(a.get("evidence_kind") == "discovery" for a in pair[1].get("acquisitions", [])):
                gaps.append(f"搜索摘要不能作为原始财务依据：{row.field}")
                continue
            if any(a.get("original_disclosure_required") or a.get("adapter_id") == "yahoo" or a.get("information_provider") == "yahoo" for a in pair[1].get("acquisitions", [])):
                original = anchors.get(row.disclosure_source_ref)
                if not original or not row.disclosure_quote or row.disclosure_quote not in original[0]["text"] or not source_number_present(row.raw_value, original[0]["text"]) or any(a.get("adapter_id") == "yahoo" for a in original[1].get("acquisitions", [])):
                    gaps.append(f"Yahoo财务聚合值缺少原始披露定位：{row.field}")
                    continue
            if not row.disclosure_date and pair[1].get("date"):
                row.disclosure_date = pair[1]["date"]
            if not source_number_present(row.raw_value, text):
                gaps.append(f"拒绝无原文数值的字段：{row.field}")
                continue
            try:
                if numeric(row.raw_value) != row.value:
                    raw_number = numeric(row.raw_value)
                    base_unit = "shares" if row.field == "shares" else "unit"
                    converted = float(_convert(raw_number, row.unit, base_unit))
                    if not math.isclose(row.value, converted, rel_tol=1e-12, abs_tol=1e-6):
                        raise ValueError("source value disagrees")
                    row.model_value = row.value
                    row.value = raw_number
                    row.normalization_note = "模型已将原值换算为基础单位；程序恢复原始单位数值，避免重复乘单位因子"
                if not row.disclosure_date or cutoff_time(row.disclosure_date) > cutoff_time(cutoff):
                    raise ValueError("undated or future disclosure")
                if pair[1].get("date") and cutoff_time(pair[1]["date"]) > cutoff_time(cutoff):
                    raise ValueError("source became public after cutoff")
                if not re.search(r"\d{4}", row.period):
                    raise ValueError("period is not a fiscal period")
                from .valuation import PER_SHARE_UNITS
                if row.unit not in PER_SHARE_UNITS and row.unit != "ratio":
                    _convert(row.value, row.unit, "million shares" if row.field == "shares" else "million")
            except ValueError:
                gaps.append(f"字段日期、期间、单位或数值未通过校验：{row.field} at {row.source_ref}")
                continue
            records.append(row.model_dump())
        for row in value.evidence:
            pair = anchors.get(row.source_ref)
            if not pair or row.source_hash != pair[1]["source_hash"] or row.quote not in pair[0]["text"]:
                gaps.append("拒绝无法定位的研究证据")
                continue
            if any(a.get("evidence_kind") == "discovery" for a in pair[1].get("acquisitions", [])):
                gaps.append("搜索摘要仅作线索，不能成为原始研究事实")
                continue
            if not row.disclosure_date and pair[1].get("date"):
                row.disclosure_date = pair[1]["date"]
            if not row.disclosure_date or cutoff_time(row.disclosure_date) > cutoff_time(cutoff):
                gaps.append("未注明可获得日期或超出截止时点的研究证据")
                continue
            if pair[1].get("date") and cutoff_time(pair[1]["date"]) > cutoff_time(cutoff):
                gaps.append("原始证据披露晚于截止时点")
                continue
            if row.kind == "management_statement" and not row.speaker:
                gaps.append("管理层陈述缺少主体")
                continue
            evidence.append(row.model_dump())
        # Preserve competing values and source identities; calculations never silently pick one.
        groups = {}
        for row in records:
            key = (row["field"], row["period"], row["currency"], row["consolidated"], row.get("security_code"), row.get("share_class"), row.get("accounting_scope"),row.get("business_scope"))
            groups.setdefault(key, []).append(row)
        conflicts = [list(rows) for rows in groups.values() if len({(x["value"], x["unit"], x["restatement"]) for x in rows}) > 1]
        resolutions = []
        for rows in conflicts:
            normalized = [Decimal(str(r["value"])) if r["unit"] == "ratio" or r["unit"].endswith("/share") or r["unit"] == "元/股"
                          else _convert(r["value"], r["unit"], "million shares" if r["field"] == "shares" else "million") for r in rows]
            if len(set(normalized)) == 1:
                resolutions.append({"conflict_hash": _hash(rows), "selected_source_ref": rows[0]["source_ref"],
                    "explanation": "原始单位换算后数值相同，采用匹配口径并保留两条原始记录。",
                    "evidence_refs": sorted({r["source_ref"] for r in rows})})
                continue
            newest = max(rows, key=lambda r: cutoff_time(r["disclosure_date"]))
            source = anchors[newest["source_ref"]]
            text = source[0]["text"]
            dated = newest["disclosure_date"] == source[1].get("date") or newest["disclosure_date"] in text
            if dated and newest.get("restatement") and newest["restatement"] in text and re.search(r"重述|修订|更正|restat|corrected", text, re.I) and all(cutoff_time(newest["disclosure_date"]) > cutoff_time(r["disclosure_date"]) for r in rows if r is not newest):
                resolutions.append({"conflict_hash": _hash(rows), "selected_source_ref": newest["source_ref"],
                    "explanation": "采用有原文修订说明及更晚披露日期的重述版本；此前候选保存在冲突记录中。",
                    "evidence_refs": sorted({r["source_ref"] for r in rows})})
                records = [r for r in records if r not in rows or r == newest]
        if len(resolutions) != len(conflicts):
            gaps.append("存在未调节的财务口径或重述冲突，保留候选值")
        accepted = {"financials": records, "evidence": evidence, "gaps": list(dict.fromkeys(gaps)), "conflicts": conflicts,
                    "conflict_resolutions": resolutions,
                    "conflict_refs": [{"conflict_hash": _hash(rows), "field": rows[0]["field"], "period": rows[0]["period"],
                                       "evidence_refs": sorted({r["source_ref"] for r in rows})} for rows in conflicts]}
        self.extraction_path.write_text(json.dumps(accepted, ensure_ascii=False, indent=2))
        return accepted

    def _extract_financials(self, args):
        ReadArguments.model_validate(args)
        if not self.extraction_path.exists():
            raise FileNotFoundError("budgeted model extraction has not run")
        data = json.loads(self.extraction_path.read_text())
        refs = sorted({x["source_ref"] for x in data["financials"] + data["evidence"]})
        return ToolResponse(ok=True, data=data, evidence_refs=refs)

    def _calculate_metrics(self, args, *, verify_sources=True, screening_version=None):
        records = MetricsArguments.model_validate(args).records
        if verify_sources and not self.extraction_path.exists():
            raise ValueError("metrics require an original extraction receipt")
        accepted = json.loads(self.extraction_path.read_text())["financials"] if self.extraction_path.exists() else []
        if verify_sources and any(canonical(r.model_dump()) not in [canonical(x) for x in accepted] for r in records):
            raise ValueError("metrics require accepted original records")
        groups, gaps = {}, []
        normalized = []
        for row in records:
            from .valuation import PER_SHARE_UNITS
            target = row.unit if row.unit in PER_SHARE_UNITS or row.unit == "ratio" else "million shares" if row.field == "shares" else "million"
            quantity = Decimal(str(row.value)) if target == row.unit else _convert(row.value, row.unit, target)
            item = {**row.model_dump(), "normalized_value": _out(quantity), "normalized_unit": target}
            normalized.append(item)
            key = (row.period, row.currency, row.consolidated, row.security_code, row.accounting_scope) if row.accounting_scope else (row.period, row.currency, row.consolidated)
            if row.business_scope:
                key=(*key,row.business_scope)
            groups.setdefault(key, {}).setdefault(row.field, []).append((quantity, row))
        results = {"periods": {}, "fcf": {}, "gaps": gaps}
        for group, rows in groups.items():
            period, currency, scope = group[:3]
            unique = {}
            for field, values in rows.items():
                if len({x[0] for x in values}) == 1:
                    unique[field] = values[0]
                else:
                    gaps.append(f"未解决冲突：{period}/{field}")
            metrics = {"currency": currency, "consolidated": scope, "unit": "million"}
            if len(group)>5:
                metrics["business_scope"]=group[5]
            for name, numerator in (("operating_margin", "operating_profit"), ("net_margin", "net_profit")):
                if "revenue" in unique and unique["revenue"][0] != 0 and (not getattr(self, "screening_version", 0) or unique["revenue"][0] > 0) and numerator in unique:
                    metrics[name] = _out(unique[numerator][0] / unique["revenue"][0])
            if {"debt", "cash"} <= unique.keys():
                metrics["net_debt"] = _out(unique["debt"][0] - unique["cash"][0])
            if "minority_interest" in unique:
                metrics["minority_interest"] = _out(unique["minority_interest"][0])
            if {"ebit", "tax_rate", "depreciation", "capex", "delta_nwc"} <= unique.keys():
                from .valuation import fcff_from_ebit
                bridge = [unique[k][1] for k in ("ebit", "tax_rate", "depreciation", "capex", "delta_nwc")]
                if scope and all(r.accounting_scope == "consolidated" and r.security_code == bridge[0].security_code and r.security_code and r.business_scope in {None,"group"} for r in bridge) and unique["tax_rate"][1].unit == "ratio":
                    metrics["fcff"] = fcff_from_ebit(*(unique[k][0] for k in ("ebit", "tax_rate", "depreciation", "capex", "delta_nwc")))
                    metrics["fcff_bridge"] = {"method": "ebit", "formula": "EBIT*(1-tax_rate)+D&A-Capex-delta_NWC", "source_refs": [r.source_ref for r in bridge]}
                    results["fcf"][period] = metrics["fcff"]
                else:
                    gaps.append(f"{period} EBIT桥接身份、合并口径或税率单位未核对")
            if {"operating_cash_flow", "capex"} <= unique.keys():
                proxy = _out(unique["operating_cash_flow"][0] - abs(unique["capex"][0]))
                metrics["ocf_minus_capex"] = proxy
                if unique["operating_cash_flow"][1].cash_flow_basis == "unlevered" and unique["operating_cash_flow"][1].business_scope in {None,"group"}:
                    if "fcff" not in metrics:
                        metrics["fcff"] = proxy
                        results["fcf"][period] = proxy
                else:
                    gaps.append(f"{period} OCF−资本开支尚未调节融资利息和税盾，不视为FCFF")
            if "shares" in unique:
                row = unique["shares"][1]
                if row.share_scope and row.security_code:
                    metrics.update(shares=_out(unique["shares"][0]), security_code=row.security_code, share_scope=row.share_scope)
                    if row.share_class:
                        metrics["share_class"] = row.share_class
            suffix = f"/{group[3]}/{group[4]}" if len(group) >= 5 else ""
            if len(group)>5:
                suffix+="/business:"+str(group[5])
            results["periods"][f"{period}/{currency}/{'consolidated' if scope else 'parent'}{suffix}"] = metrics
        # ISO-like fiscal dates sort numerically; annual/interim are kept separate.
        ordered = sorted(results["periods"], key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.split("/")[0])))
        context = getattr(self, "research_context", None)
        if context and any(len(k.split("/")) == 5 for k in ordered):
            ordered = [k for k in ordered if len(k.split("/")) == 5 and k.split("/")[3] == context["security_code"] and k.split("/")[4] == "consolidated"]
        if ordered:
            results.update(latest_period=ordered[-1].split("/")[0], **results["periods"][ordered[-1]])
        screening_version = screening_version if screening_version is not None else getattr(self, "screening_version", 0)
        if screening_version:
            from .financial_analysis import screening
            results["financial_screening"] = screening(normalized)
        payload = {"kind": "raw_financial_metrics", "inputs": normalized,
                   "formula": {"net_debt": "debt - cash", "margin": "profit / revenue", "ocf_minus_capex": "OCF - abs(capex cash outflow); not FCFF without reconciliation", "units": "value * original unit factor / million"}, "results": results}
        ref = self._record_calculation(payload)
        return ToolResponse(ok=True, data={**results, "metrics": results, "calculation": payload, "calculation_ref": ref},
                            evidence_refs=[r.source_ref for r in records], calculation_refs=[ref])

    def _check_consistency(self, args):
        value = CheckArguments.model_validate(args)
        entries = [{**v, "target": k} for k, v in value.results.items()] if isinstance(value.results, dict) else list(value.results)
        for entry in list(entries):
            auxiliary_models = entry.get("data", {}).get("auxiliary_models", {})
            for model, data in (auxiliary_models.items() if isinstance(auxiliary_models, dict) else []):
                entries.append({"target": "valuation", "data": data, "calculation_refs": [data["calculation_ref"]], "evidence_refs": []})
        anchors = self.anchors()
        issues, refs, calcs = [], set(), set()
        original = json.loads(self.extraction_path.read_text()) if self.extraction_path.exists() else {"financials": [], "evidence": []}
        used_refs = {ref for entry in entries if entry.get("target") == "valuation" for ref in entry.get("evidence_refs", [])}
        def source_refs(item):
            if isinstance(item, dict):
                for key, content in item.items():
                    if key == "source_ref" and isinstance(content, str):
                        used_refs.add(content)
                    elif key in {"source_refs", "evidence_refs"} and isinstance(content, list):
                        used_refs.update(content)
                    else:
                        source_refs(content)
            elif isinstance(item, list):
                for content in item:
                    source_refs(content)
        source_refs(value.assumptions.model_dump())
        for entry in entries:
            if entry.get("target") == "thesis":
                source_refs(entry.get("data", {}))
        groups = {_hash(rows): rows for rows in original.get("conflicts", [])}
        assessments = []
        for assessment in value.conflict_assessments:
            group = groups.get(assessment.conflict_hash)
            group_refs = {row["source_ref"] for row in group} if group else set()
            if group_refs and not group_refs.intersection(used_refs) and group_refs <= set(assessment.evidence_refs) and all(ref in anchors for ref in assessment.evidence_refs):
                assessments.append(assessment.model_dump())

        def issue(target, message, severity="major"):
            issues.append({"id": f"issue-{len(issues)+1}", "target": target, "message": message, "severity": severity, "requirement": "repair_original_evidence"})

        for entry in entries:
            data = entry.get("data", entry)
            if getattr(self, "screening_version", 0) and entry.get("target") == "finance" and data.get("metrics"):
                metrics = data["metrics"]
                metric_receipt = self.calculations.get(metrics.get("calculation_ref"))
                if not metric_receipt or canonical(metrics.get("calculation")) != canonical(metric_receipt) or any(canonical(metrics.get(k)) != canonical(v) for k, v in metric_receipt.get("results", {}).items()):
                    issue("finance", "财务指标或异常信号与凭证不符", "critical")
            for key in ("businesses", "risks", "disconfirming", "tracking"):
                for row in data.get(key, []):
                    if not isinstance(row, dict):
                        continue  # Historical textual lists retain their original contract.
                    citations = row.get("evidence_refs", [])
                    requires_source = key == "businesses" or row.get("suggested_level", "unknown") != "unknown" or row.get("status", "unknown") != "unknown" or row.get("current_value") is not None
                    if any(r not in anchors for r in citations) or (requires_source and not citations):
                        issue("thesis", "业务、风险或跟踪当前状态缺少原始来源", "critical")
            for ref in entry.get("evidence_refs", []):
                if ref not in anchors:
                    issue("finance", f"引用不能解析：{ref}", "critical")
                else:
                    refs.add(ref)
            for row in data.get("financials", []):
                if canonical(row) not in [canonical(r) for r in original["financials"]]:
                    issue("finance", "财务记录不等于已校验原文提取", "critical")
            for row in data.get("claims", []):
                if row.get("kind", row.get("type")) not in {"fact", "management_statement", "assumption", "prediction", "judgment"}:
                    issue("thesis", "论点必须注明实际信息类型")
                if row.get("kind", row.get("type")) in {"fact", "management_statement"} and (not row.get("evidence_refs") or any(r not in anchors for r in row["evidence_refs"])):
                    issue("thesis", "事实必须引用原始材料")
            if data.get("market_comparison"):
                thesis = next((e.get("data", e).get("expectations", {}) for e in (value.results.values() if isinstance(value.results, dict) else value.results) if "expectations" in e.get("data", e)), {})
                baseline = thesis.get("baseline")
                if baseline and baseline != data["market_comparison"].get("baseline"):
                    issue("thesis", "论点与计算工具使用不同市场基准", "critical")
            for ref in entry.get("calculation_refs", []):
                receipt = self.calculations.get(ref)
                if not receipt or ref != f"calc:{_hash(receipt)[:24]}":
                    issue("valuation", "缺少或损坏计算凭证", "critical")
                    continue
                if receipt["kind"] == "unit_conversion":
                    expected = self._convert_units(receipt["inputs"]).data
                    expected = {k: v for k, v in expected.items() if k != "calculation_ref"}
                elif receipt["kind"] == "raw_financial_metrics":
                    rows = [{k: v for k, v in r.items() if k not in {"normalized_value", "normalized_unit"}} for r in receipt["inputs"]]
                    if any(canonical(r) not in [canonical(x) for x in original["financials"]] for r in rows):
                        issue("finance", "计算输入与原始记录不符", "critical")
                    expected = self._calculate_metrics({"records": rows}, verify_sources=False, screening_version=1 if "financial_screening" in receipt["results"] else 0).data["calculation"]
                elif receipt["kind"] == "expectation_comparison":
                    expected = self._compare_expectations(receipt["inputs"]).data["calculation"]
                    if data.get("expectations", {}).get("calculation") and data["expectations"] != self._compare_expectations(receipt["inputs"]).data:
                        issue("thesis", "经营预期比较与凭证不符", "critical")
                elif receipt["kind"] in {"dcf", "dcf_bridge", "valuation_method"}:
                    model = receipt.get("model") if receipt["kind"] == "valuation_method" else "dcf"
                    published = data.get("auxiliary_models", {}).get(model, data) if data.get("model") != model else data
                    authorized = value.assumptions if model == value.assumptions.model else value.assumptions.auxiliary_models.get(model)
                    if not authorized or not authorized.calculation_permitted or receipt["assumptions_version"] != authorized.version or receipt["inputs"] != authorized.parameters:
                        issue("valuation", "估值授权或版本失效", "critical")
                    if not authorized or (receipt["kind"] in {"valuation_method", "dcf_bridge"} and receipt["basis"] != authorized.basis):
                        issue("valuation", "方法身份或参数依据与授权不符", "critical")
                    try:
                        if receipt["kind"] == "dcf_bridge":
                            self.validate_dcf_sources(authorized.model_dump(), value.research.model_dump())
                        expected = self._valuation({"model": model, "parameters": receipt["inputs"], "assumptions_version": receipt["assumptions_version"], "basis": receipt.get("basis", {})}).data["calculation"]
                    except (ValueError, KeyError, FileNotFoundError) as exc:
                        issue("valuation", f"估值来源或复算拒绝：{exc}", "critical")
                        continue
                    if (entry.get("target") == "valuation" or published.get("calculation")) and (published.get("calculation") != receipt or any(published.get(k) != v for k, v in receipt["results"].items())):
                        issue("valuation", "发布结果与凭证不符", "critical")
                    if entry.get("target") == "valuation" or published.get("calculation"):
                        metadata = ["inputs", "formula", "assumptions_version"] + (["basis", "model"] if receipt["kind"] != "dcf" else [])
                        metadata += [k for k in ["scenarios", "sensitivity"] if k in receipt]
                        if any(published.get(k) != receipt.get(k) for k in metadata):
                            issue("valuation", "发布参数、方法、公式或情景与凭证不符", "critical")
                else:
                    issue("valuation", "未知凭证类型，不支持复算", "critical")
                    continue
                if canonical(expected) != canonical(receipt):
                    issue("valuation", "计算复算不一致", "critical")
                else:
                    calcs.add(ref)
            if data.get("conflicts"):
                permitted = {canonical(r)["conflict_hash"] for r in original.get("conflict_resolutions", [])
                             if canonical(r) in [canonical(x) for x in data.get("conflict_resolutions", [])]}
                if any(_hash(rows) not in permitted for rows in data["conflicts"]):
                    for rows in data["conflicts"]:
                        if _hash(rows) in permitted:
                            continue
                        noncritical = next((a for a in assessments if a["conflict_hash"] == _hash(rows)), None)
                        issue("finance", "非关键财务差异：" + noncritical["reason"] if noncritical else "财务冲突尚未调节",
                              "minor" if noncritical else "major")
        if not original["financials"] or not original["evidence"]:
            issue("finance", "真实研究缺少可定位财务记录或定性研究证据")
        return ToolResponse(ok=True, data={"passed": not any(i["severity"] in {"major", "critical"} for i in issues), "issues": issues,
            "conflict_assessments": assessments,
            "business_checks": {"scope": "原文定位、数字、计算及版本通过程序校验；经济判断由独立核验Agent判断"}}, evidence_refs=sorted(refs), calculation_refs=sorted(calcs))
