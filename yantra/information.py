"""Read-only information adapters for the existing JSON command extension protocol."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys

import httpx
import jsonschema


def object_schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 2000}
CODE = {"type": "string", "pattern": r"^[0-9]{6}$"}
THSCODE = {"type": "string", "pattern": r"^[0-9]{6}\.(SH|SZ|BJ)$"}
THSCODES = {"type": "string", "pattern": r"^[0-9]{6}\.(SH|SZ|BJ)(,[0-9]{6}\.(SH|SZ|BJ)){0,19}$"}
ROW_LIMIT = {"type": "integer", "minimum": 1, "maximum": 1000}
FINANCIAL_PARAMS = object_schema({"thscode": THSCODE, "period": {"enum": ["annual", "quarterly"]},
    "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ("thscode", "period"))
FUYAO_OPERATIONS = {
    "ticker-search": ("/meta/tickers/search", object_schema({"q": TEXT,
        "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ("q",))),
    "prices-snapshot": ("/a-share/prices/snapshot", object_schema({"thscodes": THSCODES}, ("thscodes",))),
    "valuations-snapshot": ("/a-share/valuations/snapshot", object_schema({"thscodes": THSCODES}, ("thscodes",))),
    "income-statements": ("/a-share/financials/income-statements", FINANCIAL_PARAMS),
    "balance-sheets": ("/a-share/financials/balance-sheets", FINANCIAL_PARAMS),
    "cash-flow-statements": ("/a-share/financials/cash-flow-statements", FINANCIAL_PARAMS),
}
AKSHARE_FUNCTIONS = {
    "stock_individual_info_em": object_schema({"symbol": CODE}, ("symbol",)),
    "stock_news_em": object_schema({"symbol": TEXT}, ("symbol",)),
    "stock_financial_abstract": object_schema({"symbol": CODE}, ("symbol",)),
    "stock_zh_a_hist": object_schema({"symbol": CODE, "period": {"enum": ["daily", "weekly", "monthly"]},
        "start_date": {"type": "string", "pattern": r"^[0-9]{8}$"},
        "end_date": {"type": "string", "pattern": r"^[0-9]{8}$"},
        "adjust": {"enum": ["", "qfq", "hfq"]}}, ("symbol", "start_date", "end_date")),
}
EASTMONEY_ENDPOINTS = {"eastmoney": ("news-search", "query"),
    "eastmoney_data": ("query", "toolQuery")}


def operation_schema(operations, key):
    alternatives = []
    for name, parameters in operations.items():
        properties = {key: {"const": name}, "parameters": parameters}
        if key == "function":
            properties["limit"] = ROW_LIMIT
        alternatives.append(object_schema(properties, (key, "parameters")))
    return {"type": "object", "oneOf": alternatives}


SCHEMAS = {
    "fuyao": operation_schema({name: schema for name, (_, schema) in FUYAO_OPERATIONS.items()}, "operation"),
    **{name: object_schema({field: TEXT}, (field,)) for name, (_, field) in EASTMONEY_ENDPOINTS.items()},
    "akshare": operation_schema(AKSHARE_FUNCTIONS, "function"),
    "cls": object_schema({}),
}
ENV_REFS = {"fuyao": {"FUYAO_API_KEY": "FUYAO_API_KEY"},
    **{name: {"MX_APIKEY": "MX_APIKEY"} for name in EASTMONEY_ENDPOINTS}, "akshare": {}, "cls": {}}
DESCRIPTIONS = {
    "fuyao": "同花顺 Fuyao A股：标的检索、指定证券行情及估值快照、最近财务报表。证券代码须含 SH/SZ/BJ 后缀，返回供应商原始字段及口径。",
    "eastmoney": "东方财富妙想金融资讯搜索：按 query 检索新闻、研报、公告或政策；原始结果需继续核验。",
    "eastmoney_data": "东方财富妙想金融数据：按 toolQuery 自然语言查询行情、财务及关联关系；保留原始单位、表格和证券身份，需核验。",
    "akshare": "AKShare只读信息：白名单函数查询个股资料、历史行情、新闻、财务摘要；保留中文字段和上游单位，limit控制返回行数并标记截断。",
    "cls": "觅知API聚合的财联社全部实时电报。无参数；第三方转发来源，事件结论需查原文核验。",
}


def extension_specs(python=None, absolute=True):
    command = [python or sys.executable, str(Path(__file__).resolve())] if absolute else [".venv/bin/python", "-m", "yantra.information"]
    return {name: {"description": DESCRIPTIONS[name], "command": command + [name],
        "input_schema": schema, "env_refs": ENV_REFS[name], "roles": ["finance", "thesis", "verification"],
        "timeout_seconds": 60, "cost_cny": 0, "cost_kind": "service", "enabled": name in {"akshare", "cls"}}
        for name, schema in SCHEMAS.items()}


def credential(name):
    value = os.getenv(name, "").strip()
    if not value or value.startswith("<") or value.lower().startswith(("replace_me", "your_", "fill_me")):
        raise ValueError(f"请在 .env 填写 {name} 并通过 --env-file 加载")
    return value


def request_json(url, *, method="GET", headers=None, parameters=None, payload=None):
    # Do not follow redirects carrying credentials or expose remote error bodies.
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        with client.stream(method, url, headers=headers, params=parameters, json=payload) as response:
            if not 200 <= response.status_code < 300:
                raise ValueError(f"信息接口 HTTP {response.status_code}")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 900_000:
                    raise ValueError("接口响应超过 900 KB；请缩小查询范围")
    value = json.loads(raw)
    if not isinstance(value, (dict, list)):
        raise ValueError("信息接口返回格式应为 JSON 对象或数组")
    return value


def fetch(provider, arguments):
    try:
        jsonschema.validate(arguments, SCHEMAS[provider])
    except (KeyError, jsonschema.ValidationError):
        raise ValueError("信息来源或参数无效") from None
    if provider == "akshare":
        try:
            ak = importlib.import_module("akshare")
        except ModuleNotFoundError:
            raise ValueError("请先安装 requirements-information.txt 中的 AKShare") from None
        function, parameters = arguments["function"], arguments["parameters"]
        if function == "stock_zh_a_hist" and parameters["end_date"] < parameters["start_date"]:
            raise ValueError("end_date 不得早于 start_date")
        # Third-party progress output must not corrupt the one-object stdout contract.
        with redirect_stdout(sys.stderr):
            frame = getattr(ak, function)(**parameters)
        limit = arguments.get("limit", 200)
        data = {"function": function, "parameters": parameters, "columns": list(frame.columns),
            "total_rows": len(frame), "truncated": len(frame) > limit,
            "records": json.loads(frame.head(limit).to_json(orient="records", date_format="iso", force_ascii=False))}
        url = "https://akshare.akfamily.xyz/data/stock/stock.html"
    elif provider == "fuyao":
        path, _ = FUYAO_OPERATIONS[arguments["operation"]]
        url = "https://fuyao.aicubes.cn/api" + path
        data = request_json(url, headers={"X-api-key": credential("FUYAO_API_KEY")}, parameters=arguments["parameters"])
        if not isinstance(data, dict) or data.get("code") != 0:
            raise ValueError("Fuyao 返回业务错误或不符合 ApiResponse 合约")
    elif provider in EASTMONEY_ENDPOINTS:
        endpoint, _ = EASTMONEY_ENDPOINTS[provider]
        url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/" + endpoint
        # Saved search snapshots pass only the former credential reference.
        key_env = "EASTMONEY_APIKEY" if provider == "eastmoney" and not os.getenv("MX_APIKEY") and os.getenv("EASTMONEY_APIKEY") else "MX_APIKEY"
        data = request_json(url, method="POST", headers={"apikey": credential(key_env)}, payload=arguments)
        if isinstance(data, dict) and (data.get("success") is False or any(data[field] not in (0, "0") for field in ("status", "code") if field in data)):
            raise ValueError("妙想 API 返回业务错误；请检查 Key、调用额度和查询条件")
    else:
        url = "https://api.98dou.cn/api/hotlist/cls/all"
        data = request_json(url)
        if isinstance(data, dict) and data.get("success") is False:
            raise ValueError("觅知 API 返回失败状态")
    return {"provider": provider, "source_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "data": data}


def main():
    try:
        value = fetch(sys.argv[1], json.load(sys.stdin))
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(text.encode()) > 950_000:
            raise ValueError("信息输出超过 950 KB；请缩小查询范围或 limit")
        print(text)
    except ValueError as exc:
        # Validation/HTTP messages above contain no input or credential values.
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("信息接口调用失败；请检查网络、凭据和上游接口状态", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
