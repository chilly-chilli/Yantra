"""Small JSON CLI for knowledge administration and explicit requirements."""
from __future__ import annotations

import json
from pathlib import Path
import time
from uuid import uuid4

from ..budget import RunBudget, deadline
from ..models import ModelCatalog
from ..schemas import Limits
from .preferences import Preferences
from .schemas import KnowledgeQuery
from .service import KnowledgeService
from .store import KnowledgeStore, atomic_bytes


def add_commands(commands):
    knowledge = commands.add_parser("knowledge", help="查询、查看、重建、编辑及删除项目知识")
    knowledge.add_argument("--knowledge-dir", type=Path, default=Path(".yantra/knowledge"))
    knowledge.add_argument("--env-file", type=Path)
    actions = knowledge.add_subparsers(dest="knowledge_action", required=True)
    query = actions.add_parser("query")
    query.add_argument("query", nargs="?", default="")
    query.add_argument("--company-id", action="append")
    query.add_argument("--security-id")
    query.add_argument("--kind", action="append")
    query.add_argument("--period")
    query.add_argument("--cutoff")
    query.add_argument("--budget", type=json.loads, help="语义检索显式预算JSON；省略仅元数据/FTS")
    query.add_argument("--embedding")
    view = actions.add_parser("view")
    view.add_argument("entry_id")
    view.add_argument("--revision-id")
    actions.add_parser("status")
    source = actions.add_parser("source")
    source.add_argument("source_id")
    rebuild = actions.add_parser("rebuild")
    rebuild.add_argument("--vectors", action="store_true")
    rebuild.add_argument("--embedding")
    rebuild.add_argument("--budget", type=json.loads)
    edit = actions.add_parser("edit-fact")
    edit.add_argument("entry_id")
    edit.add_argument("--changes", type=json.loads, required=True)
    edit.add_argument("--reason", required=True)
    delete = actions.add_parser("delete-source")
    delete.add_argument("source_id")
    delete.add_argument("--session", help="仅移除此session引用")
    delete.add_argument("--impact", action="store_true")
    delete.add_argument("--permanent", action="store_true")
    delete.add_argument("--confirm", help="影响清单返回的明确确认token")
    prefs = commands.add_parser("preferences", help="明确长期要求的新增、更新、查看及撤销")
    prefs.add_argument("--knowledge-dir", type=Path, default=Path(".yantra/knowledge"))
    actions = prefs.add_subparsers(dest="preference_action", required=True)
    listing = actions.add_parser("list")
    listing.add_argument("--history", action="store_true")
    remember = actions.add_parser("remember")
    remember.add_argument("original")
    remember.add_argument("--meaning", required=True)
    remember.add_argument("--parameters", type=json.loads, required=True)
    remember.add_argument("--scope", default="global", help="global或明确company ID")
    remember.add_argument("--id", help="显式更新已有要求")
    forget = actions.add_parser("forget")
    forget.add_argument("id")
    for action in (remember, forget):
        action.add_argument("--apply-to", help="明确将变更应用于当前RUN_ID")
        action.add_argument("--runs-dir", type=Path, default=Path("runs"))
        action.add_argument("--modify-this-task", action="store_true", help="明确覆盖本次对应显式参数")


def explicit_budget(store, specification):
    if not specification or not all(k in specification for k in ("max_cost_cny", "run_seconds", "max_models")):
        raise ValueError("semantic query/rebuild requires explicit max_cost_cny, run_seconds and max_models")
    limits = Limits.model_validate({"close_seconds": 1, **specification}).model_dump()
    identifier = "index-" + uuid4().hex
    directory = store.root / "index-runs" / identifier
    directory.mkdir(parents=True)
    state = {"session": identifier, "limits": limits, "deadline": deadline(time.time(), limits["run_seconds"]),
             "model_ledger": [], "budget": {"model_calls": 0}, "stop_reasons": []}
    def save():
        atomic_bytes(directory/"state.json", json.dumps(state, ensure_ascii=False, indent=2).encode())
    budget = RunBudget(state, save)
    budget.state_path = directory
    save()
    return budget


