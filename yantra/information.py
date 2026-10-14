"""Read-only information adapters for the existing JSON command extension protocol."""
from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx
import jsonschema


class InformationError(ValueError):
    """An adapter-authored message safe to expose without a remote error body."""


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
DATE = {"type": "string", "pattern": r"^[0-9]{8}$"}
TUSHARE_FINANCIAL = object_schema({"ts_code": THSCODE, "period": DATE,
    "start_date": DATE, "end_date": DATE, "report_type": {"const": "1"}}, ("ts_code",)) | {
        "anyOf": [{"required": ["period"]}, {"required": ["start_date", "end_date"]}]}
TUSHARE_OPERATIONS = {
    "stock_basic": {"type": "object", "properties": {"ts_code": THSCODE, "name": TEXT,
        "exchange": {"enum": ["SSE", "SZSE", "BSE"]}, "list_status": {"const": "L"}},
        "anyOf": [{"required": ["ts_code"]}, {"required": ["name"]}], "additionalProperties": False},
    "daily": object_schema({"ts_code": THSCODE, "trade_date": DATE, "start_date": DATE, "end_date": DATE}, ("ts_code",)) | {
        "anyOf": [{"required": ["trade_date"]}, {"required": ["start_date", "end_date"]}]},
    "income": TUSHARE_FINANCIAL, "balancesheet": TUSHARE_FINANCIAL, "cashflow": TUSHARE_FINANCIAL,
}
YAHOO_SYMBOL = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,19}$"}
YAHOO_OPERATIONS = {
    "quote": object_schema({"symbol": YAHOO_SYMBOL}, ("symbol",)),
    "history": object_schema({"symbol": YAHOO_SYMBOL, "start": {"type": "string", "format": "date"},
        "end": {"type": "string", "format": "date"}, "interval": {"enum": ["1d", "1wk", "1mo"]},
        "auto_adjust": {"const": False}, "back_adjust": {"const": False}}, ("symbol", "start", "end")),
    "income-statement": object_schema({"symbol": YAHOO_SYMBOL, "frequency": {"enum": ["yearly", "quarterly"]}}, ("symbol",)),
    "balance-sheet": object_schema({"symbol": YAHOO_SYMBOL, "frequency": {"enum": ["yearly", "quarterly"]}}, ("symbol",)),
    "cash-flow": object_schema({"symbol": YAHOO_SYMBOL, "frequency": {"enum": ["yearly", "quarterly"]}}, ("symbol",)),
}


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
    "tushare": operation_schema(TUSHARE_OPERATIONS, "operation"),
    "tavily": object_schema({"query": TEXT, "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        "topic": {"enum": ["general", "news"]}, "search_depth": {"const": "basic"},
        "include_domains": {"type": "array", "items": {"type": "string", "pattern": r"^[A-Za-z0-9.-]+$"}, "maxItems": 10}}, ("query",)),
    "yahoo": operation_schema(YAHOO_OPERATIONS, "operation"),
}
ENV_REFS = {"fuyao": {"FUYAO_API_KEY": "FUYAO_API_KEY"},
    **{name: {"MX_APIKEY": "MX_APIKEY"} for name in EASTMONEY_ENDPOINTS}, "akshare": {}, "cls": {},
    "tushare": {"TUSHARE_TOKEN": "TUSHARE_TOKEN"}, "tavily": {"TAVILY_API_KEY": "TAVILY_API_KEY"}, "yahoo": {}}
