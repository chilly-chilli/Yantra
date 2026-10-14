"""New fictional v0.3 fixtures only; storage/retrieval/verification boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from yantra.budget import RunBudget, deadline, BudgetExceeded
from yantra.configuration import ConfigStore
from yantra.knowledge import KnowledgeStore, KnowledgeService
from yantra.knowledge.cli import explicit_budget
from yantra.knowledge.embedding import Embedder
from yantra.knowledge.preferences import Preferences
from yantra.knowledge.schemas import KnowledgeQuery
from yantra.knowledge.store import VersionConflict
from yantra.materials import RawMaterialAdapter
from yantra.models import ModelCatalog
from yantra.sources import Candidate, DiscoveryResult, FetchResult, SourcePipeline


PRICE = {"input_per_million":2,"output_per_million":0,"source":"fictional-test-price","as_of":"2026-10-13"}


class KnowledgeFixture(unittest.TestCase):
    def setUp(self):
        if self._testMethodName == "test_two_sessions_reuse_verified_fact_not_historical_conclusion":
            # This historical-cutoff fixture records knowledge before that cutoff.
            clock = patch("yantra.knowledge.store.now", return_value="2026-10-13T00:00:00+00:00")
            clock.start(); self.addCleanup(clock.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = KnowledgeStore(self.root/"knowledge")
        self.company,self.security = self.store.identity({"company":"虚构汽车集团","market":"CN","exchange":"SSE","security_code":"600001","share_class":"A"},"first")
        self.adapter = RawMaterialAdapter.create(self.root/"first"/"materials","虚构汽车集团",[])
        self.content = "明确标识的虚构夹具 2026-01-01披露 FY2025 CNY million\n集团毛利率 10 汽车业务毛利率 20 汽车收入 1000；竞争风险\n集团毛利率下降至 8 汽车业务改善至 25 收入 1200".encode()
        self.source = self.adapter.add(self.content,".txt","fictional-fixture",disclosure_date="2026-01-01")
        self.store.archive_source(self.source,self.content,"first")
        self.store.register_reviewer("independent-reviewer","first")
        self.service = KnowledgeService(self.store,"first",{"cutoff":"2026-10-13","knowledge_security_id":self.security})
        self.ref = {"source_id":self.source["id"],"source_hash":self.source["source_hash"],"anchor_ref":self.source["chunks"][1]["ref"],"locator":self.source["chunks"][1]["location"]}

    def item(self, kind="fact", value=10, period="FY2025", scope="consolidated", **changes):
        item = {"company_id":self.company,"security_id":self.security,"kind":kind,"period":period,"disclosed_at":"2026-01-01","formed_at":"2026-01-02","topics":["汽车","盈利质量"],"evidence":[self.ref],
                "content":{"field":"gross_margin","value":value,"raw_value":str(value),"period":period,"currency":"CNY","unit":"ratio","consolidated":True,"security_code":"600001","share_class":"A","accounting_scope":scope,"statement":"集团毛利率","source_ref":self.ref["anchor_ref"],"source_hash":self.ref["source_hash"],"disclosure_date":"2026-01-01","information_kind":"fact"},"retention":{"R1":"检验虚构集团利润率论点"}}
        if kind != "fact":
            item["content"] = {"statement":"汽车业务扩大将改善集团毛利率","metric_scope":"group"}
            item["assumptions"] = ["其他业务利润稳定"]
            item["invalidation_conditions"] = ["集团毛利率持续下降且不能解释"]
        item.update(changes)
        return item

    def propose(self,item=None,entry=None,session="first"):
        return self.store.propose(item or self.item(),session,"researcher",self.store.version(self.company),entry)

    def reviewed(self,proposal,passed=True):
        self.service.review({"proposal_id":proposal["id"],"content_hash":proposal["hash"],"passed":passed,"reasons":["已逐项核对虚构原文、口径及反证" if passed else "虚构反例否决"]},"independent-reviewer","verification")

    def save(self,item=None):
        p=self.propose(item)
        self.reviewed(p)
        return self.store.commit(p["id"])


class KnowledgeTests(KnowledgeFixture):
    def test_permanent_delete_interruption_recovers_authorized_cleanup(self):
        self.save()
        impact=self.store.deletion_impact(self.source["id"])
        with patch("pathlib.Path.unlink",side_effect=OSError("fictional deletion interruption")):
            with self.assertRaises(OSError):self.store.delete_source(self.source["id"],True,impact["confirmation"])
        self.assertTrue(self.store.blob_path(self.source["source_hash"]).exists())
        recovered=KnowledgeStore(self.store.root)
        self.assertFalse(recovered.blob_path(self.source["source_hash"]).exists())
        with recovered.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM deletion_journal WHERE status='pending'").fetchone()[0],0)

    def test_business_and_group_metrics_remain_distinct(self):
        from yantra.v2_tools import RawToolRegistry
        tools=RawToolRegistry(self.adapter.materials_dir)
        tools.research_context={"security_code":"600001"}
        rows=[]
        for business,profit in ((None,10),("automotive",20)):
            for field,value in (("revenue",1000),("net_profit",profit)):
                rows.append({"field":field,"value":value,"raw_value":str(value),"period":"FY2025","unit":"million","currency":"CNY","consolidated":True,"security_code":"600001","share_class":"A","accounting_scope":"consolidated","business_scope":business,"source_ref":self.ref["anchor_ref"],"source_hash":self.ref["source_hash"],"disclosure_date":"2026-01-01"})
        accepted=tools.accept_extraction({"financials":rows,"evidence":[],"gaps":[]},"2026-10-13")
        self.assertEqual(accepted["conflicts"],[])
        result=tools._calculate_metrics({"records":accepted["financials"]}).data
        self.assertEqual(len(result["periods"]),2)
        self.assertEqual(result["net_margin"],.01)
        business=next(v for v in result["periods"].values() if v.get("business_scope"))
        self.assertEqual(business["net_margin"],.02)

    def test_correction_relation_and_independent_index_failure(self):
        original=self.save()
        proposal=self.propose(self.item(relations={"correction_of":[original["id"]]}))
        self.reviewed(proposal)
        with patch.object(self.store,"index_revision",side_effect=__import__("sqlite3").OperationalError("derived index failed")):
            corrected=self.store.commit(proposal["id"])
        self.assertEqual(self.store.read(corrected["entry_id"])["verification"]["status"],"passed")
        self.assertFalse(self.service.applicability(original)["reusable_fact"])
        self.store.rebuild_fts()
        self.assertEqual(len(self.service.search({"query":"毛利率"})["results"]),2)

    def test_lock_wait_consumes_original_deadline(self):
        import fcntl
        with (self.store.root/"commit.lock").open("a") as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            started=time.monotonic()
            def check():
                if time.monotonic()-started>.06:
                    raise BudgetExceeded("fixture original deadline")
            self.store.check=check
            with self.assertRaises(BudgetExceeded):
                with self.store.transaction():pass
            self.assertLess(time.monotonic()-started,.3)

    def test_business_scope_cannot_directly_confirm_group_thesis(self):
        original=self.save(self.item("thesis",judgment="supported"))
        changed=self.item("thesis",judgment="supported")
        changed["content"]["metric_scope"]="automotive-business"
        proposal=self.propose(changed,original["entry_id"])
        with self.assertRaisesRegex(ValueError,"different metric scope"):
            self.reviewed(proposal)
        self.assertEqual(self.store.read(original["entry_id"])["item"]["content"]["metric_scope"],"group")

    def test_two_sessions_reuse_verified_fact_not_historical_conclusion(self):
        fact=self.save()
        thesis=self.save(self.item("thesis",judgment="supported"))
        second=KnowledgeService(self.store,"second",{"cutoff":"2026-10-13","knowledge_security_id":self.security})
        results=second.search({"query":"毛利率","company_ids":[self.company]})["results"]
        self.assertEqual(len(results),2)
        self.assertTrue(next(r for r in results if r["entry_id"]==fact["entry_id"])["applicability"]["reusable_fact"])
        self.assertFalse(next(r for r in results if r["entry_id"]==thesis["entry_id"])["applicability"]["formal_current_conclusion"])
        second.read(fact["entry_id"],fact["id"],adopt=True)
        with self.assertRaises(ValueError):second.read(thesis["entry_id"],thesis["id"],adopt=True)

    def test_snapshot_precedes_comparison_and_revision_preserves_history(self):
        original=self.save(self.item("thesis",judgment="supported"))
        with self.assertRaises(ValueError):self.store.compare("missing",[])
        snapshot=self.store.snapshot("second",self.company,{"statement":"集团利润率下降，汽车业务改善"},[self.ref])
        with self.store.connect() as db:frozen=dict(db.execute("SELECT * FROM snapshots WHERE id=?",(snapshot,)).fetchone())
        self.store.compare(snapshot,[{"relation":"scope_changed","reason":"业务与集团分开核查"}])
        update=self.propose(self.item("thesis",judgment="weakened",reason="补查一次性损失后缩小范围"),original["entry_id"])
        with self.assertRaises(PermissionError):self.store.commit(update["id"])
        self.reviewed(update)
        revised=self.store.commit(update["id"])
        self.assertEqual(revised["supersedes"],original["id"])
        self.assertEqual(self.store.read(original["entry_id"],original["id"])["item"]["judgment"],"supported")
        with self.store.connect() as db:self.assertEqual(dict(db.execute("SELECT * FROM snapshots WHERE id=?",(snapshot,)).fetchone()),frozen)

    def test_thesis_states_and_rejected_proposal_keep_review_records(self):
        first=self.save(self.item("thesis",judgment="open"))
        previous=first
        for state in ("supported","weakened","refuted","unresolved","closed"):
            proposal=self.propose(self.item("thesis",judgment=state,reason="有来源的虚构论点变更 "+state),first["entry_id"])
            self.reviewed(proposal)
            current=self.store.commit(proposal["id"])
            self.assertEqual(current["verification"]["status"],"passed")
            self.assertEqual(current["supersedes"],previous["id"])
            previous=current
        rejected=self.propose(self.item("thesis",judgment="supported"),first["entry_id"])
        self.reviewed(rejected,False)
        with self.assertRaises(PermissionError):self.store.commit(rejected["id"])
        self.assertEqual(self.store.read(first["entry_id"])["item"]["judgment"],"closed")

    def test_cross_session_conflict_and_distinct_period_scope(self):
        self.save()
        # Another source proves a competing same-period candidate; latest is not chosen.
        source=self.adapter.add(b"fictional 2026-01-02 published margin 12",".txt","second-channel",disclosure_date="2026-01-02")
        self.store.archive_source(source,(self.adapter.materials_dir/source["file"]).read_bytes(),"first")
        ref={"source_id":source["id"],"source_hash":source["source_hash"],"anchor_ref":source["chunks"][0]["ref"],"locator":source["chunks"][0]["location"]}
        self.save(self.item(value=12,evidence=[ref]))
        rows=self.service.search({"query":"毛利率","kinds":["fact"],"period":"FY2025"})["results"]
        self.assertEqual({r["item"]["content"]["value"] for r in rows},{10,12})
        self.assertTrue(all(not r["applicability"]["reusable_fact"] for r in rows))
        different=self.save(self.item(value=20,period="FY2024",scope="parent"))
        self.assertTrue(self.service.applicability(different)["reusable_fact"])

    def test_historical_cutoff_excludes_future_evidence_and_posthoc_thesis(self):
        fact=self.save()
        with patch("yantra.knowledge.store.now",return_value="2026-01-02T00:00:00Z"):
            old=self.save(self.item("thesis",formed_at="2026-01-02"))
        new=self.propose(self.item("thesis",formed_at="2026-10-13",judgment="refuted"),old["entry_id"])
        self.reviewed(new);self.store.commit(new["id"])
        before=self.service.search({"query":"毛利率","cutoff":"2026-02-01"})["results"]
        self.assertEqual(next(r for r in before if r["entry_id"]==old["entry_id"])["id"],old["id"])
        self.assertFalse(self.service.search({"query":"毛利率","cutoff":"2025-12-31"})["results"])
        source=copy.deepcopy(self.source);source["id"]="source-undated";source["date"]=None
        self.store.archive_source(source,self.content,"first")
        ref=self.ref|{"source_id":"source-undated"}
        self.save(self.item(evidence=[ref]))
        result=self.service.search({"query":"毛利率","cutoff":"2026-02-01"})["results"]
        self.assertTrue(all(r["item"]["evidence"][0]["source_id"]!="source-undated" for r in result))

    def test_chinese_short_words_return_original_locator_and_hard_filters(self):
        fact=self.save()
        for term in ("毛利率","汽车","利"):
            rows=self.service.search({"query":term,"company_ids":[self.company],"security_id":self.security})["results"]
            self.assertEqual(rows[0]["item"]["evidence"][0]["locator"],{"line":2})
            self.assertEqual(rows[0]["item"]["evidence"][0]["source_hash"],self.source["source_hash"])
        self.assertFalse(self.service.search({"query":"毛利率","period":"FY1999"})["results"])
        self.assertFalse(self.service.search({"query":"毛利率","company_ids":["unrelated"]})["results"])

    def test_permissions_veto_and_content_hash_binding(self):
        p=self.propose()
        with self.assertRaises(PermissionError):self.service.review({"proposal_id":p["id"],"content_hash":p["hash"],"passed":True,"reasons":["self"]},"researcher","finance")
        with self.assertRaises(ValueError):self.service.review({"proposal_id":p["id"],"content_hash":"tampered","passed":True,"reasons":["bad"]},"independent-reviewer","verification")
        self.reviewed(p,False)
        self.reviewed(p,True)
        archived=self.store.commit(p["id"],allow_pending=True)
        self.assertEqual(archived["verification"]["status"],"rejected")

    def test_archive_pending_and_partial_fact_qualification_are_separate(self):
        p=self.propose();record=self.store.commit(p["id"],allow_pending=True)
        self.store.log("session_archived","first",{"status":"partial"})
        self.assertEqual(self.store.read(record["entry_id"])["verification"]["status"],"pending")
        self.reviewed(p)
        self.assertTrue(self.service.applicability(self.store.read(record["entry_id"]))["reusable_fact"])

    def test_manual_edit_revokes_verification_and_old_pin_survives(self):
        old=self.save()
        self.service.read(old["entry_id"],old["id"],adopt=True)
        revised=self.store.edit_fact(old["entry_id"],{"value":12,"raw_value":"12"},"人工更正无原文依据")
        self.assertEqual(revised["verification"]["status"],"pending")
        self.assertTrue(revised["item"]["manual"])
        self.assertEqual(self.service.read(old["entry_id"],old["id"])["item"]["content"]["value"],10)
        same=self.store.edit_fact(old["entry_id"],{"statement":"集团毛利率  "},"排版空白")
        self.assertEqual(same["id"],revised["id"])
        with self.assertRaises(ValueError):self.store.edit_fact(old["entry_id"],{"verification":"passed"},"绕过核验")

    def test_deletion_soft_pin_session_unlink_and_permanent_impact(self):
        old=self.save();self.service.read(old["entry_id"],old["id"])
        self.store.archive_source(self.source,self.content,"second")
        self.store.delete_source(self.source["id"],session="second")
        self.assertTrue(self.service.search({"query":"毛利率"})["results"])
        self.store.delete_source(self.source["id"])
        self.assertFalse(self.service.search({"query":"毛利率"})["results"])
        self.assertEqual(self.service.read(old["entry_id"],old["id"])["item"]["content"]["value"],10)
        impact=self.store.deletion_impact(self.source["id"])
        self.assertEqual(set(impact["sessions"]),{"first","second"})
        with self.assertRaises(ValueError):self.store.delete_source(self.source["id"],True,"wrong")
        other=self.adapter.add(b"unrelated fictional bytes",".txt","unrelated")
        self.store.archive_source(other,b"unrelated fictional bytes","third")
        self.store.delete_source(self.source["id"],True,impact["confirmation"])
        with self.assertRaises(ValueError):self.service.read(old["entry_id"],old["id"])
        self.assertFalse(self.store.blob_path(self.source["source_hash"]).exists())
        self.assertEqual(self.store.source(other["id"])[1].read_bytes(),b"unrelated fictional bytes")

    def test_company_security_identity_does_not_merge_names_or_share_classes(self):
        company,security=self.store.identity({"company":"虚构汽车集团","market":"HK","exchange":"HKEX","security_code":"00001","share_class":"H"},"second")
        self.assertNotEqual(company,self.company);self.assertNotEqual(security,self.security)
        explicit,new=self.store.identity({"company":"虚构汽车集团","company_id":self.company,"market":"HK","exchange":"HKEX","security_code":"00002","share_class":"H"},"second")
        self.assertEqual(explicit,self.company);self.assertNotEqual(new,self.security)

    def test_concurrent_company_commit_stale_base_cannot_overwrite(self):
        base=self.store.version(self.company)
        a=self.store.propose(self.item("question"),"first","a",base)
        b=self.store.propose(self.item("thesis"),"first","b",base)
        self.reviewed(a);self.reviewed(b)
        def commit(p):
            try:return self.store.commit(p["id"])
            except VersionConflict:return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(commit,[a,b]))
        self.assertEqual(sum(r=="conflict" for r in results),1)
        self.assertEqual(self.store.version(self.company),base+1)

    def test_interrupted_file_commit_recovers_and_indexes_rebuild(self):
        source=self.adapter.add(b"fictional interrupted bytes",".txt","interruption")
        with patch("yantra.knowledge.store.atomic_bytes",side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):self.store.archive_source(source,b"fictional interrupted bytes","first")
        recovered=KnowledgeStore(self.store.root)
        self.assertEqual(recovered.source(source["id"])[1].read_bytes(),b"fictional interrupted bytes")
        fact=self.save()
        with self.store.connect() as db:db.execute("DELETE FROM fulltext")
        self.store.rebuild_fts()
        self.assertEqual(self.service.search({"query":"毛利率"})["results"][0]["id"],fact["id"])

    def test_requirements_priority_versions_revoke_and_hard_constraints(self):
        preferences=Preferences(self.store)
        global_pref=preferences.save("以后默认18个月","默认期限18个月",{"horizon":"18个月"})
        company_pref=preferences.save("此公司默认24个月","公司期限24个月",{"horizon":"24个月"},self.company)
        values,application=preferences.resolve(self.company,{"horizon":"12个月"},{"horizon":"6个月"},"second")
        self.assertEqual(values["horizon"],"12个月")
        self.assertTrue(all(a["explicit_overrides"]==["horizon"] for a in application))
        preferences.revoke(company_pref["id"])
        self.assertEqual(preferences.resolve(self.company,{}, {"horizon":"6个月"},"third")[0]["horizon"],"18个月")
        preferences.revoke(global_pref["id"])
        self.assertEqual(preferences.resolve(self.company,{}, {"horizon":"6个月"},"fourth")[0]["horizon"],"6个月")
        self.assertEqual(len(preferences.list(True)),4)
        with self.assertRaises(ValueError):preferences.save("不要核验","跳过核验",{"max_cost_cny":100})

    def test_source_channels_deduplicate_bytes_not_acquisition_history(self):
        again=self.adapter.add(self.content,".txt","another-channel",url="https://fictional.invalid/report")
        self.assertEqual(again["id"],self.source["id"])
        self.assertEqual(len(again["acquisitions"]),2)
        self.store.archive_source(again|{"origin":"another-channel","url":"https://fictional.invalid/report"},self.content,"second")
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sources").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM acquisitions").fetchone()[0],2)


class EmbeddingTests(KnowledgeFixture):
    def test_metadata_only_change_reuses_vector_and_insufficient_cost_degrades(self):
        first=self.save()
        budget=self.budget()
        service=KnowledgeService(self.store,"index",{},budget,self.profile())
        with patch.dict(os.environ,{"YANTRA_TEST_EMBED_KEY":"fixture-secret"}),patch("httpx.Client.post",self.response):
            service.index_vectors(service.rows(KnowledgeQuery()))
            count=budget.state["budget"]["model_calls"]
            self.save(self.item(retention={"R2":"估值输入优先"}))
            service.index_vectors(service.rows(KnowledgeQuery()))
            self.assertEqual(budget.state["budget"]["model_calls"],count)
            poor=self.budget(cap=.000001)
            poor_service=KnowledgeService(self.store,"poor",{},poor,self.profile())
            self.assertTrue(poor_service.search({"query":"毛利率"})["results"])
            self.assertEqual(poor.state["budget"]["model_calls"],0)

    def profile(self,model="fictional-embedding"):
        return {"provider":"openai-compatible","model_id":model,"base_url":"https://fixture.invalid/v1","api_key_env":"YANTRA_TEST_EMBED_KEY","dimensions":2,"pricing":PRICE}

    def budget(self,cap=1,requests=10):
        budget=explicit_budget(self.store,{"max_cost_cny":cap,"run_seconds":60,"max_models":requests})
        return budget

    @staticmethod
    def response(client,url,**kwargs):
        inputs=kwargs["json"]["input"]
        vectors=[{"index":i,"embedding":[1.,.1] if "profit" in text or "毛利率" in text else [.1,1.]} for i,text in enumerate(inputs)]
        return httpx.Response(200,json={"data":vectors,"usage":{"prompt_tokens":5}},request=httpx.Request("POST",url))

    def test_semantic_retrieval_incremental_switch_and_budget(self):
        self.save()
        budget=self.budget()
        service=KnowledgeService(self.store,"index",{},budget,self.profile())
        with patch.dict(os.environ,{"YANTRA_TEST_EMBED_KEY":"fixture-secret"}),patch("httpx.Client.post",self.response):
            rows=service.rows(KnowledgeQuery())
            self.assertEqual(service.index_vectors(rows),1)
            count=budget.state["budget"]["model_calls"]
            self.assertEqual(service.index_vectors(rows),0)
            self.assertEqual(budget.state["budget"]["model_calls"],count)
            result=service.search({"query":"profitability"})["results"]
            self.assertEqual(result[0]["item"]["evidence"][0]["locator"],{"line":2})
            self.assertIsNone(result[0]["match"]["fts_rank"])
            switched=KnowledgeService(self.store,"index",{},budget,self.profile("new-model"))
            self.assertEqual(switched.index_vectors(rows),1)
        self.assertEqual(self.store.status()["vectors"],2)
        self.assertGreater(budget.state["budget"]["model_cost_cny"],0)
        self.assertNotIn("fixture-secret",json.dumps(budget.state))

    def test_embedding_unknown_fee_failure_missing_usage_and_request_limit(self):
        self.save();rows=self.service.rows(KnowledgeQuery())
        budget=self.budget(requests=1)
        profile=self.profile();profile["pricing"]=None
        service=KnowledgeService(self.store,"index",{},budget,profile)
        self.assertEqual(service.index_vectors(rows),0)
        self.assertEqual(budget.state["model_ledger"],[])
        profile=self.profile()
        with patch.dict(os.environ,{"YANTRA_TEST_EMBED_KEY":"fixture-secret"}),patch("httpx.Client.post",side_effect=httpx.ConnectError("fixture fail")):
            service=KnowledgeService(self.store,"index",{},budget,profile)
            self.assertEqual(service.index_vectors(rows),0)
            self.assertEqual(budget.state["model_ledger"][0]["status"],"usage_missing_reservation_retained")
            self.assertTrue(service.search({"query":"毛利率"})["results"])
        self.assertEqual(budget.state["budget"]["model_calls"],1)
        with self.assertRaises(ValueError):explicit_budget(self.store,{"max_cost_cny":1})
        with self.assertRaises(ValueError):explicit_budget(self.store,{"max_cost_cny":50,"run_seconds":60,"max_models":10})

    def test_embedding_profile_catalog_persists_and_validates(self):
        config=ConfigStore(self.root/"config.json")
        catalog=ModelCatalog(config)
        catalog.add("fictional-vector",{"kind":"embedding",**self.profile()})
        catalog.select("embedding","fictional-vector")
        self.assertEqual(catalog.resolve("embedding")["dimensions"],2)


class SourceInterfaceTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.socket.connect", "httpx.HTTPTransport.handle_request"):
            blocker = patch(target, side_effect=AssertionError("unmocked external call forbidden"))
            blocker.start()
            self.addCleanup(blocker.stop)

    def test_web_and_offline_adapter_share_fetch_archive_and_checkpoint(self):
        class OfflineAdapter:
            id="fictional-offline"
            def discover(self,request,cursor=None):
                request.budget.check()
                return DiscoveryResult([Candidate(self.id,"fictional",external_id="item-1",locator="fixture:item-1",published_at="2026-01-01",publication_basis="fixture-declaration")],cursor="opaque:2",checkpoint={"fixture":"page1"})
            def fetch(self,candidate,budget):
                budget.check()
                return FetchResult(candidate,b"fictional original 2026-01-01 published profit 10",".txt","2026-10-13T00:00:00Z","fetched")
        with tempfile.TemporaryDirectory() as d:
            materials=RawMaterialAdapter.create(Path(d)/"materials","fictional",[])
            store=KnowledgeStore(Path(d)/"knowledge")
            pipeline=SourcePipeline(materials,OfflineAdapter(),store,"2026-02-01")
            result=pipeline.discover_and_fetch("fictional disclosure","disclosure",1)
            self.assertEqual(result["results"][0]["status"],"fetched")
            self.assertEqual(result["cursor"],"opaque:2")
            offline=materials.load_manifest()["materials"][0]
            with patch.object(materials,"fetch_raw",return_value={"content":b"fictional original 2026-01-01 published profit 10","suffix":".txt","mime":"text/plain","final_url":"https://fixture.invalid/report"}):
                web=SourcePipeline(materials).acquire(Candidate("web_search","public_web",url="https://fixture.invalid/report"))
            self.assertEqual(web["source_hash"],offline["source_hash"])
            self.assertEqual(len(materials.load_manifest()["materials"]),1)
            self.assertEqual(len(materials.load_manifest()["materials"][0]["acquisitions"]),2)
            with patch("yantra.sources.public_search",return_value=[{"href":"https://fixture.invalid/report","title":"fictional disclosure","body":"summary is not evidence"}]),patch.object(materials,"fetch_raw",return_value={"content":b"fictional original 2026-01-01 published profit 10","suffix":".txt","mime":"text/plain","final_url":"https://fixture.invalid/report"}):
                discovered=materials.search("fictional question","disclosure",1)
            self.assertEqual(discovered["adapter_id"],"web_search")
            self.assertEqual(discovered["results"][0]["source_hash"],offline["source_hash"])
            self.assertEqual(discovered["results"][0]["source_channel_id"],"public_web")
            excluded=pipeline.acquire(Candidate("fictional-offline","fictional",external_id="future",published_at="2027-01-01"))
            self.assertEqual(excluded["status"],"excluded")
