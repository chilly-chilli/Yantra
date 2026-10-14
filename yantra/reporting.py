"""Readable memo views of the accepted structured artifacts."""
import json
import re

FIELDS = {"revenue": "营业收入", "operating_profit": "营业利润", "net_profit": "净利润",
          "operating_cash_flow": "经营现金流", "capex": "资本开支",
          "debt": "债务", "cash": "现金", "shares": "股数"}


def memo_markdown(state, directory):
    report = state["report"]
    research, assumptions = state["research"], state["assumptions"]
    a = state["artifacts"]
    thesis = a.get("thesis", {}).get("data", {})
    finance = a.get("finance", {}).get("data", {})
    valuation = a.get("valuation", {}).get("data", {})
    snapshot = state["input_snapshot"]
    version = assumptions["version"]
    model = valuation.get("model", assumptions.get("model", "dcf"))
    method_label = {"dcf": "DCF", "pe": "PE", "pb": "PB", "ev_ebitda": "EV/EBITDA", "ddm": "单阶段 DDM"}.get(model, model.upper())
    labels = {"fact": "事实", "management_statement": "管理层陈述", "assumption": "假设", "prediction": "预测", "judgment": "判断"}

    def source(ref):
        if ":A" in ref:
            material, anchor = ref.rsplit(":A", 1)
            entry = next((m for m in snapshot["manifest"]["materials"] if m["id"] == material), None)
            if entry:
                location = next((c["location"] for c in entry["chunks"] if c["ref"] == ref), {})
                return f"[{ref} {location}]({directory / 'materials' / entry['file']})"
        material, _, line = ref.partition(":L")
        entries = {m["id"]: m for m in snapshot["manifest"]["materials"]}
        if material not in entries or not line.isdigit():
            return ref
        path = directory / "materials" / entries[material]["file"]
        return f"[{ref}]({path}:{line})"

    def bullets(values):
        items = []
        for value in values:
            if isinstance(value, dict):
                title = value.get("description", value.get("condition", value.get("name", "")))
                detail = {k: v for k, v in value.items() if k not in {"description", "condition", "name", "evidence_refs"} and v is not None}
                items.append("- " + title + " · " + json.dumps(detail, ensure_ascii=False) + " · " + "、".join(source(r) for r in value.get("evidence_refs", [])))
            else:
                items.append("- " + str(value))
        return items or ["- 证据不足，待补查。"]

    lines = ["# Yantra 投资备忘录", f"运行：{state['run_id']} · 模式：{state['mode']} · 状态：**{state['status']}**",
             f"对象：{research['company']} · 研究期限：{research['horizon']} · 资料截止：{research['cutoff']}"]
    if snapshot["fictional"]:
        lines += ["**材料声明：本报告使用明确标注的虚构固定材料，不是真实公司研究或实时行情。**"]
    if state["mode"] == "offline-demo":
        lines += ["行动由脚本化演示策略选择，用于验证执行闭环；不代表真实模型推理。"]
    lines += ["## 1. 投资结论摘要", "[判断] " + report["投资结论摘要"]["statement"]]
    if valuation.get("per_share_value") is not None and report["投资结论摘要"].get("valuation_status", "confirmed") == "confirmed":
        lines += [f"[预测] 当前授权参数下，{method_label} 每股价值为 **{valuation['per_share_value']:.6f} {valuation.get('currency', 'CNY')}**。该结果是条件性模型输出。"]
    if research["reference_price"] is None:
        lines += ["用户参考价格及价格时点缺失；相对现价差异须使用下方已核对的计算基准。"]
    else:
        lines += [f"用户提供参考价格：{research['reference_price']}，时点：{research['price_time']}，来源：{research.get('price_source')}。"]
    lines += ["## 2. 投资逻辑"]
    for claim in thesis.get("claims", []):
        lines += ["[" + labels.get(claim.get("kind", claim.get("type")), "未分类") + "] " + claim.get("statement", claim.get("text", ""))]
        refs = claim.get("evidence_refs", [])
        if refs:
            lines += ["依据：" + "、".join(source(ref) for ref in refs)]
    for hypothesis in thesis.get("hypotheses", []):
        lines += ["[" + labels.get(hypothesis.get("kind", hypothesis.get("type", "assumption")), "未分类") + "] " + hypothesis.get("statement", ""),
                  "成立条件：" + str(hypothesis.get("condition", "待定义")),
                  "相反证据或缺口：" + str(hypothesis.get("counter_evidence", "待核验")),
                  "催化事件：" + str(hypothesis.get("catalyst", "待验证"))]
    lines += ["## 3. 事实与数据基础", "[事实] 原始材料定位如下。币种、原始单位、合并口径与原文件哈希保存在结构化记录。",
              "| 类型 | 期间 | 字段 | 数值 | 单位 | 原始位置 |", "|---|---|---|---:|---|---|"]
    for row in finance.get("financials", []):
        lines += [f"| {labels.get(row.get('information_kind') or 'fact')} | {row['period']} | {FIELDS.get(row['field'], row['field'])} | {row['value']:g} | {row['currency']} {row['unit']} | {source(row['source_ref'])} |"]
    for item in finance.get("evidence", []):
        lines += [f"[{labels.get(item['kind'], '未分类')}] {item['statement']} · {item.get('speaker') or ''} · {source(item['source_ref'])}"]
    for business in report["事实与数据基础"].get("businesses", []):
        lines += ["实际业务：" + business["name"], json.dumps({k: v for k, v in business.items() if k != "evidence_refs"}, ensure_ascii=False),
                  "业务依据：" + "、".join(source(r) for r in business["evidence_refs"])]
    if state.get("investment_logic_version"):
        setup = state["preference_snapshot"]
        lines += [f"使用偏好：{setup['id']} v{setup['version']} · 长期授权：{setup['parameters']['authorization']['status']} · 本次覆盖：{state.get('authorization_override')}",
                  "关键节点选择：" + json.dumps(state.get("choices", []), ensure_ascii=False)]
    if not finance.get("financials"):
        lines += ["未取得可用财务底表，不补造数字。"]
    lines += ["## 4. 财务分析与估值"]
    metrics = finance.get("metrics", {})
    if metrics:
        lines += ["[计算] OCF−资本开支仅在无杠杆现金流口径完成调节后用作FCFF；净债务 = 债务 − 现金。",
                  f"各期已调节自由现金流（{metrics.get('currency', '未确认')} 百万元）：" + json.dumps(metrics.get("fcf", {}), ensure_ascii=False),
                  f"最新期间净债务（{metrics.get('currency', '未确认')} 百万元）：{metrics.get('net_debt', '缺失')}。"]
        if "revenue_growth" in metrics:
            lines += [f"收入增长率：{metrics['revenue_growth']:.2%}；营业利润率：" + json.dumps(metrics.get("operating_margin", {}), ensure_ascii=False)]
        screen = metrics.get("financial_screening", {})
        if screen:
            lines += ["[计算] 四类指标矩阵（比例为ratio，金额为百万元；期间及平均余额口径保存在凭证）：",
                      "| 期间 | 业务范围 | 指标 | 数值 | 原始位置 |", "|---|---|---|---:|---|"]
            for entry in screen["matrix"]:
                for name, value in entry["metrics"].items():
                    lines += [f"| {entry['period']} | {entry['business_scope']} | {name} | {value:.6f} | {'、'.join(source(r) for r in entry['source_refs'][name])} |"]
                if entry["data_quality"]:
                    lines += ["数据待核：" + "；".join(entry["data_quality"])]
                lines += ["未计算的指标及原因：" + json.dumps(entry["unavailable"], ensure_ascii=False)]
            lines += ["异常调查信号（业务解释保留信号，触发不等同于质量差）：", *bullets(screen["signals"]),
                      "可能原因及待核查：" + json.dumps(thesis.get("anomaly_explanations", []), ensure_ascii=False)]
    proposal = a.get("proposal", {}).get("data", {})
    lines += ["模型：" + str(proposal.get("model", "尚未提出")), "适用范围：" + str(proposal.get("scope", "证据不足")),
              f"[假设] 版本 v{version}，确认状态：{assumptions.get('authorization_source', 'none')}；{'已确认' if assumptions['confirmed'] else '未确认，仅为暂定情景'}。",
              "| 参数 | 当前值 | 依据 |", "|---|---|---|"]
    for key, value in assumptions["parameters"].items():
        basis = assumptions["basis"].get(key, "口径标签")
        if isinstance(basis, dict):
            basis = str(basis.get("formula", "")) + "；" + "、".join(source(r) for r in basis.get("source_refs", []))
        elif isinstance(basis, str) and ":L" in basis:
            basis = source(basis)
        lines += [f"| {key} | {value} | {basis} |"]
    if valuation.get("per_share_value") is not None:
        lines += [f"[情景计算] {method_label} 每股价值：{valuation['per_share_value']:.6f} {valuation.get('currency', 'CNY')}；状态：{assumptions.get('authorization_source', 'none')}。"]
    if valuation.get("auxiliary_models"):
        for method, output in valuation["auxiliary_models"].items():
            lines += ["辅助模型："+method+" · "+json.dumps({k: output.get(k) for k in ("per_share_value", "value_range", "currency", "basis", "calculation_ref")}, ensure_ascii=False)]
        lines += ["模型差异："+json.dumps(valuation["model_differences"], ensure_ascii=False)]
    if report["财务分析与估值"].get("valuation_price_comparison"):
        lines += ["估值相对价格差异（与经营预测差异分开）："+json.dumps(report["财务分析与估值"]["valuation_price_comparison"], ensure_ascii=False)]
    if valuation and model != "dcf":
        lines += ["[预测] 公式：" + str(valuation.get("formula", "")),
                  "方法状态：" + str(valuation.get("method_status")),
                  "每股区间：" + json.dumps(valuation.get("value_range", []), ensure_ascii=False),
                  "区间规则：" + str(valuation.get("range_rule", "未形成区间")),
                  "企业价值（百万元）：" + str(valuation.get("enterprise_value")),
                  "股权价值（百万元）：" + str(valuation.get("equity_value")),
                  "敏感性：" + json.dumps(valuation.get("sensitivity", []), ensure_ascii=False),
                  *bullets(valuation.get("gaps", []))]
    elif valuation:
        equity_formula = "股权价值 = 企业价值 − 净债务 − 少数股东权益" if "minority_interest" in valuation.get("inputs", {}) else "股权价值 = 企业价值 − 净债务"
        lines += ["[预测] FCF_t = 基础 FCF × (1+增长率)^t；终值 = FCF_N × (1+永续增长率)/(折现率−永续增长率)。",
                  "企业价值 = 显式现金流现值合计 + 终值现值；" + equity_formula + "；每股价值 = 股权价值/股数。",
                  f"| 情景 | 增长率 | 折现率 | 每股价值（{valuation.get('currency', '未确认')}） |", "|---|---:|---:|---:|"]
        for s in valuation.get("scenarios", []):
            value = s.get("per_share_value", "无效：" + s.get("reason", ""))
            lines += [f"| {s['name']} | {s['inputs']['growth_rate']:.2%} | {s['inputs']['discount_rate']:.2%} | {value} |"]
        lines += ["敏感性结果、逐年现金流、完整输入及公式见下方计算凭证。"]
    else:
        lines += ["尚无正式估值结果。"]
    for index, key in ((5, "风险因素"), (6, "反向验证条件"), (7, "持续跟踪指标")):
        lines += [f"## {index}. {key}", *bullets(report[key])]
    lines += ["## 8. 市场预期差", "[待验证假设] " + str(report["市场预期差"].get("statement", "缺少带来源和日期的比较基准"))]
    if report["市场预期差"].get("baseline"):
        lines += ["比较基准：" + json.dumps(report["市场预期差"]["baseline"], ensure_ascii=False)]
    if report["市场预期差"].get("comparison"):
        lines += ["[计算] " + json.dumps(report["市场预期差"]["comparison"], ensure_ascii=False)]
    if report["市场预期差"].get("own_forecasts"):
        lines += ["本方预测及兑现条件："+json.dumps(report["市场预期差"]["own_forecasts"], ensure_ascii=False),
                  "已取得预测比较："+json.dumps(report["市场预期差"].get("comparisons", []), ensure_ascii=False)]
    lines += ["## 证据缺口与核验", *bullets(report["投资结论摘要"]["gaps"])]
    if state.get("raw_materials"):
        lines += [f"主框架：{state['framework']['version']} · 研究标准：v{state['standard_version']} · 估值授权：{assumptions.get('authorization_source', 'none')}",
                  f"全局截止：{state['deadline']} · 模型费用核算及未结算预留：¥{state['budget']['model_cost_cny']:.6f}（账单不可得时保留预留）"]
    lines += bullets([i["severity"] + "：" + i["message"] for i in state["issues"]]) if state["issues"] else ["当前程序检查未检出严重一致性问题；资料缺口仍需保留。"]
    lines += ["自动检查未满足：" + "、".join(k for k, passed in state["hard_checks"].items() if not passed),
              "核验通过表示符合本次研究过程标准；投资因果、风险重要性及模型适当性仍需业务判断，不保证未来收益。",
              f"[完整计算凭证：输入、公式和结果]({directory / f'calculations-v{version}.json'}) · [结构化备忘录]({directory / f'report-v{version}.json'})",
              f"[开发记录]({directory / 'events.jsonl'}) · [运行状态]({directory / 'state.json'})"]
    text = "\n\n".join(lines) + "\n"
    return re.sub(r"(?m)(\|$)\n\n(?=\|)", r"\1\n", text)