DESCRIPTIONS = {
    "fuyao": "同花顺 Fuyao A股：标的检索、指定证券行情及估值快照、最近财务报表。证券代码须含 SH/SZ/BJ 后缀，返回供应商原始字段及口径。",
    "eastmoney": "东方财富妙想金融资讯搜索：按 query 检索新闻、研报、公告或政策；原始结果需继续核验。",
    "eastmoney_data": "东方财富妙想金融数据：按 toolQuery 自然语言查询行情、财务及关联关系；保留原始单位、表格和证券身份，需核验。",
    "akshare": "AKShare只读信息：白名单函数查询个股资料、历史行情、新闻、财务摘要；保留中文字段和上游单位，limit控制返回行数并标记截断。",
    "cls": "觅知API聚合的财联社全部实时电报。无参数；第三方转发来源，事件结论需查原文核验。",
    "tushare": "用户自己的 Tushare 直连：A股身份、未复权日线及合并财务三表。权限按具体接口核实；日线交易日期不代表实时行情，财务须定位原始公告。",
    "tavily": "Tavily basic 网页搜索：寻找公告、财报和原始地址。保留完整结果后通过现有公开获取流程读取原文；摘要不能代替数值依据。",
    "yahoo": "Yahoo Finance，通过非官方 yfinance 访问：指定证券报价、未调整历史行情及财务三表。保留完整安全返回与字段，聚合财务须定位原始披露；仅个人研究使用。",
}
SERVICE_METADATA = {
    "tushare": {"upstream": "Tushare Pro", "markets": ["CN"], "data_kinds": ["security_identity", "market_quote", "financials"],
        "access_methods": ["http_api"], "credential_env": "TUSHARE_TOKEN", "locator": "response.data.fields/items", "original_disclosure_required": True},
    "tavily": {"upstream": "public web via Tavily", "markets": ["CN", "HK", "US"], "data_kinds": ["disclosure", "news"],
        "access_methods": ["web_search"], "credential_env": "TAVILY_API_KEY", "locator": "response.results[].url", "original_disclosure_required": True},
    "yahoo": {"upstream": "Yahoo Finance", "access_tool": "yfinance", "markets": ["CN", "HK", "US"], "data_kinds": ["market_quote", "financials"],
        "access_methods": ["public_api_sdk"], "credential_env": None, "locator": "raw_responses plus SDK records", "original_disclosure_required": True,
        "coverage": "symbol-specific; live market coverage unverified", "usage": "personal research; Yahoo data terms apply"},
}


def extension_specs(python=None, absolute=True):
    command = [python or sys.executable, str(Path(__file__).resolve())] if absolute else [".venv/bin/python", "-m", "yantra.information"]
    specs = {name: {"description": DESCRIPTIONS[name], "command": command + [name],
        "input_schema": schema, "env_refs": ENV_REFS[name], "roles": ["finance", "thesis", "verification"],
        "timeout_seconds": 60, "cost_cny": 0, "cost_kind": "service", "enabled": name in {"akshare", "cls"}}
        for name, schema in SCHEMAS.items()}
    for name in SERVICE_METADATA:
        specs[name].update(information_provider=name, cost_status="unknown")
    return specs


def credential(name):
    value = os.getenv(name, "").strip()
    if not value or value.startswith("<") or value.lower().startswith(("replace_me", "your_", "fill_me")):
        raise ValueError(f"请在 .env 填写 {name} 并通过 --env-file 加载")
    return value


def request_json(url, *, method="GET", headers=None, parameters=None, payload=None):
    # Do not follow redirects carrying credentials or expose remote error bodies.
    try:
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
    except httpx.HTTPError:
        raise ValueError("信息接口网络请求失败") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("信息接口返回无效 JSON") from None
    if not isinstance(value, (dict, list)):
        raise ValueError("信息接口返回格式应为 JSON 对象或数组")
    return value


