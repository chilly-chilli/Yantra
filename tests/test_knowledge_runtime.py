"""Fictional two-session ADK/controller integration; no remote models/services."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.genai import types

from yantra.adk_app import run_engine, YantraAgent, execute_research, _root_agent
from yantra.cli import parser, execute, read_state
from yantra.engine import Engine, StopRun
from yantra.knowledge.preferences import Preferences
from yantra.replay import replay_raw
from yantra.research import RawEngine
from yantra.schemas import MainAction
from test_v2 import V2TestBackend


PRICE={"input_per_million":2,"output_per_million":8,"source":"fictional-price","as_of":"2026-10-13"}
FIRST="虚构v0.3专用夹具 2026-01-01披露 FY2025 CNY million Revenue 1,234.50 集团毛利率 10 汽车毛利率 20；公司表示竞争风险。"
SECOND="虚构v0.3专用夹具 2026-09-01披露 FY2026 CNY million Revenue 1,234.50 集团毛利率 8 汽车毛利率 25；其他业务一次性损失。"


class KnowledgeBackend(V2TestBackend):
    async def extract(self,payload):
        result=await super().extract(payload)
        doc=payload["document"];text=doc["anchors"][0]["text"]
        date="2026-09-01" if "FY2026" in text else "2026-01-01"
        period="FY2026" if "FY2026" in text else "FY2025"
        result["financials"][0].update(security_code="600001",share_class="A",accounting_scope="consolidated",information_kind="fact",period=period,disclosure_date=date)
        result["evidence"][0]["disclosure_date"]=date
        return result

    async def worker(self,task,local):
        if task["target"]=="thesis":
            refs=task["inputs"]["finance"]["evidence_refs"]
            return self._result(task,local,data={"claims":[{"type":"judgment","statement":"汽车改善对集团毛利率的影响仍需核查","metric_scope":"group","evidence_refs":refs,"assumptions":["其他业务利润稳定"],"invalidation_conditions":["集团口径不能改善"]}],"hypotheses":[],"summary":"虚构研究：业务和集团利润率分别核查","risks":["其他业务一次性损失"],"disconfirming":["集团利润率下滑"],"tracking":["损失是否一次性"],"gaps":["需核查其他业务"],"expectations":{"status":"unresolved","baseline":None,"statement":"无市场基准"}})
        return await super().worker(task,local)

    async def review_knowledge(self,payload):
        return {"reviews":[{"proposal_id":p["id"],"content_hash":p["hash"],"passed":True,"reasons":["离线夹具：逐条检查原文、数值、证券口径；问题答案仍未知"]} for p in payload["proposals"]]}

    async def compare_knowledge(self,payload):
        return {"comparisons":[{"entry_id":r["entry_id"],"revision_id":r["id"],"relation":"weakens" if r["item"]["kind"]=="thesis" else "insufficient","metric_scope":"group","reason":"集团下降，汽车改善不能证明集团论点；需核查其他业务","evidence":payload["evidence"],"follow_up":"补查其他业务一次性损失"} for r in payload["history"]]}


def offline_search(adapter,query,purpose,max_results):
    return {"query":query,"purpose":purpose,"results":[],"errors":["fictional_offline"],"acquired_at":"2026-10-13T00:00:00Z","service_cost_cny":0}


class RuntimeKnowledgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.runs=self.root/"runs"
        self.file=self.root/"fictional-v03.txt";self.file.write_text(FIRST)

    def create(self,**options):
        return RawEngine.create_raw(self.runs,{"company":"虚构v03汽车集团","market":"CN","exchange":"SSE","security_code":"600001","share_class":"A","trading_currency":"CNY","horizon":"12个月","cutoff":"2026-10-13","confirmation_mode":"autonomous","questions":["汽车与集团利润率是否一致"]},[self.file],backend=KnowledgeBackend(),pricing=PRICE,**options)

    async def run_fixture(self,engine):
        with patch("yantra.materials.RawMaterialAdapter.search",offline_search):
            await run_engine(engine)

    async def test_two_actual_adk_sessions_reuse_compare_and_replay_fixed_revisions(self):
        first=self.create();await self.run_fixture(first)
        self.assertEqual(first.state["status"],"partial")
        facts=first.knowledge.service.search({"company_ids":[first.state["knowledge"]["company_id"]],"kinds":["fact"]})["results"]
        self.assertTrue(any(r["applicability"]["reusable_fact"] for r in facts))
        # The second input is explicitly newly created; historical bytes are only in this fixture's library.
        self.file.write_text(SECOND)
        second=self.create();await self.run_fixture(second)
        self.assertEqual(second.state["knowledge"]["company_id"],first.state["knowledge"]["company_id"])
        self.assertTrue(second.state["knowledge"]["reused"])
        self.assertEqual(len(second.state["searches"]),4)
        self.assertTrue(second.state["knowledge"]["snapshots"])
        comparisons=second.state["knowledge"]["comparisons"][0]
        self.assertEqual(comparisons["snapshot_id"],second.state["knowledge"]["snapshots"][0])
        thesis_comparison=next(c for c in comparisons["comparisons"] if c["relation"]=="weakens")
        self.assertEqual(thesis_comparison["metric_scope"],"group")
        self.assertFalse(any(r["applicability"]["formal_current_conclusion"] for r in second.state["knowledge"]["startup"]))
        pinned=next(r for r in second.state["knowledge"]["fixed_refs"] if r["adopted"])
        second.knowledge.store.edit_fact(pinned["entry_id"],{"statement":"人工修改"},"明确人工修订")
        loaded=Engine.load(self.runs,second.state["run_id"],backend=KnowledgeBackend())
        self.assertEqual(loaded.state["deadline"],second.state["deadline"])
        self.assertEqual(loaded.state["model_ledger"],second.state["model_ledger"])
        replay,result=await replay_raw(self.runs,copy.deepcopy(second.state))
        self.assertEqual(result["remote_model_calls"],0)
        self.assertEqual(replay.state["knowledge"]["fixed_refs"],second.state["knowledge"]["fixed_refs"])
        source=second.tools.load_manifest()["materials"][0]
        impact=second.knowledge.store.deletion_impact(source["id"])
        second.knowledge.store.delete_source(source["id"],True,impact["confirmation"])
        with self.assertRaisesRegex(ValueError,"missing/deleted"):
            await replay_raw(self.runs,copy.deepcopy(second.state))
        self.assertTrue(read_state(self.runs,second.state["run_id"])["reproducibility"]["source_missing"])

    async def test_identical_input_reuses_verified_parser_snapshot(self):
        first=self.create();await self.run_fixture(first)
        with patch("yantra.materials.parse_file",side_effect=AssertionError("must use cached parse")):
            second=self.create()
        self.assertEqual(second.tools.load_manifest()["materials"][0]["chunks"],first.tools.load_manifest()["materials"][0]["chunks"])

    async def test_worker_permissions_proposals_review_and_cancel_archive(self):
        engine=self.create()
        finance=engine._make_task(MainAction(action="delegate_task",target="finance",purpose="collect"))
        self.assertIn("knowledge_propose",finance.authorized_tools)
        self.assertNotIn("knowledge_review",finance.authorized_tools)
        self.assertNotIn("knowledge_commit",finance.authorized_tools)
        execution={"task":finance,"local":{"actual_calls":0},"started":__import__("time").monotonic(),"failures":{}}
        with self.assertRaises(StopRun):engine._prepare_worker_tool(execution,"knowledge_review",{})
        count=engine.knowledge.store.status()["sources"]
        await engine.respond("cancel")
        self.assertEqual(engine.state["status"],"cancelled")
        self.assertEqual(engine.knowledge.store.status()["sources"],count)
        with engine.knowledge.store.connect() as db:self.assertTrue(db.execute("SELECT 1 FROM operations WHERE kind='session_archived'").fetchone())

    async def test_requirements_boundary_revoke_and_explicit_parameters(self):
        engine=self.create(explicit_parameters={"horizon":"12个月"})
        preferences=Preferences(engine.knowledge.store)
        pref=preferences.save("以后18个月","默认18个月",{"horizon":"18个月"})
        engine.state["artifacts"]["thesis"]={"task_id":"old-thesis","data":{}}
        (engine.directory/"preferences.request.json").write_text(json.dumps({"modify_this_task":False,"keys":["horizon"]}))
        engine._apply_requirements()
        self.assertEqual(engine.state["research"]["horizon"],"12个月")
        self.assertIn("thesis",engine.state["artifacts"])
        (engine.directory/"preferences.request.json").write_text(json.dumps({"modify_this_task":True,"keys":["horizon"]}))
        version=engine.state["assumptions"]["version"]
        engine._apply_requirements()
        self.assertEqual(engine.state["research"]["horizon"],"18个月")
        self.assertIn("old-thesis",engine.state["invalidated"])
        self.assertEqual(engine.state["assumptions"]["version"],version+1)
        preferences.revoke(pref["id"])
        (engine.directory/"preferences.request.json").write_text('{}')
        engine._apply_requirements()
        self.assertEqual(engine.state["research"]["horizon"],"12个月")

    async def test_cli_and_adk_explicit_remember_list_forget(self):
        arguments=parser().parse_args(["preferences","--knowledge-dir",str(self.root/"cli-library"),"remember","以后18个月","--meaning","默认18个月","--parameters",'{"horizon":"18个月"}'])
        saved=await execute(arguments)
        self.assertEqual(saved["scope"],"global")
        engine=self.create();root=YantraAgent(engine=engine)
        class Context:
            state={}
            user_content=types.Content(role="user",parts=[types.Part(text='/remember {"original":"记住24个月","meaning":"公司默认24个月","parameters":{"horizon":"24个月"}}')])
        token=_root_agent.set(root)
        try:
            result=await execute_research(Context())
            self.assertEqual(result["scope"],engine.state["knowledge"]["company_id"])
            Context.user_content=types.Content(role="user",parts=[types.Part(text="/memories")])
            self.assertEqual(len((await execute_research(Context()))["requirements"]),1)
            Context.user_content=types.Content(role="user",parts=[types.Part(text="/forget "+result["id"])])
            self.assertTrue((await execute_research(Context()))["history_retained"])
        finally:_root_agent.reset(token)
