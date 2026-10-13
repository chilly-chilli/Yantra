"""Local evidence adapters and deterministic finance tools; no network access."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .schemas import (
    CheckArguments,
    ConversionArguments,
    MetricsArguments,
    ReadArguments,
    ToolResponse,
    ValuationArguments,
)


class MaterialAdapter(Protocol):
    """A real retrieval adapter must expose provenance through this same interface."""

    def load_manifest(self) -> dict[str, Any]: ...
    def read_materials(self, arguments: ReadArguments) -> dict[str, Any]: ...


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric parameter")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("numeric values must be finite")
    return number


def _out(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001")))


MONEY_UNITS = {
    "unit": Decimal(1), "units": Decimal(1), "元": Decimal(1),
    "thousand": Decimal(1000), "千元": Decimal(1000),
    "万元": Decimal(10000), "million": Decimal(1000000),
    "百万元": Decimal(1000000), "亿元": Decimal(100000000),
    "billion": Decimal(1000000000),
}
SHARE_UNITS = {
    "shares": Decimal(1), "股": Decimal(1),
    "thousand shares": Decimal(1000), "千股": Decimal(1000),
    "万股": Decimal(10000), "million shares": Decimal(1000000),
    "百万股": Decimal(1000000), "亿股": Decimal(100000000),
}


def _convert(value: Any, from_unit: str, to_unit: str) -> Decimal:
    table = SHARE_UNITS if from_unit in SHARE_UNITS else MONEY_UNITS
    if from_unit not in table or to_unit not in table:
        raise ValueError(f"incompatible or unsupported units: {from_unit} -> {to_unit}")
    return _number(value) * table[from_unit] / table[to_unit]


class LocalMaterialAdapter:
    def __init__(self, materials_dir: Path | None = None):
        self.materials_dir = (materials_dir or Path(__file__).parent / "materials").resolve()

    def load_manifest(self) -> dict[str, Any]:
        return json.loads((self.materials_dir / "manifest.json").read_text(encoding="utf-8"))

    def read_materials(self, arguments: ReadArguments) -> dict[str, Any]:
        manifest = self.load_manifest()
        identifiers = {manifest["company"], manifest["company_name"], manifest["company_name"].removesuffix("（虚构）")}
        if arguments.company.strip().casefold() not in {value.casefold() for value in identifiers}:
            raise FileNotFoundError(f"no local material for company {arguments.company}")
        cutoff = date.fromisoformat(arguments.cutoff) if arguments.cutoff else None
        chosen = set(arguments.material_ids) if arguments.material_ids is not None else None
        known = {entry["id"] for entry in manifest["materials"]}
        if chosen is not None and chosen - known:
            raise FileNotFoundError(f"unknown material IDs: {sorted(chosen - known)}")
        documents = []
        for entry in manifest["materials"]:
            if chosen is not None and entry["id"] not in chosen:
                continue
            if cutoff is not None and date.fromisoformat(entry["date"]) > cutoff:
                continue
            path = (self.materials_dir / entry["file"]).resolve()
            if not path.is_relative_to(self.materials_dir):
                raise ValueError("material path is outside the fixed input directory")
            content = path.read_bytes()
            text = content.decode("utf-8")
            documents.append({
                **entry, "text": text, "file_path": str(path),
                "source_hash": hashlib.sha256(content).hexdigest(),
                "lines": [
                    {"line": i, "text": line, "ref": f"{entry['id']}:L{i}"}
                    for i, line in enumerate(text.splitlines(), 1)
                ],
            })
        if not documents:
            raise FileNotFoundError("no material meets the company, cutoff, and material filters")
        return {
            "company": manifest["company"], "company_name": manifest["company_name"],
            "material_version": manifest["version"], "fictional": bool(manifest.get("fictional")),
            "adapter": manifest["adapter"], "cutoff": arguments.cutoff,
            "documents": documents, "gaps": manifest.get("gaps", []),
        }


def _dcf(parameters: dict[str, Any]) -> dict[str, Any]:
    required = {"base_fcf", "growth_rate", "discount_rate", "terminal_growth", "years", "net_debt", "shares", "currency", "unit"}
    if set(parameters) != required:
        raise ValueError(f"DCF requires exactly these parameters: {sorted(required)}")
    p = {key: _number(parameters[key]) for key in required - {"currency", "unit"}}
    if p["years"] != p["years"].to_integral_value() or not 1 <= p["years"] <= 30:
        raise ValueError("years must be an integer from 1 to 30")
    if p["base_fcf"] < 0 or p["shares"] <= 0 or p["growth_rate"] <= -1:
        raise ValueError("this demo DCF requires nonnegative FCF, positive shares, and growth > -1")
    if p["discount_rate"] <= 0 or p["terminal_growth"] < 0 or p["discount_rate"] <= p["terminal_growth"]:
        raise ValueError("discount rate must be positive and greater than nonnegative terminal growth")
    if parameters["unit"] != "million" or not isinstance(parameters["currency"], str) or not parameters["currency"]:
        raise ValueError("DCF inputs require a currency and monetary/share quantities in millions")
    with localcontext() as context:
        context.prec = 40
        annual = []
        pv_total = Decimal(0)
        for year in range(1, int(p["years"]) + 1):
            fcf = p["base_fcf"] * (1 + p["growth_rate"]) ** year
            pv = fcf / (1 + p["discount_rate"]) ** year
            pv_total += pv
            annual.append({"year": year, "fcf": _out(fcf), "present_value": _out(pv)})
        terminal_value = fcf * (1 + p["terminal_growth"]) / (p["discount_rate"] - p["terminal_growth"])
        pv_terminal = terminal_value / (1 + p["discount_rate"]) ** int(p["years"])
        enterprise = pv_total + pv_terminal
        equity = enterprise - p["net_debt"]
        return {
            "enterprise_value": _out(enterprise), "equity_value": _out(equity),
            "per_share_value": _out(equity / p["shares"]), "annual_cash_flows": annual,
            "terminal_value": _out(terminal_value), "present_value_terminal": _out(pv_terminal),
            "currency": parameters["currency"], "unit": "million",
            "per_share_unit": f"{parameters['currency']}/share",
        }


class ToolRegistry:
    names = frozenset({"read_materials", "extract_financials", "convert_units", "calculate_metrics", "valuation", "check_consistency"})

    def __init__(self, materials_dir: Path | None = None, adapter: MaterialAdapter | None = None):
        self.adapter = adapter or LocalMaterialAdapter(materials_dir)
        self.calculations: dict[str, dict[str, Any]] = {}

    def load_manifest(self) -> dict[str, Any]:
        return self.adapter.load_manifest()

    def tool_contracts(self) -> dict[str, Any]:
        contracts = {
            "read_materials": (ReadArguments, "Read original fixed materials, line anchors and source hashes; no network search."),
            "extract_financials": (ReadArguments, "Extract fields with period, currency, unit, consolidation and exact source location."),
            "convert_units": (ConversionArguments, "Convert monetary or share units; currency remains unchanged. No implicit FX conversion."),
            "calculate_metrics": (MetricsArguments, "Normalize compatible units and compute FCF, debt, growth and profit margins deterministically."),
            "valuation": (ValuationArguments, "Confirmed-assumptions-only DCF. parameters requires base_fcf,growth_rate,discount_rate,terminal_growth,years,net_debt,shares,currency,unit. unit must be million."),
            "check_consistency": (CheckArguments, "Verify original citations, source values, current assumptions, calculations and structured research evidence gaps."),
        }
        return {name: {"description": description, "arguments_schema": schema.model_json_schema()} for name, (schema, description) in contracts.items()}

    def snapshot(self) -> dict[str, Any]:
        manifest = self.load_manifest()
        materials = self.adapter.read_materials(ReadArguments(company=manifest["company"]))
        return {
            "version": manifest["version"], "fictional": materials["fictional"],
            "manifest_hash": _hash(manifest), "manifest": manifest,
            "materials": {doc["id"]: {"source_hash": doc["source_hash"], "file_path": doc["file_path"], "text": doc["text"]} for doc in materials["documents"]},
        }

    def execute(self, name: str, args: dict[str, Any]) -> ToolResponse:
        if name not in self.names:
            return ToolResponse(ok=False, error={"code": "UNKNOWN_TOOL", "message": f"unknown tool {name}", "retryable": False})
        try:
            return getattr(self, f"_{name}")(args)
        except FileNotFoundError as exc:
            return ToolResponse(ok=False, error={"code": "MISSING_MATERIAL", "message": str(exc), "retryable": False})
        except (ValueError, TypeError, KeyError, IndexError, InvalidOperation, ValidationError) as exc:
            return ToolResponse(ok=False, error={"code": "INVALID_INPUT", "message": str(exc), "retryable": False})
        except OSError as exc:
            return ToolResponse(ok=False, error={"code": "MATERIAL_IO", "message": str(exc), "retryable": True})

    def _record_calculation(self, payload: dict[str, Any]) -> str:
        ref = f"calc:{_hash(payload)[:24]}"
        self.calculations[ref] = payload
        return ref

    def _read_materials(self, args: dict[str, Any]) -> ToolResponse:
        data = self.adapter.read_materials(ReadArguments.model_validate(args))
        refs = [line["ref"] for doc in data["documents"] for line in doc["lines"]]
        return ToolResponse(ok=True, data=data, evidence_refs=refs)

    def _extract_financials(self, args: dict[str, Any]) -> ToolResponse:
        inputs = ReadArguments.model_validate(args)
        source = self.adapter.read_materials(inputs)
        fields = self.load_manifest()["financial_fields"]
        records = []
        for doc in source["documents"]:
            if not doc.get("period"):
                continue
            for field, line_number in fields.items():
                line = doc["lines"][line_number - 1]
                match = re.fullmatch(rf"[^:]+\b{re.escape(field)}: ([-+]?\d+(?:\.\d+)?)", line["text"])
                if not match:
                    raise ValueError(f"field {field} does not match original material at {line['ref']}")
                records.append({
                    "field": field, "value": float(_number(match.group(1))), "period": doc["period"],
                    "unit": "million shares" if field == "shares" else doc["unit"],
                    "currency": doc["currency"], "consolidated": doc["consolidated"],
                    "source_ref": line["ref"], "source_hash": doc["source_hash"],
                })
        if not records:
            raise FileNotFoundError("the selected materials contain no financial records")
        return ToolResponse(ok=True, data={
            "financials": records, "material_version": source["material_version"],
            "fictional": source["fictional"], "gaps": source["gaps"],
        }, evidence_refs=[record["source_ref"] for record in records])

    def _convert_units(self, args: dict[str, Any]) -> ToolResponse:
        inputs = ConversionArguments.model_validate(args).model_dump()
        result = _out(_convert(inputs["value"], inputs["from_unit"], inputs["to_unit"]))
        payload = {"kind": "unit_conversion", "inputs": inputs, "formula": "result = value × from_unit_factor / to_unit_factor; currency unchanged", "result": result}
        ref = self._record_calculation(payload)
        return ToolResponse(ok=True, data={**payload, "calculation_ref": ref}, calculation_refs=[ref])

    def _calculate_metrics(self, args: dict[str, Any]) -> ToolResponse:
        records = MetricsArguments.model_validate(args).records
        if not records:
            raise ValueError("financial records are required")
        periods: dict[str, dict[str, Decimal]] = {}
        currencies = {record.currency for record in records}
        scopes = {record.consolidated for record in records}
        if len(currencies) != 1 or len(scopes) != 1:
            raise ValueError("mixed currencies or consolidation scopes require reconciliation")
        normalized = []
        for record in records:
            fields = periods.setdefault(record.period, {})
            if record.field in fields:
                raise ValueError(f"duplicate field {record.field} in {record.period}")
            target_unit = "million shares" if record.field == "shares" else "million"
            fields[record.field] = _convert(record.value, record.unit, target_unit)
            normalized.append({**record.model_dump(), "normalized_value": _out(fields[record.field]), "normalized_unit": target_unit})
        required = {"revenue", "operating_profit", "net_profit", "operating_cash_flow", "capex", "debt", "cash", "shares"}
        for period, fields in periods.items():
            if required - fields.keys():
                raise ValueError(f"missing fields in {period}: {sorted(required - fields.keys())}")
            if fields["revenue"] == 0:
                raise ValueError("zero revenue cannot support profit margins")
        ordered = sorted(periods)
        metrics: dict[str, Any] = {
            "fcf": {period: _out(fields["operating_cash_flow"] - fields["capex"]) for period, fields in periods.items()},
            "operating_margin": {period: _out(fields["operating_profit"] / fields["revenue"]) for period, fields in periods.items()},
            "net_margin": {period: _out(fields["net_profit"] / fields["revenue"]) for period, fields in periods.items()},
            "net_debt": _out(periods[ordered[-1]]["debt"] - periods[ordered[-1]]["cash"]),
            "latest_period": ordered[-1], "shares": _out(periods[ordered[-1]]["shares"]),
            "currency": next(iter(currencies)), "unit": "million", "consolidated": next(iter(scopes)),
        }
        if len(ordered) > 1:
            previous = periods[ordered[-2]]["revenue"]
            if previous == 0:
                raise ValueError("zero prior revenue cannot support growth calculation")
            metrics["revenue_growth"] = _out(periods[ordered[-1]]["revenue"] / previous - 1)
        formulas = {
            "fcf": "operating_cash_flow - capex",
            "net_debt": "debt - cash", "operating_margin": "operating_profit / revenue",
            "net_margin": "net_profit / revenue", "revenue_growth": "latest_revenue / prior_revenue - 1",
            "units": "normalized_value = value × from_unit_factor / target_unit_factor",
        }
        payload = {"kind": "financial_metrics", "inputs": normalized, "formula": formulas, "results": metrics}
        ref = self._record_calculation(payload)
        return ToolResponse(ok=True, data={"metrics": metrics, **metrics, "calculation": payload, "calculation_ref": ref}, evidence_refs=[record.source_ref for record in records], calculation_refs=[ref])

    def _valuation(self, args: dict[str, Any]) -> ToolResponse:
        arguments = ValuationArguments.model_validate(args)
        parameters = arguments.parameters
        base = _dcf(parameters)
        scenarios = []
        for name, growth_shift, discount_shift in [("bear", -.02, .02), ("base", 0, 0), ("bull", .02, -.02)]:
            scenario = dict(parameters)
            scenario["growth_rate"] = _out(_number(parameters["growth_rate"]) + _number(growth_shift))
            scenario["discount_rate"] = _out(_number(parameters["discount_rate"]) + _number(discount_shift))
            try:
                scenario_result = _dcf(scenario)
            except (ValueError, InvalidOperation) as exc:
                scenarios.append({"name": name, "inputs": scenario, "status": "invalid", "reason": str(exc)})
            else:
                scenarios.append({"name": name, "inputs": scenario, "status": "calculated", **scenario_result})
        sensitivity = []
        for discount_shift in [Decimal("-.02"), Decimal(0), Decimal(".02")]:
            for growth_shift in [Decimal("-.01"), Decimal(0), Decimal(".01")]:
                inputs = dict(parameters)
                inputs["discount_rate"] = _out(_number(parameters["discount_rate"]) + discount_shift)
                inputs["terminal_growth"] = _out(_number(parameters["terminal_growth"]) + growth_shift)
                cell = {"discount_rate": inputs["discount_rate"], "terminal_growth": inputs["terminal_growth"]}
                try:
                    sensitivity_result = _dcf(inputs)
                except (ValueError, InvalidOperation) as exc:
                    cell.update({"status": "invalid", "reason": str(exc)})
                else:
                    cell.update({"status": "calculated", "per_share_value": sensitivity_result["per_share_value"]})
                sensitivity.append(cell)
        formula = {
            "annual_fcf": "FCF_t = base_fcf × (1 + growth_rate)^t",
            "present_value": "PV_t = FCF_t / (1 + discount_rate)^t",
            "terminal_value": "TV = FCF_years × (1 + terminal_growth) / (discount_rate - terminal_growth)",
            "enterprise_value": "EV = sum(PV_t) + TV / (1 + discount_rate)^years",
            "equity_value": "equity_value = EV - net_debt",
            "per_share_value": "per_share_value = equity_value / shares",
        }
        payload = {
            "kind": "dcf", "model": "FCFF DCF (fixed industrial demonstration)",
            "scope": "Positive, stable unlevered industrial free cash flow. Demo OCF explicitly excludes financing interest and tax shields; actual statements require interest, taxes and working capital reconciliation. Not suitable for financial firms or unresolved cash flow deficits.",
            "inputs": parameters, "assumptions_version": arguments.assumptions_version,
            "formula": formula, "results": base, "scenarios": scenarios, "sensitivity": sensitivity,
        }
        ref = self._record_calculation(payload)
        return ToolResponse(ok=True, data={**base, "scenarios": scenarios, "sensitivity": sensitivity, "inputs": parameters, "formula": formula, "assumptions_version": arguments.assumptions_version, "calculation": payload, "calculation_ref": ref}, calculation_refs=[ref])

    def _check_consistency(self, args: dict[str, Any]) -> ToolResponse:
        inputs = CheckArguments.model_validate(args)
        results = inputs.results.values() if isinstance(inputs.results, dict) else inputs.results
        entries = [entry.model_dump() if hasattr(entry, "model_dump") else entry for entry in results]
        manifest = self.load_manifest()
        company = inputs.research.company if inputs.research else manifest["company"]
        read_args = {"company": company, "cutoff": inputs.research.cutoff if inputs.research else None}
        extracted = self._extract_financials(read_args)
        original = {row["source_ref"]: row for row in extracted.data["financials"]}
        documents = self.adapter.read_materials(ReadArguments.model_validate(read_args))["documents"]
        anchors = {line["ref"]: line["text"] for doc in documents for line in doc["lines"]}
        expected_fields = {(row["period"], row["field"]): row for row in original.values()}
        issues: list[dict[str, Any]] = []
        validated_refs: set[str] = set()
        validated_calcs: set[str] = set()
        validated_claims: list[str] = []

        def issue(target: str, requirement: str, message: str, severity: str = "major") -> None:
            issues.append({"id": f"issue-{len(issues) + 1}", "severity": severity, "target": target, "requirement": requirement, "message": message})

        for entry in entries:
            data = entry.get("data", entry)
            role = entry.get("target") or ("finance" if "financials" in data else "valuation" if "calculation" in data or "per_share_value" in data else "thesis")
            if role not in {"finance", "thesis", "valuation", "verification"}:
                role = "thesis"
            if "financials" in data:
                metrics = data.get("metrics", {})
                if not isinstance(metrics, dict) or not metrics.get("calculation"):
                    issue("finance", "return_metric_receipt", "Financial result must include deterministic metric inputs, formulas, and results", "critical")
                latest_period = sorted({row["period"] for row in original.values()})[-1]
                latest_fields = {row.get("field") for row in data["financials"] if row.get("period") == latest_period}
                required_fields = {"revenue", "operating_profit", "net_profit", "operating_cash_flow", "capex", "debt", "cash", "shares"}
                if required_fields - latest_fields:
                    issue("finance", "complete_financial_inputs", f"Latest-period financial basis lacks required fields: {sorted(required_fields - latest_fields)}")
            if "per_share_value" in data and not data.get("calculation"):
                issue("valuation", "return_dcf_receipt", "Valuation must return deterministic inputs, formulas, results, scenarios, and sensitivity receipt", "critical")
            for ref in entry.get("evidence_refs", []):
                if ref not in anchors:
                    issue(role, "repair_citation", f"Citation does not resolve within cutoff materials: {ref}")
                else:
                    validated_refs.add(ref)
            for row in data.get("financials", []):
                source = original.get(row.get("source_ref"))
                if source is None:
                    issue("finance", "repair_financial_source", f"Financial field has no exact source: {row.get('field')}")
                    continue
                keys = {"field", "value", "period", "unit", "currency", "consolidated", "source_hash"}
                if any(row.get(key) != source[key] for key in keys):
                    issue("finance", "repair_financial_value", f"Financial record disagrees with original at {row['source_ref']}", "critical")
                else:
                    validated_refs.add(row["source_ref"])
            calculation_containers = [data] if data.get("calculation") else []
            if isinstance(data.get("metrics"), dict) and data["metrics"].get("calculation"):
                calculation_containers.append(data["metrics"])
            for container in calculation_containers:
                calculation = container["calculation"]
                ref = container.get("calculation_ref")
                if ref != f"calc:{_hash(calculation)[:24]}":
                    issue(role, "repair_calculation", "Calculation identifier does not match inputs, formula, and results", "critical")
                elif calculation.get("kind") == "dcf":
                    if calculation.get("assumptions_version") != inputs.assumptions.version or not inputs.assumptions.confirmed:
                        issue("valuation", "use_confirmed_assumptions", "Valuation uses unconfirmed or obsolete assumptions", "critical")
                    if calculation.get("inputs") != inputs.assumptions.parameters:
                        issue("valuation", "use_current_parameters", "Valuation inputs differ from current assumptions", "critical")
                    expected = self._valuation({"parameters": calculation["inputs"], "assumptions_version": calculation["assumptions_version"]}).data["calculation"]
                    if calculation != expected:
                        issue("valuation", "recalculate_dcf", "DCF calculation, scenarios, sensitivity, or formulas differ from deterministic recomputation", "critical")
                    elif any(container.get(key) != value for key, value in expected["results"].items()):
                        issue("valuation", "repair_valuation_result", "Returned valuation values disagree with the calculation record", "critical")
                    elif container.get("scenarios") != expected["scenarios"] or container.get("sensitivity") != expected["sensitivity"]:
                        issue("valuation", "repair_valuation_scenarios", "Returned scenarios or sensitivity disagree with the calculation record", "critical")
                    elif any(container.get(key) != expected[key] for key in {"inputs", "formula", "assumptions_version"}):
                        issue("valuation", "repair_valuation_metadata", "Displayed DCF inputs, formula, or version disagree with the calculation record", "critical")
                    else:
                        validated_calcs.add(ref)
                elif calculation.get("kind") == "financial_metrics":
                    records = [{key: value for key, value in row.items() if key not in {"normalized_value", "normalized_unit"}} for row in calculation["inputs"]]
                    for row in records:
                        source = original.get(row.get("source_ref"))
                        if source is None or any(row.get(key) != source[key] for key in {"field", "value", "period", "unit", "currency", "consolidated", "source_hash"}):
                            issue("finance", "repair_calculation_inputs", "Financial calculation input differs from original cited material", "critical")
                    expected = self._calculate_metrics({"records": records}).data["calculation"]
                    if calculation != expected:
                        issue("finance", "recalculate_metrics", "Financial metrics differ from deterministic recomputation", "critical")
                    elif container.get("metrics") != expected["results"] or any(container.get(key) != value for key, value in expected["results"].items()):
                        issue("finance", "repair_metric_result", "Returned financial metrics disagree with the calculation record", "critical")
                    else:
                        validated_calcs.add(ref)
            for claim_index, claim in enumerate(data.get("claims", [])):
                if not isinstance(claim, dict):
                    issue("thesis", "structure_claim", "Claims require an explicit fact/assumption/prediction/judgment type")
                    continue
                kind = claim.get("kind", claim.get("type"))
                if kind not in {"fact", "assumption", "prediction", "judgment"}:
                    issue("thesis", "classify_claim", "Claim type must distinguish fact, assumption, prediction, and judgment")
                refs = claim.get("evidence_refs", [])
                if kind == "fact" and (not refs or any(ref not in anchors for ref in refs)):
                    issue("thesis", "source_fact", "Fact claim requires citations resolving to original material")
                if kind == "fact" and claim.get("field") and "value" in claim:
                    source = expected_fields.get((claim.get("period"), claim["field"]))
                    if source is None or _number(claim["value"]) != _number(source["value"]) or source["source_ref"] not in refs:
                        issue("thesis", "repair_quantitative_claim", f"Quantitative claim contradicts cited source: {claim.get('field')}", "critical")
                    else:
                        validated_claims.append(claim.get("id", f"claim-{claim_index + 1}"))
                elif kind == "fact" and refs and all(ref in anchors for ref in refs):
                    text = str(claim.get("text", claim.get("statement", "")))
                    financial_refs = [original[ref] for ref in refs if ref in original]
                    if financial_refs:
                        numbers = [Decimal(value) for value in re.findall(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9])", text)]
                        allowed = {_number(row["value"]) for row in financial_refs} | {Decimal(2024), Decimal(2025)}
                        if any(number not in allowed for number in numbers):
                            issue("thesis", "repair_textual_number", "A numerical fact in claim text is not supported by the cited financial lines", "critical")
            if data.get("hypotheses") is not None:
                for key in ["risks", "disconfirming", "tracking"]:
                    if not data.get(key):
                        issue("thesis", f"add_{key}", f"Research is missing {key}")
                expectations = data.get("expectations", {})
                if isinstance(expectations, dict):
                    baseline = expectations.get("baseline")
                    insufficient = expectations.get("status") in {"insufficient_evidence", "hypothesis", "unverified", "unverified_hypothesis", "待验证", "证据不足"}
                    if not baseline and not insufficient:
                        issue("thesis", "qualify_expectations", "Market expectations lack a dated sourced baseline or an explicit evidence gap")
                    if baseline and (not isinstance(baseline, dict) or not baseline.get("date") or not baseline.get("source")):
                        issue("thesis", "source_expectations_baseline", "Market expectations baseline requires a source and date")
                    elif baseline:
                        if baseline["source"] not in anchors:
                            issue("thesis", "verify_expectations_baseline", "Market expectations baseline source does not resolve to the supplied original material")
                        try:
                            baseline_day = date.fromisoformat(baseline["date"])
                            if inputs.research and baseline_day > date.fromisoformat(inputs.research.cutoff):
                                issue("thesis", "respect_baseline_cutoff", "Market expectations baseline is later than the research cutoff")
                        except (ValueError, TypeError):
                            issue("thesis", "date_expectations_baseline", "Market expectations baseline date must be an ISO calendar date")

        business_checks = {
            "scope": "Automatic checks cover source identity, calculation reproducibility, versions and required evidence flags. Investment reasoning still requires business judgment.",
            "items_requiring_judgment": ["causal investment mechanism", "materiality of risks", "appropriateness of model and rates", "credibility of alternative explanations"],
            "return_guarantee": False,
        }
        for entry in entries:
            for ref in entry.get("calculation_refs", []):
                if ref not in self.calculations:
                    data = entry.get("data", entry)
                    target = "finance" if "financials" in data else "valuation" if "per_share_value" in data else "thesis"
                    issue(target, "resolve_calculation_reference", f"Calculation reference has no reproducible inputs, formula, and results: {ref}", "critical")
        if inputs.assumptions.parameters:
            latest_period = sorted({row["period"] for row in original.values()})[-1]
            latest = {field: row for (period, field), row in expected_fields.items() if period == latest_period}
            source_derived = {
                "base_fcf": _convert(latest["operating_cash_flow"]["value"], latest["operating_cash_flow"]["unit"], "million") - _convert(latest["capex"]["value"], latest["capex"]["unit"], "million"),
                "net_debt": _convert(latest["debt"]["value"], latest["debt"]["unit"], "million") - _convert(latest["cash"]["value"], latest["cash"]["unit"], "million"),
                "shares": _convert(latest["shares"]["value"], latest["shares"]["unit"], "million shares"),
            }
            required_sources = {
                "base_fcf": {latest["operating_cash_flow"]["source_ref"], latest["capex"]["source_ref"]},
                "net_debt": {latest["debt"]["source_ref"], latest["cash"]["source_ref"]},
                "shares": {latest["shares"]["source_ref"]},
            }
            for key, expected_value in source_derived.items():
                if key in inputs.user_overrides:
                    continue
                if key not in inputs.assumptions.parameters or _number(inputs.assumptions.parameters[key]) != expected_value:
                    issue("valuation", "source_model_input", f"DCF parameter {key} differs from the current cited financial basis without an explicit user override", "critical")
                basis = json.dumps(inputs.assumptions.basis.get(key, ""), ensure_ascii=False)
                if any(ref not in basis for ref in required_sources[key]):
                    issue("valuation", "cite_parameter_basis", f"DCF parameter {key} requires the exact underlying source references")
            for key in ["growth_rate", "discount_rate", "terminal_growth", "years"]:
                if key not in inputs.user_overrides and not inputs.assumptions.basis.get(key):
                    issue("valuation", "explain_parameter_assumption", f"DCF assumption {key} requires an explicit basis or evidence gap")
        return ToolResponse(ok=True, data={
            "passed": not any(item["severity"] in {"critical", "major"} for item in issues),
            "issues": issues, "business_checks": business_checks,
            "validated_evidence_refs": sorted(validated_refs), "validated_claim_refs": validated_claims,
            "validated_calculation_refs": sorted(validated_calcs),
        }, evidence_refs=sorted(validated_refs), calculation_refs=sorted(validated_calcs))