def yahoo_session():
    """Capture data JSON, prevent SDK request retries and consent submissions."""
    try:
        requests = importlib.import_module("curl_cffi.requests")
    except ModuleNotFoundError:
        raise InformationError("请先安装 requirements-information.txt 中的 yfinance") from None

    class SnapshotSession(requests.Session):
        def __init__(self):
            super().__init__(impersonate="chrome")
            self.raw_responses, self.requests_seen = [], {}

        def request(self, method, url, *args, **kwargs):
            parsed = urlsplit(url)
            if method.upper() != "GET" or parsed.scheme != "https" or parsed.hostname not in {
                    "query1.finance.yahoo.com", "query2.finance.yahoo.com", "fc.yahoo.com"}:
                raise InformationError("Yahoo 请求需登录、同意或不在只读白名单内；请使用公开替代来源")
            # Crumbs and cookies are authentication state, never evidence parameters.
            parameters = {k: v for k, v in dict(parse_qsl(parsed.query)).items() if k.lower() not in {"crumb", "token", "sessionid"}}
            parameters.update({k: v for k, v in (kwargs.get("params") or {}).items() if k.lower() not in {"crumb", "token", "sessionid"}})
            safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            key = json.dumps([safe_url, parameters], sort_keys=True, default=str)
            if key in self.requests_seen:
                previous = self.requests_seen[key]
                if previous is None:raise InformationError("Yahoo 同一失败请求不自动重试")
                return previous
            self.requests_seen[key] = None
            kwargs["allow_redirects"] = False
            kwargs["timeout"] = min(kwargs.get("timeout", 15), 15)
            response = super().request(method, url, *args, **kwargs)
            # fc.yahoo.com can return a cookie together with 404; no data is used.
            bootstrap = parsed.hostname == "fc.yahoo.com" or parsed.path.endswith("/getcrumb")
            if 300 <= response.status_code < 400:
                raise InformationError("Yahoo 页面需要重定向或登录；请使用公开替代来源")
            if not bootstrap and not 200 <= response.status_code < 300:
                raise InformationError(f"Yahoo 信息接口 HTTP {response.status_code}")
            if not bootstrap:
                if len(response.content) > 900_000:raise InformationError("Yahoo 原始响应超过 900 KB")
                try:data = json.loads(response.content)
                except (ValueError, UnicodeDecodeError):raise InformationError("Yahoo 返回无效 JSON") from None
                self.raw_responses.append({"url": safe_url, "parameters": parameters, "status": response.status_code,
                    "acquired_at": datetime.now(timezone.utc).isoformat(), "data": data})
            self.requests_seen[key] = response
            return response

    return SnapshotSession()


def yahoo_fetch(arguments):
    try:
        yf = importlib.import_module("yfinance")
    except ModuleNotFoundError:
        raise InformationError("请先安装 requirements-information.txt 中的 yfinance") from None
    operation, parameters = arguments["operation"], arguments["parameters"]
    if operation == "history":
        try:start, end = (datetime.fromisoformat(parameters[k]) for k in ("start", "end"))
        except ValueError:raise InformationError("Yahoo history 日期无效") from None
        if start >= end:raise InformationError("Yahoo history end 必须晚于 start，且 end 为不含的边界")
    with yahoo_session() as session, redirect_stdout(sys.stderr):
        ticker = yf.Ticker(parameters["symbol"], session=session)
        if operation == "quote":
            value = ticker.get_info()
            if not isinstance(value, dict) or not value:raise InformationError("Yahoo 报价为空")
            data = {"info": value}
        else:
            if operation == "history":
                frame = ticker.history(start=parameters["start"], end=parameters["end"], interval=parameters.get("interval", "1d"),
                    auto_adjust=False, back_adjust=False, repair=False, actions=True, rounding=False, timeout=15, raise_errors=True)
                metadata = ticker.get_history_metadata()
                data = {"metadata": metadata, "adjustment": "unadjusted", "end_exclusive": True}
            else:
                method = {"income-statement": "get_income_stmt", "balance-sheet": "get_balance_sheet", "cash-flow": "get_cashflow"}[operation]
                frame = getattr(ticker, method)(freq=parameters.get("frequency", "yearly"))
                # Keep period-labelled columns and raw SDK values; this is aggregate data.
                data = {"original_disclosure_verified": False, "original_disclosure_required": True}
            if frame is None or frame.empty:raise InformationError("Yahoo 查询结果为空")
            data.update(columns=[str(c) for c in frame.columns], records=json.loads(frame.reset_index().to_json(orient="records", date_format="iso")))
        data["raw_responses"] = session.raw_responses
        if not session.raw_responses:
            raise InformationError("Yahoo 未保存完整原始响应，不能作为数值来源")
    return data


