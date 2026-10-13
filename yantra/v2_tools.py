"""Raw-source extraction receipts and conservative deterministic metrics."""
from __future__ import annotations

import json
import math
import re

from .materials import RawMaterialAdapter, cutoff_time, numeric, research_chunks
from .schemas import CheckArguments, ChildArguments, CollaborationArguments, Extraction, FetchArguments, MetricsArguments, ReadArguments, SearchArguments, ToolResponse
from .tools import ToolRegistry, _convert, _hash, _out


def canonical(value):
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [canonical(x) for x in value]
    return value


class RawToolRegistry(ToolRegistry):
    names = ToolRegistry.names | {"search_public", "fetch_source"}

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
        contracts.update(self.extension_contracts)
        return contracts

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
            if not row.disclosure_date and pair[1].get("date"):
                row.disclosure_date = pair[1]["date"]
            if not row.raw_value or row.raw_value not in text:
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
            key = (row["field"], row["period"], row["currency"], row["consolidated"])
            groups.setdefault(key, []).append(row)
        conflicts = [list(rows) for rows in groups.values() if len({(x["value"], x["unit"], x["restatement"]) for x in rows}) > 1]
        if conflicts:
            gaps.append("存在未调节的财务口径或重述冲突，保留候选值")
        accepted = {"financials": records, "evidence": evidence, "gaps": list(dict.fromkeys(gaps)), "conflicts": conflicts}
        self.extraction_path.write_text(json.dumps(accepted, ensure_ascii=False, indent=2))
        return accepted

    def _extract_financials(self, args):
        ReadArguments.model_validate(args)
        if not self.extraction_path.exists():
            raise FileNotFoundError("budgeted model extraction has not run")
        data = json.loads(self.extraction_path.read_text())
        refs = sorted({x["source_ref"] for x in data["financials"] + data["evidence"]})
        return ToolResponse(ok=True, data=data, evidence_refs=refs)

    def _calculate_metrics(self, args):
        records = MetricsArguments.model_validate(args).records
        groups, gaps = {}, []
        normalized = []
        for row in records:
            target = "million shares" if row.field == "shares" else "million"
            quantity = _convert(row.value, row.unit, target)
            item = {**row.model_dump(), "normalized_value": _out(quantity), "normalized_unit": target}
            normalized.append(item)
            key = (row.period, row.currency, row.consolidated)
            groups.setdefault(key, {}).setdefault(row.field, []).append((quantity, row))
        results = {"periods": {}, "fcf": {}, "gaps": gaps}
        for (period, currency, scope), rows in groups.items():
            unique = {}
            for field, values in rows.items():
                if len({x[0] for x in values}) == 1:
                    unique[field] = values[0]
                else:
                    gaps.append(f"未解决冲突：{period}/{field}")
            metrics = {"currency": currency, "consolidated": scope, "unit": "million"}
            for name, numerator in (("operating_margin", "operating_profit"), ("net_margin", "net_profit")):
                if "revenue" in unique and unique["revenue"][0] != 0 and numerator in unique:
                    metrics[name] = _out(unique[numerator][0] / unique["revenue"][0])
            if {"debt", "cash"} <= unique.keys():
                metrics["net_debt"] = _out(unique["debt"][0] - unique["cash"][0])
            if {"operating_cash_flow", "capex"} <= unique.keys():
                proxy = _out(unique["operating_cash_flow"][0] - abs(unique["capex"][0]))
                metrics["ocf_minus_capex"] = proxy
                if unique["operating_cash_flow"][1].cash_flow_basis == "unlevered":
                    metrics["fcff"] = proxy
                    results["fcf"][period] = proxy
                else:
                    gaps.append(f"{period} OCF−资本开支尚未调节融资利息和税盾，不视为FCFF")
            if "shares" in unique:
                row = unique["shares"][1]
                if row.share_scope and row.security_code:
                    metrics.update(shares=_out(unique["shares"][0]), security_code=row.security_code, share_scope=row.share_scope)
            results["periods"][f"{period}/{currency}/{'consolidated' if scope else 'parent'}"] = metrics
        # ISO-like fiscal dates sort numerically; annual/interim are kept separate.
        ordered = sorted(results["periods"], key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.split("/")[0])))
        if ordered:
            results.update(latest_period=ordered[-1].split("/")[0], **results["periods"][ordered[-1]])
        payload = {"kind": "raw_financial_metrics", "inputs": normalized,
                   "formula": {"net_debt": "debt - cash", "margin": "profit / revenue", "ocf_minus_capex": "OCF - abs(capex cash outflow); not FCFF without reconciliation", "units": "value * original unit factor / million"}, "results": results}
        ref = self._record_calculation(payload)
        return ToolResponse(ok=True, data={**results, "metrics": results, "calculation": payload, "calculation_ref": ref},
                            evidence_refs=[r.source_ref for r in records], calculation_refs=[ref])

    def _check_consistency(self, args):
        value = CheckArguments.model_validate(args)
        entries = value.results.values() if isinstance(value.results, dict) else value.results
        anchors = self.anchors()
        issues, refs, calcs = [], set(), set()
        original = json.loads(self.extraction_path.read_text()) if self.extraction_path.exists() else {"financials": [], "evidence": []}

        def issue(target, message, severity="major"):
            issues.append({"id": f"issue-{len(issues)+1}", "target": target, "message": message, "severity": severity, "requirement": "repair_original_evidence"})

        for entry in entries:
            data = entry.get("data", entry)
            for ref in entry.get("evidence_refs", []):
                if ref not in anchors:
                    issue("finance", f"引用不能解析：{ref}", "critical")
                else:
                    refs.add(ref)
            for row in data.get("financials", []):
                if canonical(row) not in [canonical(r) for r in original["financials"]]:
                    issue("finance", "财务记录不等于已校验原文提取", "critical")
            for row in data.get("claims", []):
                if row.get("type") == "fact" and (not row.get("evidence_refs") or any(r not in anchors for r in row["evidence_refs"])):
                    issue("thesis", "事实必须引用原始材料")
            for ref in entry.get("calculation_refs", []):
                receipt = self.calculations.get(ref)
                if not receipt or ref != f"calc:{_hash(receipt)[:24]}":
                    issue("valuation", "缺少或损坏计算凭证", "critical")
                    continue
                if receipt["kind"] == "raw_financial_metrics":
                    rows = [{k: v for k, v in r.items() if k not in {"normalized_value", "normalized_unit"}} for r in receipt["inputs"]]
                    if any(canonical(r) not in [canonical(x) for x in original["financials"]] for r in rows):
                        issue("finance", "计算输入与原始记录不符", "critical")
                    expected = self._calculate_metrics({"records": rows}).data["calculation"]
                elif receipt["kind"] == "dcf":
                    if not value.assumptions.confirmed or receipt["assumptions_version"] != value.assumptions.version or receipt["inputs"] != value.assumptions.parameters:
                        issue("valuation", "估值授权或版本失效", "critical")
                    expected = self._valuation({"parameters": receipt["inputs"], "assumptions_version": receipt["assumptions_version"]}).data["calculation"]
                else:
                    expected = receipt
                if canonical(expected) != canonical(receipt):
                    issue("valuation", "计算复算不一致", "critical")
                else:
                    calcs.add(ref)
            if data.get("conflicts"):
                issue("finance", "财务冲突尚未调节")
        if not original["financials"] or not original["evidence"]:
            issue("finance", "真实研究缺少可定位财务记录或定性研究证据")
        return ToolResponse(ok=True, data={"passed": not issues, "issues": issues,
            "business_checks": {"scope": "原文定位、数字、计算及版本通过程序校验；经济判断由独立核验Agent判断"}}, evidence_refs=sorted(refs), calculation_refs=sorted(calcs))
