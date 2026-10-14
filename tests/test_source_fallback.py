"""Controlled source contracts: network is forbidden outside explicit substitutes."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import httpx

from yantra import information
from yantra.extensions import ExtensionRuntime, ToolSpec
from yantra.materials import RawMaterialAdapter
from yantra.sources import SourcePipeline


class SourceFallbackTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "httpx.HTTPTransport.handle_request"):
            blocker = patch(target, side_effect=AssertionError("unmocked external call forbidden"))
            blocker.start()
            self.addCleanup(blocker.stop)
        environment = patch.dict("os.environ", {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_tushare_direct_payload_quote_and_complete_table(self):
        raw = {"code": 0, "msg": None, "data": {"fields": ["ts_code", "trade_date", "close", "vol"],
            "items": [["600001.SH", "20261013", 12.5, 100], ["600001.SH", "20261012", 12.1, 99]]}}
        args = {"operation": "daily", "parameters": {"ts_code": "600001.SH", "start_date": "20261001", "end_date": "20261014"}}
        with patch.dict("os.environ", TUSHARE_TOKEN="synthetic-token"), patch.object(information, "request_json", return_value=raw) as request:
            result = information.fetch("tushare", args)
        request.assert_called_once_with("https://api.tushare.pro", method="POST", payload={"api_name": "daily",
            "token": "synthetic-token", "params": args["parameters"], "fields": ""})
        self.assertEqual(result["data"], raw)
        self.assertEqual(result["request"], args)
        self.assertEqual(result["quote"]["price_time"], "2026-10-13")
        self.assertEqual(result["quote"]["unit"], "CNY/share")
        self.assertEqual(result["quote"]["adjustment"], "unadjusted")
        self.assertEqual(result["quote"]["time_basis"], "daily_close_date")
        self.assertNotEqual(result["quote"]["price_time"], result["retrieved_at"])

    def test_tushare_permissions_empty_and_bad_row_are_safe_failures(self):
        for raw, message in (({"code": 2002, "msg": "synthetic-token"}, "业务错误"),
                ({"code": 0, "data": {"fields": ["ts_code"], "items": []}}, "为空"),
                ({"code": 0, "data": {"fields": ["ts_code"], "items": [["a", "b"]]}}, "不一致")):
            with self.subTest(raw=raw), patch.dict("os.environ", TUSHARE_TOKEN="synthetic-token"), patch.object(information, "request_json", return_value=raw):
                with self.assertRaisesRegex(ValueError, message) as caught:
                    information.fetch("tushare", {"operation": "stock_basic", "parameters": {"name": "合成公司"}})
                self.assertNotIn("synthetic-token", str(caught.exception))

    def test_new_whitelists_and_missing_keys_reject_before_external_call(self):
        cases = [("tushare", {"operation": "trade", "parameters": {}}),
            ("tushare", {"operation": "daily", "parameters": {"ts_code": "600001.SH", "adjust": "qfq"}}),
            ("tushare", {"operation": "stock_basic", "parameters": {}}),
            ("tushare", {"operation": "daily", "parameters": {"ts_code": "600001.SH"}}),
            ("tushare", {"operation": "daily", "parameters": {"ts_code": "600001.SH", "start_date": "20261001"}}),
            ("tushare", {"operation": "income", "parameters": {"ts_code": "600001.SH"}}),
            ("tushare", {"operation": "cashflow", "parameters": {"ts_code": "600001.SH", "end_date": "20261014"}}),
            ("tavily", {"query": "a", "search_depth": "advanced"}),
            ("tavily", {"query": "a", "api_key": "synthetic-key"}),
            ("yahoo", {"operation": "history", "parameters": {"symbol": "TEST", "start": "2026-01-01", "end": "2026-10-14", "auto_adjust": True}}),
            ("yahoo", {"operation": "exec", "parameters": {"symbol": "TEST"}})]
        with patch.object(information, "request_json") as request, patch.object(information.importlib, "import_module") as module:
            for provider, args in cases:
                with self.subTest(provider=provider, args=args), self.assertRaisesRegex(ValueError, "参数无效"):
                    information.fetch(provider, args)
            for provider, args, credential in (("tushare", {"operation": "stock_basic", "parameters": {"name": "合成公司"}}, "TUSHARE_TOKEN"),
                    ("tavily", {"query": "合成公司"}, "TAVILY_API_KEY")):
                with self.assertRaisesRegex(ValueError, credential):information.fetch(provider, args)
            request.assert_not_called()
            module.assert_not_called()

    def test_tavily_basic_auth_safe_complete_response_and_empty_results(self):
        raw = {"query": "合成公司 年报", "request_id": "fixture-1", "usage": {"credits": 1},
            "results": [{"url": "https://fixture.invalid/report.pdf", "title": "财报", "content": "摘要", "score": .9}], "echo": "synthetic-key"}
        with patch.dict("os.environ", TAVILY_API_KEY="synthetic-key"), patch.object(information, "request_json", return_value=raw) as request:
            result = information.fetch("tavily", {"query": "合成公司 年报", "max_results": 2})
        self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "Bearer synthetic-key"})
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["search_depth"], "basic")
        self.assertFalse(payload["auto_parameters"])
        self.assertFalse(payload["include_answer"])
        self.assertEqual(result["data"]["usage"], {"credits": 1})
        self.assertEqual(result["data"]["echo"], "[REDACTED]")
        self.assertNotIn("synthetic-key", json.dumps(result))
        with patch.dict("os.environ", TAVILY_API_KEY="synthetic-key"), patch.object(information, "request_json", return_value={"results": []}):
            self.assertEqual(information.fetch("tavily", {"query": "different substantive query"})["data"]["results"], [])

    def test_http_auth_limit_errors_and_malformed_json_hide_remote_body(self):
        original = httpx.Client
        for status, content, expected in ((401, b"synthetic-token", "HTTP 401"),
                (429, b"synthetic-token", "HTTP 429"), (200, b"not-json synthetic-token", "无效 JSON")):
            transport = httpx.MockTransport(lambda request: httpx.Response(status, content=content))
            with self.subTest(status=status), patch.object(information.httpx, "Client", side_effect=lambda **kwargs: original(transport=transport, **kwargs)):
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    information.request_json("https://fixture.invalid")
                self.assertNotIn("synthetic-token", str(caught.exception))

    def yahoo_fixture(self, info=None):
        session = MagicMock()
        session.__enter__.return_value = session
        session.raw_responses = [{"url": "https://query1.finance.yahoo.com/v7/finance/quote", "parameters": {"symbols": "TEST"},
            "data": {"quoteResponse": {"result": [info or {}]}}}]
        ticker = MagicMock()
        ticker.get_info.return_value = info or {"symbol": "TEST", "currency": "USD", "exchange": "NMS", "regularMarketPrice": 25,
            "regularMarketTime": 1791921600}
        return session, ticker, SimpleNamespace(Ticker=MagicMock(return_value=ticker))

    def test_yahoo_quote_time_currency_and_full_original_response(self):
        session, ticker, yf = self.yahoo_fixture()
        with patch.object(information.importlib, "import_module", return_value=yf), patch.object(information, "yahoo_session", return_value=session):
            result = information.fetch("yahoo", {"operation": "quote", "parameters": {"symbol": "TEST"}})
        self.assertEqual(result["data"]["raw_responses"], session.raw_responses)
        self.assertEqual(result["quote"]["price_time"], datetime.fromtimestamp(1791921600, timezone.utc).isoformat())
        self.assertEqual(result["quote"]["currency"], "USD")
        self.assertEqual(result["quote"]["exchange"], "NASDAQ")
        self.assertEqual(result["quote"]["security_code"], "TEST")
        self.assertEqual(result["metadata"]["upstream"], "Yahoo Finance")
        self.assertEqual(result["metadata"]["access_tool"], "yfinance")

    def test_yahoo_missing_actual_time_wrong_symbol_and_missing_raw_cannot_qualify(self):
        for info in ({"symbol": "TEST", "regularMarketPrice": 25, "currency": "USD", "exchange": "NMS"},
                {"symbol": "OTHER", "regularMarketPrice": 25, "currency": "USD", "exchange": "NMS", "regularMarketTime": 1791921600}):
            session, ticker, yf = self.yahoo_fixture(info)
            with self.subTest(info=info), patch.object(information.importlib, "import_module", return_value=yf), patch.object(information, "yahoo_session", return_value=session):
                result = information.fetch("yahoo", {"operation": "quote", "parameters": {"symbol": "TEST"}})
            self.assertNotIn("quote", result)
        session, ticker, yf = self.yahoo_fixture()
        session.raw_responses = []
        with patch.object(information.importlib, "import_module", return_value=yf), patch.object(information, "yahoo_session", return_value=session):
            with self.assertRaisesRegex(ValueError, "完整原始响应"):
                information.fetch("yahoo", {"operation": "quote", "parameters": {"symbol": "TEST"}})

    def test_yahoo_history_explicit_unadjusted_and_financial_periods_remain_aggregate(self):
        try:import pandas as pd
        except ImportError:self.skipTest("optional pandas unavailable")
        session, ticker, yf = self.yahoo_fixture()
        ticker.history.return_value = pd.DataFrame({"Close": [25.]}, index=pd.to_datetime(["2026-10-13"]))
        ticker.get_history_metadata.return_value = {"currency": "USD", "symbol": "TEST"}
        ticker.get_income_stmt.return_value = pd.DataFrame({pd.Timestamp("2025-12-31"): [100.]}, index=["TotalRevenue"])
        with patch.object(information.importlib, "import_module", return_value=yf), patch.object(information, "yahoo_session", return_value=session):
            result = information.fetch("yahoo", {"operation": "history", "parameters": {"symbol": "TEST", "start": "2026-10-01", "end": "2026-10-14"}})
            financials = information.fetch("yahoo", {"operation": "income-statement", "parameters": {"symbol": "TEST"}})
        kwargs = ticker.history.call_args.kwargs
        self.assertFalse(kwargs["auto_adjust"])
        self.assertFalse(kwargs["back_adjust"])
        self.assertFalse(kwargs["repair"])
        self.assertEqual(result["data"]["metadata"]["currency"], "USD")
        self.assertTrue(result["data"]["end_exclusive"])
        self.assertIn("2025-12-31", financials["data"]["columns"][0])
        self.assertFalse(financials["data"]["original_disclosure_verified"])
        self.assertTrue(financials["data"]["original_disclosure_required"])

    def test_yahoo_missing_dependency_and_dates_are_safe(self):
        with patch.object(information.importlib, "import_module", side_effect=ModuleNotFoundError):
            with self.assertRaisesRegex(ValueError, "requirements-information"):
                information.fetch("yahoo", {"operation": "quote", "parameters": {"symbol": "TEST"}})
        with patch.object(information.importlib, "import_module", return_value=SimpleNamespace()):
            with self.assertRaisesRegex(ValueError, "end 必须晚于 start"):
                information.fetch("yahoo", {"operation": "history", "parameters": {"symbol": "TEST", "start": "2026-10-14", "end": "2026-10-01"}})
        session, ticker, yf = self.yahoo_fixture()
        ticker.get_info.side_effect = ValueError("untrusted remote body synthetic-cookie")
        with patch.object(information.importlib, "import_module", return_value=yf), patch.object(information, "yahoo_session", return_value=session):
            with self.assertRaisesRegex(ValueError, "Yahoo 调用失败") as caught:
                information.fetch("yahoo", {"operation": "quote", "parameters": {"symbol": "TEST"}})
        self.assertNotIn("synthetic-cookie", str(caught.exception))

    def test_yahoo_session_records_safe_json_and_blocks_retry_consent_and_redirect(self):
        class FixtureSession:
            def __init__(self, **kwargs):self.calls = []
            def request(self, method, url, *args, **kwargs):
                self.calls.append((method, url, kwargs))
                code = 429 if "limited" in url else 302 if "redirect" in url else 200
                return SimpleNamespace(status_code=code, content=b'{"price":25}', url=url)
        with patch.object(information.importlib, "import_module", return_value=SimpleNamespace(Session=FixtureSession)):
            session = information.yahoo_session()
        url = "https://query1.finance.yahoo.com/v7/finance/quote"
        session.request("GET", url, params={"symbols": "TEST", "crumb": "synthetic-cookie"})
        self.assertNotIn("synthetic-cookie", json.dumps(session.raw_responses))
        session.request("GET", url, params={"symbols": "TEST", "crumb": "changed-cookie"})
        self.assertEqual(len(session.calls), 1)
        with self.assertRaisesRegex(ValueError, "HTTP 429"):session.request("GET", url + "limited")
        with self.assertRaisesRegex(ValueError, "不自动重试"):session.request("GET", url + "limited")
        self.assertEqual(len(session.calls), 2)
        session.request("GET", url, params={"symbols": "DIFFERENT"})
        self.assertEqual(len(session.calls), 3)
        with self.assertRaisesRegex(ValueError, "白名单"):session.request("POST", "https://consent.yahoo.com/v2/collectConsent")
        with self.assertRaisesRegex(ValueError, "重定向"):session.request("GET", url + "redirect")
        self.assertTrue(all(not call[2]["allow_redirects"] for call in session.calls))

    def test_cost_readiness_known_zero_missing_credentials_and_dependency(self):
        spec = information.extension_specs()["tavily"]
        spec["enabled"] = True
        runtime = ExtensionRuntime({"tools": {"tavily": spec}})
        self.assertIn("cost_unknown", runtime.readiness("ext_tavily")["reasons"])
        self.assertNotIn("ext_tavily", runtime.permissions("finance"))
        self.assertNotIn("ext_tavily", runtime.contracts())
        with patch("yantra.extensions.run_command") as command:
            with self.assertRaisesRegex(ValueError, "未就绪"):asyncio.run(runtime.call("ext_tavily", {"query": "fixture"}, 1))
            command.assert_not_called()
        spec.update(cost_status="known", cost_source="controlled synthetic zero fee", cost_as_of="2026-10-14")
        ToolSpec.model_validate(spec)
        with patch.dict("os.environ", TAVILY_API_KEY="synthetic-key"):
            runtime = ExtensionRuntime({"tools": {"tavily": spec}})
            self.assertTrue(runtime.readiness("ext_tavily")["ready"])
            self.assertIn("ext_tavily", runtime.permissions("finance"))
            self.assertNotIn("ext_tavily", runtime.permissions("valuation"))
        yahoo = information.extension_specs()["yahoo"]
        with patch("importlib.util.find_spec", return_value=None):
            self.assertIn("dependency_missing:yfinance", ExtensionRuntime({"tools": {"yahoo": yahoo}}).readiness("ext_yahoo")["reasons"])
        spec.pop("cost_source")
        with self.assertRaisesRegex(ValueError, "零费用也需核实"):ToolSpec.model_validate(spec)

    def test_existing_canonical_command_provider_inference_preserves_availability(self):
        spec = information.extension_specs()["akshare"]
        runtime = ExtensionRuntime({"tools": {"akshare": spec}})
        self.assertEqual(runtime.bindings["ext_akshare"][0]["information_provider"], "akshare")
        self.assertIn("ext_akshare", runtime.permissions("finance"))

    def test_query_plan_binds_security_dates_and_separates_substantive_queries(self):
        research = {"company": "合成公司", "market": "CN", "security_code": "600001", "exchange": "SSE", "cutoff": "2026-10-14"}
        quote = information.query_arguments("tushare", "quote", research)[0]
        self.assertEqual(quote["parameters"]["ts_code"], "600001.SH")
        self.assertEqual(quote["parameters"]["end_date"], "20261014")
        statements = information.query_arguments("tushare", "financials", research)
        self.assertEqual({q["operation"] for q in statements}, {"income", "balancesheet", "cashflow"})
        query_a = information.query_arguments("tavily", "search", research | {"query": "原始公告", "max_results": 2})
        query_b = information.query_arguments("tavily", "search", research | {"query": "新风险公告", "max_results": 2})
        self.assertNotEqual(query_a, query_b)
        self.assertEqual(information.query_arguments("yahoo", "quote", {"company": "HK fixture", "market": "HK", "security_code": "00700"})[0]["parameters"]["symbol"], "0700.HK")
        self.assertEqual(information.query_arguments("yahoo", "quote", {"company": "unresolved"}), [])

    def test_tavily_results_enter_original_fetch_and_hash_snapshot_pipeline(self):
        response = {"provider": "tavily", "request": {"query": "原始财报"}, "retrieved_at": "2026-10-14T00:00:00Z", "data": {"results": [
            {"url": "https://fixture.invalid/report", "title": "合成财报", "content": "not numeric evidence", "published_date": "2026-10-13"},
            {"url": "https://fixture.invalid/future", "published_date": "2026-10-15"},
            {"url": "https://user:password@fixture.invalid/private"}], "request_id": "fixture-2", "usage": {"credits": 1}}}
        with tempfile.TemporaryDirectory() as directory:
            materials = RawMaterialAdapter.create(Path(directory) / "materials", "合成公司", [])
            pipeline = SourcePipeline(materials, cutoff="2026-10-14")
            with patch.object(materials, "fetch_raw", return_value={"content": b"original published 2026-10-13 revenue 100", "suffix": ".txt", "mime": "text/plain", "final_url": "https://fixture.invalid/report"}) as fetch:
                receipt = pipeline.ingest_information(response, "原始财报", "disclosure", 3)
            fetch.assert_called_once_with("https://fixture.invalid/report")
            self.assertEqual(receipt["results"][1]["status"], "excluded")
            snapshot = receipt["discovery_snapshots"][0]
            self.assertEqual(json.loads((materials.materials_dir / snapshot["raw_content_ref"]).read_bytes()), response)
            self.assertEqual(snapshot["evidence_kind"], "discovery")
            source = receipt["results"][0]
            self.assertNotEqual(source["source_hash"], snapshot["source_hash"])
            self.assertTrue(source["parsed"])
            self.assertTrue(snapshot["evidence_refs"])

    def test_api_identity_original_snapshot_does_not_require_artificial_page(self):
        response = {"provider": "tushare", "data": {"code": 0, "data": {"fields": ["ts_code", "name"], "items": [["600001.SH", "合成公司"]]}}}
        with tempfile.TemporaryDirectory() as directory:
            materials = RawMaterialAdapter.create(Path(directory) / "materials", "合成公司", [])
            with patch.object(materials, "fetch_raw", side_effect=AssertionError("API identity needs no artificial page")):
                receipt = materials.pipeline().ingest_information(response, "identity", "identity", 3)
            self.assertEqual(receipt["results"][0]["evidence_kind"], "source_api")
            self.assertEqual(receipt["results"][0]["status"], "fetched")
            self.assertTrue(receipt["results"][0]["evidence_refs"])

    def test_empty_identity_api_preserves_snapshot_and_continues_public_acquisition(self):
        cases = [("tushare", {"code": 0, "data": {"fields": ["ts_code", "name"], "items": []}}),
            ("akshare", {"records": [], "columns": ["item", "value"], "total_rows": 0}),
            ("fuyao", {"code": 0, "data": []}), ("fuyao", {"code": 0, "data": {}}),
            ("fuyao", {"code": 0, "data": {"items": [], "total": 0}})]
        for provider, data in cases:
            with self.subTest(provider=provider, data=data), tempfile.TemporaryDirectory() as directory:
                materials = RawMaterialAdapter.create(Path(directory) / "materials", "合成公司", [])
                response = {"provider": provider, "data": data}
                receipt = materials.pipeline().ingest_information(response, "company identity", "identity", 3)
                self.assertEqual(receipt["results"], [])
                self.assertEqual(receipt["errors"], ["empty_identity_results"])
                snapshot = receipt["discovery_snapshots"][0]
                self.assertEqual(json.loads((materials.materials_dir / snapshot["raw_content_ref"]).read_bytes()), response)
                with patch("yantra.sources.public_search", return_value=[{"href": "https://fixture.invalid/identity", "title": "company original"}]) as search, \
                        patch.object(materials, "fetch_raw", return_value={"content": b"synthetic company 600001.SH identity", "suffix": ".txt", "mime": "text/plain", "final_url": "https://fixture.invalid/identity"}) as fetch:
                    # The controller uses this same no-usable-result condition.
                    if not receipt["results"]:receipt = materials.search("company identity", "identity", 3)
                search.assert_called_once()
                fetch.assert_called_once_with("https://fixture.invalid/identity")
                self.assertEqual(receipt["results"][0]["status"], "fetched")

    def test_empty_primary_can_fall_back_without_repeating_failed_search(self):
        with tempfile.TemporaryDirectory() as directory:
            materials = RawMaterialAdapter.create(Path(directory) / "materials", "合成公司", [])
            first = materials.pipeline().ingest_information({"provider": "tavily", "data": {"results": []}}, "first query", "disclosure", 2)
            self.assertEqual(first["results"], [])
            with patch("yantra.sources.public_search", return_value=[{"href": "https://fixture.invalid/report", "title": "原文"}]) as search, \
                    patch.object(materials, "fetch_raw", return_value={"content": b"original source", "suffix": ".txt", "mime": "text/plain", "final_url": "https://fixture.invalid/report"}):
                fallback = materials.search("first query", "disclosure", 2)
            self.assertEqual(search.call_count, 1)
            self.assertEqual(fallback["results"][0]["status"], "fetched")
            with patch("yantra.sources.public_search", side_effect=ValueError("synthetic-token")) as search:
                result = materials.search("distinct risk query", "counter_evidence", 2)
            self.assertEqual(search.call_count, 1)
            self.assertEqual(result["errors"], ["ValueError"])
            self.assertNotIn("synthetic-token", json.dumps(result))

    def test_new_flow_http_failure_never_retries_same_url_with_other_client(self):
        original = httpx.Client
        for status in (401, 402, 403, 429, 502):
            calls = []
            def respond(request):
                calls.append(str(request.url))
                return httpx.Response(status, content=b"untrusted body synthetic-token")
            transport = httpx.MockTransport(respond)
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                materials = RawMaterialAdapter.create(Path(directory) / "materials", "合成公司", [])
                materials.no_retry = True
                with patch("yantra.materials.httpx.Client", side_effect=lambda **kwargs: original(transport=transport, **kwargs)), \
                        patch("primp.Client", side_effect=AssertionError("same-source fallback forbidden")) as alternate:
                    with self.assertRaisesRegex(ValueError, "其他公开来源") as caught:
                        materials.fetch_raw("https://fixture.invalid/report")
                alternate.assert_not_called()
                self.assertEqual(calls, ["https://fixture.invalid/report"])
                self.assertNotIn("synthetic-token", str(caught.exception))

    def test_login_page_is_a_gap_without_form_submission(self):
        original = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200,
            headers={"content-type": "text/html"}, content=b'<form><input type="password"></form>'))
        with tempfile.TemporaryDirectory() as directory:
            materials = RawMaterialAdapter.create(Path(directory) / "materials", "合成公司", [])
            with patch("yantra.materials.httpx.Client", side_effect=lambda **kwargs: original(transport=transport, **kwargs)):
                with self.assertRaisesRegex(ValueError, "需要登录"):
                    materials.fetch_raw("https://fixture.invalid/login")


if __name__ == "__main__":
    unittest.main()