def normalized_quote(provider, arguments, data):
    """Only explicit supplier times and unadjusted prices qualify as quotes."""
    if provider == "yahoo" and arguments["operation"] == "quote":
        info = data["info"]
        symbol = str(info.get("symbol", ""))
        if symbol.upper() != arguments["parameters"]["symbol"].upper():return None
        price, stamp, currency = info.get("regularMarketPrice"), info.get("regularMarketTime"), info.get("currency")
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):return None
        try:price_time = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):return None
        if symbol.endswith(".HK"):market, exchange, code, share_class = "HK", "HKEX", symbol[:-3].zfill(5), "ordinary"
        elif symbol.endswith((".SS", ".SZ")):market, exchange, code, share_class = "CN", "SSE" if symbol.endswith(".SS") else "SZSE", symbol[:-3], "A"
        else:
            market, code, share_class = "US", symbol, "ordinary"
            exchange = {"NYQ": "NYSE", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "ASE": "AMEX"}.get(info.get("exchange"), info.get("exchange"))
    elif provider == "tushare" and arguments["operation"] == "daily":
        rows = [dict(zip(data["data"]["fields"], row)) for row in data["data"]["items"]]
        code = arguments["parameters"]["ts_code"]
        rows = [row for row in rows if row.get("ts_code") == code and row.get("trade_date")]
        if not rows:return None
        row = max(rows, key=lambda row: row["trade_date"])
        try:price_time = datetime.strptime(row["trade_date"], "%Y%m%d").date().isoformat()
        except ValueError:return None
        price, currency, market, share_class = row.get("close"), "CNY", "CN", "A"
        code, suffix = code.split(".")
        if code.startswith(("200", "900")):return None
        exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[suffix]
    else:return None
    if isinstance(price, bool) or not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0 or not currency or not exchange:return None
    return {"price": price, "price_time": price_time, "currency": currency, "unit": currency + "/share", "security_code": code,
        "share_class": share_class, "market": market, "exchange": exchange, "adjustment": "unadjusted", "provider": provider,
        "time_basis": "daily_close_date" if provider == "tushare" else "regular_market_timestamp"}


def _query_argument(provider, purpose, research):
    """One substantive request for the resolved security and existing cutoff."""
    company, code = research.get("company", ""), research.get("security_code")
    exchange, market = research.get("exchange"), research.get("market")
    suffix = {"SSE": "SH", "SZSE": "SZ", "BSE": "BJ"}.get(exchange)
    ts_code = code + "." + suffix if code and suffix and market == "CN" else None
    cutoff = datetime.fromisoformat(research.get("cutoff") or datetime.now(timezone.utc).date().isoformat())
    if provider in {"tavily", "eastmoney", "eastmoney_data"}:
        field = "toolQuery" if provider == "eastmoney_data" else "query"
        question = {"identity": "公司 上市证券代码 交易所", "quote": "最新股价 报价时间 币种 未复权", "financials": "最新年度报告 合并财务报表 原文",
            "disclosure": "年度报告 公告 原文", "counter_evidence": "风险 处罚 经营恶化", "changes": "最新公告 业务变化", "expectations": "业绩预期 原始研报"}.get(purpose, purpose)
        request = {field: research.get("query") if purpose == "search" and research.get("query") else " ".join(str(x) for x in (company, code, question, "截至", research.get("cutoff")) if x)}
        if provider == "tavily":request["max_results"] = min(10, max(1, research.get("max_results", 5)))
        return request
    if provider == "tushare":
        if purpose == "identity":return {"operation": "stock_basic", "parameters": {"ts_code": ts_code} if ts_code else {"name": company}}
        if not ts_code:return None
        if purpose == "quote":return {"operation": "daily", "parameters": {"ts_code": ts_code, "start_date": (cutoff-timedelta(days=14)).strftime("%Y%m%d"), "end_date": cutoff.strftime("%Y%m%d")}}
        if purpose == "financials":return {"operation": "income", "parameters": {"ts_code": ts_code, "start_date": (cutoff-timedelta(days=1460)).strftime("%Y%m%d"), "end_date": cutoff.strftime("%Y%m%d"), "report_type": "1"}}
    if provider == "fuyao":
        if purpose == "identity":return {"operation": "ticker-search", "parameters": {"q": company, "limit": 10}}
        if ts_code and purpose == "quote":return {"operation": "prices-snapshot", "parameters": {"thscodes": ts_code}}
        if ts_code and purpose == "financials":return {"operation": "income-statements", "parameters": {"thscode": ts_code, "period": "annual", "limit": 4}}
    if provider == "yahoo" and code and market in {"CN", "HK", "US"}:
        if market == "CN" and exchange not in {"SSE", "SZSE"}:return None
        symbol = code + {"SSE": ".SS", "SZSE": ".SZ"}[exchange] if market == "CN" else str(int(code)).zfill(4) + ".HK" if market == "HK" else code
        if purpose == "quote":return {"operation": "quote", "parameters": {"symbol": symbol}}
        if purpose == "financials":return {"operation": "income-statement", "parameters": {"symbol": symbol, "frequency": "yearly"}}
    if provider == "akshare" and code and market == "CN":
        if purpose == "identity":return {"function": "stock_individual_info_em", "parameters": {"symbol": code}}
        if purpose == "financials":return {"function": "stock_financial_abstract", "parameters": {"symbol": code}}
    return None


