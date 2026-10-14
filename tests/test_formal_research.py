"""Name-only formal research through ADK with SYNTHETIC sources and decisions.

No credentials, providers or model endpoints are contacted. These fixtures test
the observable execution flow, not live coverage or investment-model quality.
"""
import copy
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from examples.v022.synthetic import PRICE, TEXT, SyntheticBackend
from yantra.adk_app import run_engine
from yantra.budget import BudgetExceeded
from yantra.configuration import ConfigStore
from yantra.extensions import ToolSpec
from yantra.information import extension_specs, normalized_quote
from yantra.knowledge.preferences import Preferences
from yantra.knowledge.store import KnowledgeStore
from yantra.research import RawEngine


IDENTITIES = [{"legal_name": "合成公司", "market": "CN", "exchange": "SSE", "security_code": "600000",
               "trading_currency": "CNY", "share_class": "A"}]


def identity_text(identities):
    return "\n".join("SYNTHETIC ONLY " + " ".join(row.values()) for row in identities)


class AcquiredBackend(SyntheticBackend):
    def __init__(self, identities=None, insufficient=False, expire_after_finance=False):
        super().__init__()
        self.identities = copy.deepcopy(identities or IDENTITIES)
        self.insufficient, self.expire_after_finance = insufficient, expire_after_finance

    async def identity(self, payload):
        candidates = []
        for row in self.identities:
            quote = identity_text([row])
            for document in payload["sources"]:
                anchor = next((a for a in document["lines"] if quote in a["text"]), None)
                if anchor:
                    candidates.append(row | {"source_ref": anchor["ref"], "quote": quote})
                    break
        return {"candidates": candidates, "gaps": []}

    async def extract(self, payload):
        if not any("eps 2 bvps 8" in row["text"] for row in payload["document"]["anchors"]):
            return {"financials": [], "evidence": [], "gaps": []}
        result = await super().extract(payload)
        if self.insufficient:
            result["financials"] = []
            result["gaps"] = ["SYNTHETIC：取得竞争披露，缺少可核对的财务关键依据"]
        return result

    def proposal(self, finance):
        if not finance.get("financials"):
            return {"model": "not_applicable", "parameters": {}, "basis": {},
                    "scope": "缺少财务关键依据，保留已取得的经营证据", "gaps": ["财务关键依据不足"]}
        return super().proposal(finance) | {"safety_margin": {"ratio": .25,
            "rationale": "SYNTHETIC：竞争与预测误差需要余量；比例由受控决策选择", "benchmark": "base_per_share_value",
            "evidence_refs": [finance["financials"][0]["source_ref"]]}}

    async def main(self, view):
        if self.expire_after_finance and view["artifacts"].get("finance"):
            self.engine.state["deadline"] = "2000-01-01T00:00:00+00:00"
        return await super().main(view)

    async def worker(self, task, local):
        if task["target"] == "finance":
            extracted = next((o["response"]["data"] for o in local["tool_outputs"]
                if o["tool"] == "extract_financials" and o["response"]["ok"]), None)
            if extracted is not None and not extracted["financials"]:
                return self._result(task, local, status="partial", data={"use_receipts": True},
                                    unresolved=["财务关键依据不足，保留已取得的经营证据"])
        return await super().worker(task, local)


class FormalResearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "knowledge"
        self.searches = []
        for target in ("socket.create_connection", "socket.socket.connect", "httpx.HTTPTransport.handle_request",
                       "httpx.AsyncHTTPTransport.handle_async_request", "yantra.sources.public_search", "primp.Client.get"):
            blocker = patch(target, side_effect=AssertionError("unmocked external network forbidden"))
            blocker.start()
            self.addCleanup(blocker.stop)
        # The SDK itself is replaced by an AsyncMock response below. Its absence
        # cannot masquerade as a live dependency/coverage check.
        original_find_spec = importlib.util.find_spec
        dependency = patch("importlib.util.find_spec", side_effect=lambda name, *a, **kw:
            SimpleNamespace(name="synthetic-yfinance") if name == "yfinance" else original_find_spec(name, *a, **kw))
        dependency.start()
        self.addCleanup(dependency.stop)
        clean_environment = patch.dict(os.environ, {"FUYAO_API_KEY": "synthetic-test-key", "TAVILY_API_KEY": "synthetic-test-key"}, clear=True)
        clean_environment.start()
        self.addCleanup(clean_environment.stop)

    def create(self, backend=None, providers=("yahoo",), roles=None, cost=0, research=None):
        settings = ConfigStore(self.root / ("settings-" + str(len(list(self.root.glob("settings-*")))) + ".json"))
        for i, provider in enumerate(providers):
            spec = extension_specs()[provider] | {"enabled": True, "cost_status": "known", "cost_cny": cost,
                "cost_source": "SYNTHETIC ONLY offline fee bound", "cost_as_of": "2026-10-13",
                "roles": roles or ["finance", "thesis", "verification"]}
            settings.update(lambda config, spec=spec, i=i, provider=provider:
                config["tools"].__setitem__(str(i) + "-" + provider, ToolSpec.model_validate(spec).model_dump()))
        selected = backend or AcquiredBackend()
        engine = RawEngine.create_raw(self.root / "runs", research or {"company": "合成公司", "horizon": "12个月", "cutoff": "2026-10-13"},
            [], backend=selected, pricing=PRICE, knowledge_dir=self.library, settings=settings, framework="quality")
        selected.engine = engine
        self.assertEqual(engine.state["input_snapshot"]["manifest"]["materials"], [])
        return engine

    def public_search(self, adapter, query, purpose, max_results):
        self.searches.append((query, purpose))
        content = identity_text(self.identities) if purpose == "identity" else TEXT if purpose == "disclosure" else None
        source = adapter.add(content.encode(), ".txt", "SYNTHETIC public original", disclosure_date="2026-10-12") if content else None
        return {"query": query, "purpose": purpose, "acquired_at": "2026-10-13T16:00:00+08:00", "service_cost_cny": 0,
                "results": [{"status": "fetched", "source_id": source["id"]}] if source else [], "errors": []}

    def response(self, name, arguments, timeout, price=15, fail_first=False):
        if fail_first and name.endswith("fuyao"):
            raise ValueError("SYNTHETIC first source failure; remote details suppressed")
        if name.endswith("tavily"):
            return {"provider": "tavily", "request": arguments, "retrieved_at": "2026-10-13T16:00:00+08:00",
                "data": {"results": [{"url": "https://synthetic.invalid/disclosure.txt", "title": "SYNTHETIC original",
                    "published_date": "2026-10-12", "content": "SYNTHETIC search clue only"}]}}
        result = {"provider": "yahoo", "request": arguments, "retrieved_at": "2026-10-13T16:00:00+08:00",
                  "data": {"SYNTHETIC_ONLY": True, "value": 999, "original_disclosure_required": True}}
        if arguments.get("operation") == "quote" and price is not None:
            result["data"] = {"info": {"symbol": "600000.SS", "regularMarketPrice": price,
                "regularMarketTime": datetime(2026, 10, 13, 7, tzinfo=timezone.utc).timestamp(), "currency": "CNY"}}
            result["quote"] = normalized_quote("yahoo", arguments, result["data"])
        return result

    async def run_fixture(self, engine, price=15, fail_first=False):
        self.identities = engine.backend.identities
        call = AsyncMock(side_effect=lambda name, arguments, timeout:
            self.response(name, arguments, timeout, price=price, fail_first=fail_first))
        with patch.object(engine.extensions, "call", call), patch("yantra.materials.RawMaterialAdapter.search",
                lambda adapter, query, purpose, maximum: self.public_search(adapter, query, purpose, maximum)):
            state = await run_engine(engine)
        return state, call

    async def test_company_name_only_gets_archived_sources_and_three_deterministic_answers(self):
        for price, expected in ((15, "值得买"), (35, "暂不值得买"), (None, "目前无法判断")):
            with self.subTest(price=price):
                engine = self.create()
                state, call = await self.run_fixture(engine, price)
                answer = state["report"]["投资结论摘要"]["buy_judgment"]
                self.assertEqual(answer["label"], expected, answer["gaps"])
                self.assertEqual(state["research"]["security_code"], "600000")
                self.assertEqual(state["assumptions"]["authorization_source"], "default_policy")
                self.assertFalse(state["assumptions"]["confirmed"])
                self.assertEqual(Preferences(KnowledgeStore(self.library)).list(history=True), [])
                self.assertEqual(answer["safety_margin"]["buy_price_ceiling"], 22.5)
                self.assertEqual(len(answer["scenarios"]), 3)
                self.assertTrue(all(ref in state["calculations"] for ref in answer["calculation_refs"]))
                self.assertTrue(state["artifacts"]["verification"]["data"]["passed"], state["issues"])
                rows = state["artifacts"]["finance"]["data"]["financials"]
                self.assertNotIn(999, [row["value"] for row in rows])
                self.assertTrue(any("SYNTHETIC ONLY" in pair[0]["text"] for pair in engine.tools.anchors().values()))
                self.assertGreater(call.await_count, 0)
                self.assertTrue(Path(state["report_path"]).exists())
                preparation_tasks = [t for t in state["tasks"] if t["question"].startswith("获取")]
                self.assertTrue(preparation_tasks)
                self.assertTrue(all(any(r["task_id"] == t["task_id"] for r in state["results"]) for t in preparation_tasks))
                self.assertIn(expected, Path(state["report_path"]).read_text().splitlines()[2])

    async def test_first_source_failure_uses_second_without_retry_or_losing_receipts(self):
        engine = self.create(providers=("fuyao", "yahoo"))
        state, call = await self.run_fixture(engine, fail_first=True)
        attempts = [a for a in state["information_attempts"] if a["purpose"] == "quote"]
        self.assertEqual([row["status"] for row in attempts], ["failed", "acquired"])
        self.assertEqual(state["report"]["投资结论摘要"]["buy_judgment"]["label"], "值得买")
        self.assertEqual(len([row for row in call.await_args_list if row.args[0].endswith("fuyao") and row.args[1]["operation"] == "prices-snapshot"]), 1)
        self.assertTrue(state["model_ledger"])
        self.assertEqual(state["quote_history"][0]["source_hash"], state["quote"]["source_hash"])

    async def test_ambiguous_company_or_market_waits_then_resumes_same_run(self):
        alternatives = (IDENTITIES + [IDENTITIES[0] | {"legal_name": "另一合成公司", "security_code": "600001"}],
                        IDENTITIES + [IDENTITIES[0] | {"market": "HK", "exchange": "HKEX", "security_code": "00005",
                            "trading_currency": "HKD", "share_class": "ordinary"}])
        for options in alternatives:
            backend = AcquiredBackend(options)
            engine = self.create(backend)
            self.identities = options
            call = AsyncMock(side_effect=self.response)
            with patch.object(engine.extensions, "call", call), patch("yantra.materials.RawMaterialAdapter.search",
                    lambda adapter, query, purpose, maximum: self.public_search(adapter, query, purpose, maximum)):
                waiting = await run_engine(engine)
                self.assertEqual(waiting["status"], "waiting")
                self.assertEqual(waiting["pending_user"]["kind"], "identity")
                self.assertEqual(len(waiting["pending_user"]["candidates"]), 2)
                self.assertNotIn("valuation", waiting["artifacts"])
                run_id, deadline = waiting["run_id"], waiting["deadline"]
                calls = waiting["budget"]["model_calls"]
                final = await engine.select_identity("security-1")
            self.assertEqual(final["run_id"], run_id)
            self.assertEqual(final["deadline"], deadline)
            self.assertGreater(final["budget"]["model_calls"], calls)
            self.assertEqual(final["report"]["投资结论摘要"]["buy_judgment"]["label"], "值得买")

    async def test_credentials_role_and_shared_budget_rejections_do_not_call_provider(self):
        identity = {"company": "合成公司", "horizon": "12个月", "cutoff": "2026-10-13"} | IDENTITIES[0]
        identity.pop("legal_name")
        with patch.dict(os.environ, {}, clear=True):
            engine = self.create(providers=("fuyao",), research=identity)
            with patch.object(engine.extensions, "call", new_callable=AsyncMock) as call:
                await engine._information("quote")
                call.assert_not_awaited()
                self.assertIn("credential_missing:FUYAO_API_KEY", engine.state["information_attempts"][0]["reasons"])
        engine = self.create(roles=["thesis"], research=identity)
        with patch.object(engine.extensions, "call", new_callable=AsyncMock) as call:
            await engine._information("quote")
            call.assert_not_awaited()
        engine = self.create(cost=.2, research=identity)
        engine.state["limits"]["max_cost_cny"] = .1
        with patch.object(engine.extensions, "call", new_callable=AsyncMock) as call:
            with self.assertRaises(BudgetExceeded):
                await engine._information("quote")
            call.assert_not_awaited()
            self.assertEqual(engine.state["budget"]["external_service_cost_cny"], 0)

    async def test_inapplicable_quote_cannot_stop_appropriate_fallback(self):
        identity = {"company": "合成公司", "horizon": "12个月", "cutoff": "2026-10-13"} | IDENTITIES[0]
        identity.pop("legal_name")
        for mismatch in ({"security_code": "600001"}, {"currency": "HKD"}, {"adjustment": "back_adjusted"},
                         {"price_time": "2026-10-14T07:00:00+00:00"}):
            with self.subTest(mismatch=mismatch):
                engine = self.create(providers=("fuyao", "yahoo"), research=identity)

                def response(name, arguments, timeout):
                    if name.endswith("fuyao"):
                        value = self.response("ext_1-yahoo", {"operation": "quote", "parameters": {"symbol": "600000.SS"}}, timeout)
                        value["quote"].update(mismatch)
                        return value | {"provider": "fuyao", "request": arguments}
                    return self.response(name, arguments, timeout)

                with patch.object(engine.extensions, "call", AsyncMock(side_effect=response)):
                    await engine._information("quote")
                attempts = engine.state["information_attempts"]
                self.assertEqual([row["status"] for row in attempts], ["inapplicable", "acquired"])
                self.assertEqual(engine.state["quote"]["security_code"], "600000")
                self.assertEqual(engine.state["quote"]["currency"], "CNY")
                self.assertEqual(engine.state["quote"]["adjustment"], "unadjusted")
                self.assertEqual(len(engine.state["quote_history"]), 2)

    async def test_insufficient_and_expired_runs_keep_substantive_analysis(self):
        for backend in (AcquiredBackend(insufficient=True), AcquiredBackend(expire_after_finance=True)):
            engine = self.create(backend)
            state, _ = await self.run_fixture(engine)
            self.assertEqual(state["status"], "partial", state["stop_reasons"])
            answer = state["report"]["投资结论摘要"]["buy_judgment"]
            self.assertEqual(answer["label"], "目前无法判断")
            self.assertTrue(answer["gaps"])
            self.assertTrue(state["artifacts"]["finance"]["data"]["evidence"])
            self.assertEqual(answer["quote"]["price"], 15)
            self.assertTrue(state["model_ledger"])
            self.assertIn("管理层", Path(state["report_path"]).read_text())
            if backend.expire_after_finance:
                self.assertTrue(any("截止" in reason for reason in state["stop_reasons"]))
                self.assertTrue(state["calculations"])

    async def test_tavily_search_flows_through_original_web_acquisition(self):
        engine = self.create(providers=("tavily", "yahoo"))
        original = (identity_text(IDENTITIES) + "\n" + TEXT).encode()
        fetched = []

        def fetch(adapter, url):
            adapter.check()
            fetched.append(url)
            return {"content": original, "suffix": ".txt", "mime": "text/plain", "final_url": url}

        call = AsyncMock(side_effect=self.response)
        with patch.object(engine.extensions, "call", call), patch("yantra.materials.RawMaterialAdapter.fetch_raw", fetch):
            state = await run_engine(engine)
        self.assertEqual(state["report"]["投资结论摘要"]["buy_judgment"]["label"], "值得买",
                         state["report"]["投资结论摘要"]["buy_judgment"]["gaps"])
        self.assertTrue(fetched)
        self.assertTrue(any(row.get("adapter_id") == "tavily" for row in state["searches"]))
        sources = state["input_snapshot"]["manifest"]["materials"]
        self.assertTrue(any(acquisition.get("evidence_kind") == "discovery" for row in sources for acquisition in row["acquisitions"]))
        self.assertTrue(any(row.get("url") == "https://synthetic.invalid/disclosure.txt" for row in sources))
        finance = state["artifacts"]["finance"]["data"]["financials"]
        self.assertTrue(all(engine.tools.anchors()[row["source_ref"]][1]["url"] == "https://synthetic.invalid/disclosure.txt" for row in finance))


if __name__ == "__main__":
    unittest.main()