def execute(args, settings):
    store = KnowledgeStore(args.knowledge_dir)
    if args.command == "preferences":
        preferences = Preferences(store)
        if args.preference_action == "list":
            result = {"requirements": preferences.list(args.history)}
        elif args.preference_action == "remember":
            result = preferences.save(args.original, args.meaning, args.parameters, args.scope, identifier=args.id)
        else:
            prior = next((p for p in preferences.list() if p["id"] == args.id), None)
            result = preferences.revoke(args.id)
        if getattr(args,"apply_to",None):
            from ..cli import read_state
            state = read_state(args.runs_dir,args.apply_to)
            if state["status"] not in {"running","waiting"} or not state.get("knowledge"):
                raise ValueError("requirement saved but application failed: active v0.3 session required")
            if Path(state["knowledge"]["root"]).resolve() != store.root:
                raise ValueError("requirement saved but application failed: knowledge library mismatch")
            parameters = args.parameters if args.preference_action == "remember" else (prior or {}).get("parameters",{})
            scope = args.scope if args.preference_action == "remember" else (prior or {}).get("scope")
            if scope not in {"global",state["knowledge"]["company_id"]}:
                raise ValueError("requirement saved but application failed: different company scope")
            atomic_bytes(Path(args.runs_dir)/args.apply_to/"preferences.request.json", json.dumps({"modify_this_task":args.modify_this_task,"keys":list(parameters)}).encode())
            result["application"] = "pending_at_scheduling_boundary"
    else:
        action = args.knowledge_action
        if action == "status":
            result = store.status()
        elif action == "view":
            result = store.read(args.entry_id,args.revision_id)
            with store.connect() as db:
                result["versions"] = [dict(r) for r in db.execute("SELECT id,hash,recorded_at,company_version,supersedes FROM revisions WHERE entry_id=? ORDER BY company_version", (args.entry_id,))]
        elif action == "source":
            metadata, path = store.source(args.source_id, allow_deleted=True)
            with store.connect() as db:
                acquisitions = [dict(r) for r in db.execute("SELECT * FROM acquisitions WHERE source_id=?",(args.source_id,))]
            result = {"metadata":metadata,"raw_path":str(path),"acquisitions":acquisitions}
        elif action == "edit-fact":
            result = store.edit_fact(args.entry_id,args.changes,args.reason)
        elif action == "delete-source":
            if args.permanent and args.session:
                raise ValueError("permanent shared deletion and session unlink are different operations")
            if args.impact or args.permanent and not args.confirm:
                result = store.deletion_impact(args.source_id)
            else:
                result = store.delete_source(args.source_id,args.permanent,args.confirm,args.session)
        else:
            if action=="rebuild" and not args.budget:
                raise ValueError("index rebuild requires explicit --budget for time, cost and requests")
            budget = explicit_budget(store,args.budget) if args.budget else None
            if budget:
                store.check=budget.check
            profile = ModelCatalog(settings).resolve("embedding",args.embedding) if budget else None
            if action == "rebuild" and args.vectors and not budget:
                raise ValueError("vector rebuild requires --budget; no unbounded remote calls")
            session = budget.state["session"] if budget else "cli"
            service = KnowledgeService(store,session,{},budget,profile)
            if action == "query":
                result = service.search({"query":args.query,"company_ids":args.company_id,"security_id":args.security_id,"kinds":args.kind,"period":args.period,"cutoff":args.cutoff},"cli")
            else:
                store.rebuild_fts()
                indexed = service.index_vectors(service.rows(KnowledgeQuery()), force=False) if args.vectors else 0
                result = store.status() | {"vector_rows_built":indexed,"budget_session":session}
    print(json.dumps(result,ensure_ascii=False,indent=2))