def query_arguments(provider, purpose, research):
    request = _query_argument(provider, purpose, research)
    if request is None:return []
    if purpose == "financials":
        operations = {"tushare": ["income", "balancesheet", "cashflow"],
            "fuyao": ["income-statements", "balance-sheets", "cash-flow-statements"],
            "yahoo": ["income-statement", "balance-sheet", "cash-flow"]}.get(provider)
        if operations:return [request | {"operation": operation} for operation in operations]
    return [request]


def fetch(provider, arguments):
    try:
        jsonschema.validate(arguments, SCHEMAS[provider])
    except (KeyError, jsonschema.ValidationError):
        raise ValueError("信息来源或参数无效") from None
    if provider == "tushare":
        parameters = dict(arguments["parameters"])
        for key in ("period", "trade_date", "start_date", "end_date"):
            if key in parameters:
                try:datetime.strptime(parameters[key], "%Y%m%d")
                except ValueError:raise ValueError("Tushare 日期无效") from None
        if parameters.get("start_date", "") > parameters.get("end_date", "99999999"):
            raise ValueError("end_date 不得早于 start_date")
        url = "https://api.tushare.pro"
        operation = arguments["operation"]
        if operation in {"income", "balancesheet", "cashflow"}:
            parameters.setdefault("report_type", "1")
        fields = "ts_code,symbol,name,fullname,exchange,curr_type,list_status,list_date" if operation == "stock_basic" else ""
        data = request_json(url, method="POST", payload={"api_name": operation, "token": credential("TUSHARE_TOKEN"), "params": parameters, "fields": fields})
        if not isinstance(data, dict) or data.get("code") != 0:
            raise ValueError("Tushare 返回业务错误；请检查具体接口权限、额度和凭据")
        table = data.get("data")
        if not isinstance(table, dict) or not isinstance(table.get("fields"), list) or not isinstance(table.get("items"), list):
            raise ValueError("Tushare fields/items 返回合约无效")
        if not table["items"]:raise ValueError("Tushare 查询结果为空；其他查询或来源仍可使用")
        if any(not isinstance(row, list) or len(row) != len(table["fields"]) for row in table["items"]):
            raise ValueError("Tushare 字段与数据行不一致")
    elif provider == "tavily":
        url = "https://api.tavily.com/search"
        payload = {"search_depth": "basic", "max_results": 5, "topic": "general", **arguments,
            "include_answer": False, "include_raw_content": False, "auto_parameters": False, "include_usage": True}
        data = request_json(url, method="POST", headers={"Authorization": "Bearer " + credential("TAVILY_API_KEY")}, payload=payload)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("Tavily 返回错误或结果合约无效")
    elif provider == "yahoo":
        url = "https://finance.yahoo.com/quote/" + arguments["parameters"]["symbol"]
        try:data = yahoo_fetch(arguments)
        except InformationError:raise
        except Exception:raise ValueError("Yahoo 调用失败；请检查依赖、限流和目标证券覆盖") from None
    elif provider == "akshare":
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
    # Direct use is safe as well as the existing credential-redacting command runner.
    if __package__:
        from .configuration import redact
    else:
        from configuration import redact
    data = redact(data, [os.getenv(reference, "") for reference in ENV_REFS[provider].values()])
    result = {"provider": provider, "source_url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(), "data": data}
    if provider in SERVICE_METADATA:
        result.update(request=arguments, metadata=SERVICE_METADATA[provider])
        if provider == "tushare":
            result["request"] = {"operation": operation, "parameters": parameters}
        quote = normalized_quote(provider, arguments, data)
        if quote:result["quote"] = quote | {"acquired_at": result["retrieved_at"]}
    return result


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
