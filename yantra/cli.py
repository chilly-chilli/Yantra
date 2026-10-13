"""Command-line start, explicit confirmation, durable resume and fixed-input replay."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile

from .adk_app import run_engine
from .engine import Engine


def comparison(first, second):
    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items() if k != "file_path"}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value
    def stable(state):
        thesis = state["artifacts"].get("thesis", {}).get("data", {})
        valuation = state["artifacts"].get("valuation", {}).get("data", {})
        return {"status": state["status"], "inputs": normalize(state["input_snapshot"]),
                "parameters": state["assumptions"]["parameters"],
                "valuation": {k: valuation.get(k) for k in ("enterprise_value", "equity_value", "per_share_value", "scenarios", "sensitivity")},
                "conclusions": {k: thesis.get(k) for k in ("claims", "hypotheses", "risks", "disconfirming", "tracking", "expectations")},
                "judgment": (state.get("report") or {}).get("投资结论摘要")}
    left, right = stable(first), stable(second)
    differences = [key for key in left if left[key] != right[key]]
    return {"first": first["run_id"], "second": second["run_id"], "equal": not differences,
            "differences": differences, "calculation_equal": left["valuation"] == right["valuation"],
            "conclusions_equal": left["conclusions"] == right["conclusions"] and left["judgment"] == right["judgment"]}


async def replay(storage, original):
    if original.get("raw_materials"):
        from .replay import replay_raw
        return await replay_raw(storage, original)
    if not original["assumptions"]["confirmed"]:
        raise ValueError("等待中的运行不能自动批准复测；请先明确确认原运行")
    if original["mode"] == "test":
        raise ValueError("注入测试策略使用单元测试复测，CLI不冒充重放测试模型")
    # Reconstruct the saved input bytes rather than reading possibly edited files.
    snapshot = original["input_snapshot"]
    with tempfile.TemporaryDirectory(prefix="yantra-replay-") as temporary:
        source_dir = Path(temporary)
        (source_dir / "manifest.json").write_text(json.dumps(snapshot["manifest"], ensure_ascii=False))
        for entry in snapshot["manifest"]["materials"]:
            target = (source_dir / entry["file"]).resolve()
            if not target.is_relative_to(source_dir.resolve()):
                raise ValueError("snapshot material path is outside replay directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(snapshot["materials"][entry["id"]]["text"])
        engine = Engine.create(storage, original["research"], mode="offline-demo", limits=original["limits"], materials_dir=source_dir)
    await run_engine(engine)
    if engine.state["status"] == "waiting":
        current = engine.state["assumptions"]["parameters"]
        historical = original["assumptions"]["parameters"]
        if current == historical:
            await engine.respond("confirm")
        else:
            await engine.respond("modify", historical)
    engine._log("offline_replay", {"source_run": original["run_id"],
                                    "confirmation": "仅重放原运行已明确确认的参数，不批准新假设", "live_retrieval": False})
    return engine, comparison(original, engine.state)


def parser():
    p = argparse.ArgumentParser(prog="yantra", description="Yantra v0.3 — 检索、共享知识与证据核验投研")
    p.add_argument("--config",type=Path,help="能力配置文件，默认 .yantra/config.json 或 YANTRA_CONFIG")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("start", "resume", "status", "compare", "replay", "cancel"):
        sub = commands.add_parser(name)
        sub.add_argument("--runs-dir", type=Path, default=Path("runs"))
        sub.add_argument("--env-file", type=Path, help="明确加载模型环境文件；不保存真实密钥")
        if name == "start":
            sub.add_argument("--company", required=True)
            sub.add_argument("--horizon", default=None)
            from datetime import datetime
            from zoneinfo import ZoneInfo
            sub.add_argument("--cutoff", default=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
            sub.add_argument("--mode", choices=("offline-demo", "real-model"), default="offline-demo")
            sub.add_argument("--materials-dir", type=Path)
            sub.add_argument("--material", action="append", default=[], help="原始文件、目录或公开URL，可重复")
            sub.add_argument("--market", choices=("CN", "HK"))
            sub.add_argument("--security-code")
            sub.add_argument("--exchange")
            sub.add_argument("--trading-currency")
            sub.add_argument("--confirmation-mode", choices=("autonomous", "manual"), default="autonomous")
            sub.add_argument("--framework", default=None, help="auto/value/quality/change 或经独立核验的框架版本")
            sub.add_argument("--knowledge-dir", type=Path, default=Path(".yantra/knowledge"))
            sub.add_argument("--company-id", help="明确同一公司跨证券类别的身份")
            sub.add_argument("--share-class")
            sub.add_argument("--question", action="append", default=[])
            sub.add_argument("--topic", action="append", default=[])
            sub.add_argument("--embedding",help="覆盖本次embedding模型配置")
            sub.add_argument("--framework-file", type=Path)
            sub.add_argument("--pricing", type=json.loads, help="具来源和日期的模型单价JSON")
            sub.add_argument("--llm",help="覆盖本次 LLM 配置选择")
            sub.add_argument("--ocr",help="覆盖本次 OCR 配置选择")
            sub.add_argument("--reference-price", type=float)
            sub.add_argument("--price-time")
            sub.add_argument("--price-source")
            sub.add_argument("--limits", type=json.loads, default={}, help="JSON limits，例如 {\"total_tools\":10}")
        else:
            sub.add_argument("run_id")
        if name == "resume":
            sub.add_argument("--decision", choices=("confirm", "modify", "reject", "cancel"))
            sub.add_argument("--parameters", type=json.loads)
            sub.add_argument("--continue-research", action="store_true", help="明确继续partial/failed的原始研究，保留原预算及截止")
        if name == "compare":
            sub.add_argument("second_run_id")
    configure=commands.add_parser("configure",help="交互式配置模型和扩展")
    configure.add_argument("--env-file",type=Path)
    models=commands.add_parser("models",help="LLM/OCR 配置与选择")
    models.add_argument("--env-file",type=Path)
    model_actions=models.add_subparsers(dest="model_action")
    model_actions.add_parser("list")
    add=model_actions.add_parser("add");add.add_argument("name");add.add_argument("--file",type=Path,required=True)
    select=model_actions.add_parser("select");select.add_argument("kind",choices=("llm","ocr","embedding"));select.add_argument("name")
    check=model_actions.add_parser("check");check.add_argument("name")
    extensions=commands.add_parser("extensions",help="用户 skills、MCP 及 tools")
    extensions.add_argument("--env-file",type=Path)
    ext_actions=extensions.add_subparsers(dest="extension_action")
    ext_actions.add_parser("list")
    add=ext_actions.add_parser("add");add.add_argument("kind",choices=("skills","mcp","tools"));add.add_argument("name")
    add.add_argument("--file",type=Path,help="MCP/tool JSON 描述文件")
    add.add_argument("--path",type=Path,help="SKILL.md 或技能目录")
    add.add_argument("--roles",default="finance,thesis,verification")
    for action in ("enable","disable","check"):
        command=ext_actions.add_parser(action);command.add_argument("kind",choices=("skills","mcp","tools"));command.add_argument("name")
    from .knowledge.cli import add_commands
    add_commands(commands)
    return p


def show(state):
    print(f"运行：{state['run_id']}\n模式：{state['mode']}\n状态：{state['status']}")
    b = state["budget"]
    print(f"工具：{b['tool_calls']}/{state['limits']['total_tools']} · 决策：{b['model_calls']}/{state['limits']['max_models']} · 修复：{b['repair_rounds']}")
    if state.get("raw_materials"):
        print(f"模型费用核算／未结算预留：¥{b['model_cost_cny']:.4f} · 截止时点：{state['deadline']}")
    if state["status"] == "waiting":
        print("\n等待确认，当前未执行正式估值：")
        print(json.dumps(state["pending_user"], ensure_ascii=False, indent=2))
        print(f"\n确认：python -m yantra resume {state['run_id']} --decision confirm")
        print("modify 表示明确修改并确认提交的参数；不传 decision 持续等待。")
    if state.get("report_path"):
        print("\n备忘录：" + state["report_path"])
    if state["stop_reasons"]:
        print("停止原因：" + "；".join(state["stop_reasons"]))


async def execute(args):
    if getattr(args,"env_file",None):
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            raise ValueError("env file does not exist")
        load_dotenv(args.env_file, override=True)
    from .configuration import ConfigStore
    settings=ConfigStore(args.config)
    if args.command in {"knowledge","preferences"}:
        from .knowledge.cli import execute as knowledge_command
        knowledge_command(args,settings)
        return
    if args.command in {"configure","models","extensions"}:
        await capability_command(args,settings)
        return
    if args.command == "start":
        research = {"company": args.company, "horizon": args.horizon or "12个月", "cutoff": args.cutoff,
                    "reference_price": args.reference_price, "price_time": args.price_time, "price_source": args.price_source}
        if args.material:
            if args.mode != "real-model":
                raise ValueError("原始材料研究须用 --mode real-model；offline-demo保留固定演示语义")
            from .research import RawEngine
            research.update(market=args.market, security_code=args.security_code, exchange=args.exchange,
                            trading_currency=args.trading_currency, confirmation_mode=args.confirmation_mode,
                            company_id=args.company_id,share_class=args.share_class,questions=args.question,topics=args.topic)
            explicit = {k:v for k,v in {"horizon":args.horizon,"framework":args.framework,"questions":args.question or None,"topics":args.topic or None}.items() if v is not None}
            if args.framework_file:
                explicit["framework"] = args.framework or "auto"
            engine = RawEngine.create_raw(args.runs_dir, research, args.material, limits=args.limits, pricing=args.pricing,
                                          framework=args.framework or "auto", framework_file=args.framework_file,settings=settings,
                                          llm_profile=args.llm,ocr_profile=args.ocr,embedding_profile=args.embedding,
                                          knowledge_dir=args.knowledge_dir,explicit_parameters=explicit)
        else:
            # Preserve explicit v0.1 demonstration behavior, including manual confirmation.
            research["cutoff"] = research["cutoff"][:10]
            engine = Engine.create(args.runs_dir, research, mode=args.mode, limits=args.limits, materials_dir=args.materials_dir)
        state = await run_engine(engine)
    elif args.command == "compare":
        # Comparison and status read durable state without configuring/calling a model.
        first = read_state(args.runs_dir, args.run_id)
        second = read_state(args.runs_dir, args.second_run_id)
        print(json.dumps(comparison(first, second), ensure_ascii=False, indent=2))
        return
    elif args.command == "status":
        state = read_state(args.runs_dir, args.run_id)
    elif args.command == "cancel":
        state = read_state(args.runs_dir, args.run_id)
        (Path(args.runs_dir) / args.run_id / "cancel.request").write_text("user requested cancellation\n")
        print("已提交取消；活动任务将在下一个预算边界收束并保存。")
        return
    elif args.command == "replay":
        engine, result = await replay(args.runs_dir, read_state(args.runs_dir, args.run_id))
        show(engine.state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    else:
        engine = Engine.load(args.runs_dir, args.run_id)
        if args.continue_research:
            if not engine.state.get("raw_materials") or engine.state["status"] not in {"partial", "failed"}:
                raise ValueError("continue-research requires partial/failed raw research")
            engine.run_budget.check()
            with engine._lock():
                engine.state.setdefault("resume_history", []).append({"stop_reasons": engine.state["stop_reasons"], "status": engine.state["status"]})
                engine.state.update(status="running", stop_reasons=[])
                engine._save()
        if engine.state.get("raw_materials") and engine.state["status"] == "running" and args.decision == "modify":
            if not args.parameters:
                raise ValueError("modify requires parameters")
            target = engine.directory / "parameters.request.json"
            temporary = engine.directory / "parameters.request.tmp"
            temporary.write_text(json.dumps(args.parameters))
            temporary.replace(target)
            print("参数修改已提交；下一个调度边界应用新版本、重算及核验。")
            return
        state = await engine.respond(args.decision, args.parameters) if args.decision else await run_engine(engine)
    show(state)


async def capability_command(args,store):
    from .configuration import ask
    from .models import ModelCatalog,interactive_models,check_model
    from .extensions import ExtensionCatalog,interactive_extensions,discover_mcp
    models,extensions=ModelCatalog(store),ExtensionCatalog(store)
    if args.command=="configure":
        while True:
            choice=ask("配置中心：models 模型 / extensions 能力扩展 / exit 退出","models")
            if choice=="exit":return
            try:
                if choice=="models":interactive_models(models)
                elif choice=="extensions":await interactive_extensions(extensions)
                else:print("请输入 models、extensions 或 exit。")
            except ValueError as exc:print("配置未保存："+safe_validation_error(exc))
    elif args.command=="models":
        action=args.model_action
        if action is None:interactive_models(models)
        elif action=="list":print(json.dumps({"models":models.entries(),"selected":store.load()["selected"],"env_llm":"MODEL_*"},ensure_ascii=False,indent=2))
        elif action=="add":models.add(args.name,json.loads(args.file.read_text()));print("模型配置已保存。")
        elif action=="select":models.select(args.kind,args.name);print("模型选择已保存。")
        else:
            profile=models.entries().get(args.name)
            if args.name=="env":
                import os
                from .models import ModelProfile
                profile=ModelProfile(kind="llm",provider="openai-compatible",model_id=os.getenv("MODEL_ID",""),
                                     base_url=os.getenv("MODEL_BASE_URL",""),api_key_env="MODEL_API_KEY").model_dump()
            if profile is None:raise ValueError("模型未注册")
            print(json.dumps(await check_model({"name":args.name,**profile}),ensure_ascii=False,indent=2))
    else:
        action=args.extension_action
        if action is None:await interactive_extensions(extensions)
        elif action=="list":print(json.dumps({k:store.load()[k] for k in ("skills","mcp","tools")},ensure_ascii=False,indent=2))
        elif action=="add":
            if args.kind=="skills":
                if not args.path:raise ValueError("技能导入需要 --path")
                data={"path":str(args.path)}
            else:
                if not args.file:raise ValueError("MCP/tool 导入需要 --file")
                data=json.loads(args.file.read_text())
            data["roles"]=[r.strip() for r in args.roles.split(",")]
            await extensions.add(args.kind,args.name,data);print("扩展已导入并启用。")
        elif action in {"enable","disable"}:extensions.enable(args.kind,args.name,action=="enable");print("扩展状态已保存。")
        else:
            spec=store.load()[args.kind].get(args.name)
            if spec is None:raise ValueError("扩展未注册")
            if args.kind=="mcp":
                tools=await discover_mcp(spec);store.update(lambda c:c["mcp"][args.name].__setitem__("tools",tools))
                print(json.dumps({"ready":True,"tools":tools},ensure_ascii=False,indent=2))
            elif args.kind=="skills":print(json.dumps({"ready":Path(spec["path"]).is_file(),"roles":spec["roles"]},ensure_ascii=False))
            else:
                import shutil
                print(json.dumps({"ready":bool(shutil.which(spec["command"][0])),"input_schema":spec["input_schema"]},ensure_ascii=False))


def safe_validation_error(exc):
    from pydantic import ValidationError
    if isinstance(exc,ValidationError):return "；".join(".".join(map(str,e["loc"]))+": "+e["type"] for e in exc.errors())
    return str(exc)


def read_state(storage, run_id):
    if not run_id.startswith("run-") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in run_id):
        raise ValueError("invalid run id")
    state = json.loads((Path(storage) / run_id / "state.json").read_text())
    marker = Path(storage)/run_id/"source-missing.json"
    if marker.exists():
        state["reproducibility"] = json.loads(marker.read_text())
    return state


def main():
    logging.getLogger("google.adk").setLevel(logging.ERROR)
    args = parser().parse_args()
    try:
        asyncio.run(execute(args))
    except (ValueError, OSError) as exc:
        print("无法运行：" + safe_validation_error(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("执行已中断；已保存的运行记录可检查，中断操作不会自动重放。", file=sys.stderr)
        return 130
    except EOFError:
        print("交互已结束；尚未提交的输入没有保存。",file=sys.stderr)
        return 0
    except Exception as exc:
        print("无法运行：" + type(exc).__name__ + "（请检查模型配置；未输出敏感服务响应）", file=sys.stderr)
        return 2
    return 0
