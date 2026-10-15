"""Command-line start, explicit confirmation, durable resume and fixed-input replay."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import tempfile
import shlex
from contextlib import redirect_stdout

from .cli_contract import (CLIError, Display, Parser, VersionAction, add_json_input,
                           command_name, emit, exception_error, load_json_file,
                           load_json_inputs, output_mode)


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
    from .adk_app import run_engine
    from .engine import Engine
    if original.get("raw_materials"):
        from .replay import replay_raw
        return await replay_raw(storage, original)
    if not original["assumptions"]["confirmed"]:
        raise CLIError("invalid_run_state", "等待中的运行不能自动批准复测；请先明确确认原运行", field="run_id")
    if original["mode"] == "test":
        raise CLIError("invalid_run_state", "注入测试策略使用单元测试复测，CLI不能重放测试模型", field="run_id")
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
    from . import __version__
    p = Parser(prog="yantra", description=f"Yantra v{__version__} — 检索、共享知识与证据核验投研")
    p.add_argument("--config",type=Path,help="能力配置文件，默认 .yantra/config.json 或 YANTRA_CONFIG")
    p.add_argument("--version", action=VersionAction, nargs=0, help="显示版本")
    commands = p.add_subparsers(dest="command", required=True)
    for name in ("start", "demo", "resume", "status", "compare", "replay", "cancel"):
        descriptions = {
            "start": "创建正式研究；可只给公司名称，使用已配置模型与预算；可能保存 waiting/partial/failed",
            "demo": "固定虚构材料的离线演示；不联网、不调用真实模型",
            "resume": "沿用已保存预算及截止恢复；不指定 decision 不自动确认；modify 会提交或应用参数",
            "status": "只读查询运行，失败研究仍是成功查询，不调用模型",
            "compare": "只读比较两个保存运行，不调用模型",
            "replay": "固定输入离线复算；不批准新假设，不重新调用远程判断",
            "cancel": "提交取消请求；已结束任务不创建请求，实际取消须查询 status",
        }
        sub = commands.add_parser(name, help=descriptions[name], description=descriptions[name])
        sub.add_argument("--runs-dir", type=Path, default=Path("runs"), help="运行存储目录，默认 runs；恢复时必须使用同一目录")
        if name != "demo":
            sub.add_argument("--env-file", type=Path, help="明确加载模型环境文件；不保存真实密钥")
        if name == "start":
            sub.add_argument("--company", required=True)
            sub.add_argument("--horizon", default=None, help="默认12个月；原始研究可继承明确长期要求")
            from datetime import datetime
            from zoneinfo import ZoneInfo
            sub.add_argument("--cutoff", default=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
            sub.add_argument("--mode", choices=("offline-demo", "real-model"), default="real-model", help="兼容显式 real-model；旧 offline-demo 请迁移到 demo")
            sub.add_argument("--materials-dir", type=Path, help=argparse.SUPPRESS)
            sub.add_argument("--material", action="append", default=[], help="原始文件、目录或公开URL，可重复")
            sub.add_argument("--market", choices=("CN", "HK"))
            sub.add_argument("--security-code")
            sub.add_argument("--exchange")
            sub.add_argument("--trading-currency")
            sub.add_argument("--confirmation-mode", choices=("autonomous", "manual"), default=None, help="继承长期偏好；未回复的节点使用暂定情景继续交付")
            add_json_input(sub, "authorization", help="仅本次覆盖自主选择授权，明确status/scope；不修改长期偏好")
            sub.add_argument("--framework", default=None, help="auto/value/quality/change 或经独立核验的框架版本")
            sub.add_argument("--knowledge-dir", type=Path, default=Path(".yantra/knowledge"))
            sub.add_argument("--company-id", help="明确同一公司跨证券类别的身份")
            sub.add_argument("--share-class")
            sub.add_argument("--question", action="append", default=[])
            sub.add_argument("--topic", action="append", default=[])
            sub.add_argument("--embedding",help="覆盖本次embedding模型配置")
            sub.add_argument("--framework-file", type=Path)
            add_json_input(sub, "pricing", help="具来源和日期的模型单价JSON；与 --pricing-file 互斥")
            sub.add_argument("--llm",help="覆盖本次 LLM 配置选择")
            sub.add_argument("--ocr",help="覆盖本次 OCR 配置选择")
            sub.add_argument("--reference-price", type=float)
            sub.add_argument("--price-time")
            sub.add_argument("--price-source")
            add_json_input(sub, "limits", default={}, help="覆盖既有预算默认值的JSON对象；原始研究默认7200秒/49元，与 --limits-file 互斥")
        elif name == "demo":
            sub.add_argument("--company", default="DEMO", help="manifest内明确标记的虚构公司")
            sub.add_argument("--horizon", default="12个月")
            sub.add_argument("--cutoff", default="2025-12-31", help="固定演示资料的研究截止日")
            sub.add_argument("--materials-dir", type=Path, required=True, help="含 fictional=true manifest.json 的固定虚构材料目录")
            sub.add_argument("--reference-price", type=float)
            sub.add_argument("--price-time")
            sub.add_argument("--price-source")
            add_json_input(sub, "limits", default={}, help="固定离线演示预算的JSON对象")
        else:
            sub.add_argument("run_id")
        if name == "resume":
            sub.add_argument("--decision", choices=("confirm", "modify", "reject", "cancel"))
            sub.add_argument("--select-identity", help="选择等待中的证券：候选ID、从1起的序号，或唯一匹配的证券代码")
            add_json_input(sub, "parameters", help="modify 的JSON参数；与 --parameters-file 互斥")
            sub.add_argument("--continue-research", action="store_true", help="明确继续partial/failed的原始研究，保留原预算及截止")
        if name == "compare":
            sub.add_argument("second_run_id")
    configure=commands.add_parser("configure",help="交互式配置模型和扩展")
    configure.add_argument("--env-file",type=Path)
    models=commands.add_parser("models",help="LLM/OCR 配置与选择")
    models.add_argument("--env-file",type=Path)
    model_actions=models.add_subparsers(dest="model_action")
    model_actions.add_parser("interactive", help="明确进入交互菜单，需要文本模式和TTY")
    model_actions.add_parser("list")
    add=model_actions.add_parser("add");add.add_argument("name");add.add_argument("--file",type=Path,required=True)
    select=model_actions.add_parser("select");select.add_argument("kind",choices=("llm","ocr","embedding"));select.add_argument("name")
    check=model_actions.add_parser("check");check.add_argument("name")
    extensions=commands.add_parser("extensions",help="用户 skills、MCP 及 tools")
    extensions.add_argument("--env-file",type=Path)
    ext_actions=extensions.add_subparsers(dest="extension_action")
    ext_actions.add_parser("interactive", help="明确进入交互菜单，需要文本模式和TTY")
    ext_actions.add_parser("list")
    add=ext_actions.add_parser("add");add.add_argument("kind",choices=("skills","mcp","tools"));add.add_argument("name")
    add.add_argument("--file",type=Path,help="MCP/tool JSON 描述文件")
    add.add_argument("--path",type=Path,help="SKILL.md 或技能目录")
    add.add_argument("--roles",default="finance,thesis,verification")
    add.add_argument("--loading",choices=("eager","native"),help="仅skills：eager完整注入；native为Worker按需读取试点")
    for action in ("enable","disable","check"):
        command=ext_actions.add_parser(action);command.add_argument("kind",choices=("skills","mcp","tools"));command.add_argument("name")
    from .knowledge.cli import add_commands
    add_commands(commands)
    host = commands.add_parser("host", help="宿主共享契约、快速提交、主要 Skill 与本地 stdio MCP")
    host_actions = host.add_subparsers(dest="host_action", required=True)
    from .host_contract import INPUTS
    for operation in (*INPUTS, "mcp", "skill"):
        action = host_actions.add_parser(operation)
        action.add_argument("--workspace", type=Path, required=True, help="共享本地工作区；推荐绝对路径，跨目录调用使用相同值")
        action.add_argument("--runs-dir", type=Path, help="显式运行目录；默认 workspace/runs 或 YANTRA_RUNS_DIR")
        action.add_argument("--knowledge-dir", type=Path, help="显式知识库；默认 workspace/.yantra/knowledge 或 YANTRA_KNOWLEDGE_DIR")
        action.add_argument("--env-file", type=Path, help="仅 worker 明确加载的本地环境文件")
        if operation in INPUTS:
            add_json_input(action, "input", default={}, help="操作输入对象；实际 schema 见 host schema")
        elif operation == "skill":
            action.add_argument("--destination", type=Path, help="将主要 Skill 导出到新目录；不覆盖既有文件")
    def outputs(command, root=False):
        command.add_argument("--output", choices=("text", "json"),
                             default="text" if root else argparse.SUPPRESS,
                             help="输出格式，默认 text；json 为 schema_version=1 的单个对象")
        for action in command._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    outputs(child)
    outputs(p, root=True)
    return p


def run_result(state, args, settings):
    keys = ("run_id", "mode", "status", "budget", "limits", "deadline", "pending_user",
            "stop_reasons", "hard_checks", "issues", "report_path", "reproducibility", "preference_snapshot", "choices")
    result = {key: state.get(key) for key in keys}
    result["research"] = state.get("research")
    judgment = (state.get("report") or {}).get("投资结论摘要", {}).get("buy_judgment")
    if state.get("raw_materials") and judgment is not None:
        result["buy_judgment"] = judgment
    prefix = [sys.executable, "-m", "yantra", "--output", "json", "--config", str(settings.path)]
    context = ["--runs-dir", str(args.runs_dir.resolve())]
    if getattr(args, "env_file", None):
        context += ["--env-file", str(args.env_file.resolve())]
    actions = [{"action": "status", "argv": prefix + ["status", state["run_id"], *context]}]
    if state["status"] == "waiting":
        pending = state.get("pending_user") or {}
        if pending.get("kind") == "identity":
            actions.extend({"action": "select_identity", "candidate_id": candidate["candidate_id"],
                            "argv": prefix + ["resume", state["run_id"], *context, "--select-identity", candidate["candidate_id"]]}
                           for candidate in pending.get("candidates", []))
        else:
            actions.append({"action": "confirm", "argv": prefix + ["resume", state["run_id"], *context, "--decision", "confirm"]})
    elif state.get("raw_materials") and state["status"] in {"partial", "failed"}:
        actions.append({"action": "continue_research", "argv": prefix + ["resume", state["run_id"], *context, "--continue-research"],
                        "notice": "仅在原预算及截止仍允许时可继续，不重置预算"})
        if state.get("investment_logic_version") and state.get("assumptions", {}).get("parameters") and not state["assumptions"]["confirmed"]:
            actions.append({"action": "confirm", "argv": prefix + ["resume", state["run_id"], *context, "--decision", "confirm"],
                            "notice": "明确确认当前主模型形成新版本、重算及核验；辅助模型须分别提交确认"})
    result["next_actions"] = actions
    return result


def format_text(command, data):
    if command.startswith("host "):
        return None
    if data.get("operation") == "interactive_finished":
        return ""
    messages = {"models add": "模型配置已保存。", "models select": "模型选择已保存。",
                "extensions add": "扩展已导入并启用。", "extensions enable": "扩展状态已保存。",
                "extensions disable": "扩展状态已保存。"}
    if command in messages:
        return messages[command] + "\n"
    if "run_id" not in data:
        return None
    lines = []
    if data.get("buy_judgment"):
        judgment = data["buy_judgment"]
        lines.append(judgment.get("statement") or judgment["label"])
    elif data["mode"] == "offline-demo":
        lines.append("虚构材料离线演示")
    lines += [f"运行：{data['run_id']}", f"模式：{data['mode']}", f"状态：{data['status']}"]
    b, limits = data["budget"], data["limits"]
    lines.append(f"工具：{b['tool_calls']}/{limits['total_tools']} · 决策：{b['model_calls']}/{limits['max_models']} · 修复：{b['repair_rounds']}")
    if data.get("deadline"):
        lines.append(f"模型费用核算／未结算预留：¥{b.get('model_cost_cny', 0):.4f} · 截止时点：{data['deadline']}")
    if data["status"] == "waiting":
        pending = data.get("pending_user") or {}
        if pending.get("kind") == "identity":
            lines += ["\n等待选择公司／上市市场，当前未执行估值：", pending.get("question", "请选择具体证券。")]
            for index, candidate in enumerate(pending.get("candidates", []), 1):
                name = candidate.get("legal_name") or candidate.get("company") or ""
                lines.append(f"{index}. {name} · {candidate.get('market', '')} {candidate.get('security_code', '')} · {candidate.get('trading_currency', '')} · {candidate['candidate_id']}")
            selections = [a for a in data["next_actions"] if a["action"] == "select_identity"]
            if selections:
                lines.append("\n选择示例：" + shlex.join(selections[0]["argv"]))
            lines.append("不提交选择时持续等待；预算与原截止时点保持不变。")
        else:
            lines += ["\n等待确认，当前未执行正式估值：", json.dumps(pending, ensure_ascii=False, indent=2)]
            action = next(a for a in data["next_actions"] if a["action"] == "confirm")
            lines += ["\n确认：" + shlex.join(action["argv"]), "modify 表示明确修改并确认提交的参数；不传 decision 持续等待。"]
    if data.get("report_path"):
        lines.append("\n备忘录：" + data["report_path"])
    if data.get("stop_reasons"):
        lines.append("停止原因：" + "；".join(data["stop_reasons"]))
    if data.get("message"):
        lines.append(data["message"])
    if data.get("comparison"):
        lines.append(json.dumps(data["comparison"], ensure_ascii=False, indent=2))
    return "\n".join(lines) + "\n"


async def execute(args):
    load_json_inputs(args)
    if getattr(args,"env_file",None):
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            raise CLIError("input_file_error", "环境文件不存在", field="--env-file")
        load_dotenv(args.env_file, override=True)
    from .configuration import ConfigStore
    settings=ConfigStore(args.config)
    if args.command == "host":
        from .host_service import HostService
        service = HostService(args.workspace, config=args.config, runs_dir=args.runs_dir, knowledge_dir=args.knowledge_dir, env_file=args.env_file)
        if args.host_action == "skill":
            import shutil
            source = Path(__file__).parent / "host_skill" / "yantra-research"
            if args.destination:
                if args.destination.exists():
                    raise CLIError("destination_exists", "Skill 目标已存在；请选择新目录，不覆盖用户内容")
                shutil.copytree(source, args.destination)
            return {"skill": "yantra-research", "path": str((args.destination or source).resolve()),
                    "content": (source / "SKILL.md").read_text()}
        value = load_json_file(args.input_file, "--input-file") if args.input_file else args.input
        return service.call(args.host_action, value)
    if args.command in {"knowledge","preferences"}:
        from .knowledge.cli import execute as knowledge_command
        return knowledge_command(args,settings)
    if args.command in {"configure","models","extensions"}:
        return await capability_command(args,settings)
    from .adk_app import run_engine
    from .engine import Engine, TERMINAL
    if args.command == "start":
        if args.mode == "offline-demo":
            raise CLIError("demo_command_required", "start 仅启动正式研究；旧 --mode offline-demo 请改用 demo --materials-dir 虚构材料目录", field="--mode")
        if args.materials_dir is not None:
            raise CLIError("demo_argument", "--materials-dir 属于 demo；正式研究可用 --material 文件或目录，也可只提供公司名称", field="--materials-dir")
        research = {"company": args.company, "horizon": args.horizon or "12个月", "cutoff": args.cutoff,
                    "reference_price": args.reference_price, "price_time": args.price_time, "price_source": args.price_source}
        from .research import RawEngine
        research.update(market=args.market, security_code=args.security_code, exchange=args.exchange,
                        trading_currency=args.trading_currency,
                        company_id=args.company_id,share_class=args.share_class,questions=args.question,topics=args.topic)
        explicit = {k:v for k,v in {"horizon":args.horizon,"framework":args.framework,"questions":args.question or None,"topics":args.topic or None}.items() if v is not None}
        if args.framework_file:
            explicit["framework"] = args.framework or "auto"
        if args.confirmation_mode is not None:
            research["confirmation_mode"] = args.confirmation_mode
            explicit["confirmation_mode"] = args.confirmation_mode
        engine = RawEngine.create_raw(args.runs_dir, research, args.material, limits=args.limits, pricing=args.pricing,
                                      framework=args.framework or "auto", framework_file=args.framework_file,settings=settings,
                                      llm_profile=args.llm,ocr_profile=args.ocr,embedding_profile=args.embedding,
                                      knowledge_dir=args.knowledge_dir,explicit_parameters=explicit, authorization_override=args.authorization)
        state = await run_engine(engine)
    elif args.command == "demo":
        manifest = args.materials_dir / "manifest.json"
        if not manifest.is_file():
            raise CLIError("demo_materials_missing", "离线演示材料目录须包含 manifest.json", field="--materials-dir")
        if load_json_file(manifest, "--materials-dir").get("fictional") is not True:
            raise CLIError("demo_materials_invalid", "demo 仅接受 manifest 中明确标记 fictional=true 的虚构材料", field="--materials-dir")
        research = {"company": args.company, "horizon": args.horizon, "cutoff": args.cutoff,
                    "reference_price": args.reference_price, "price_time": args.price_time, "price_source": args.price_source}
        engine = Engine.create(args.runs_dir, research, mode="offline-demo", limits=args.limits, materials_dir=args.materials_dir)
        state = await run_engine(engine)
    elif args.command == "compare":
        # Comparison and status read durable state without configuring/calling a model.
        first = read_state(args.runs_dir, args.run_id)
        second = read_state(args.runs_dir, args.second_run_id)
        return comparison(first, second)
    elif args.command == "status":
        state = read_state(args.runs_dir, args.run_id)
    elif args.command == "cancel":
        state = read_state(args.runs_dir, args.run_id)
        result = run_result(state, args, settings)
        if state["status"] in TERMINAL:
            result["cancellation"] = "cancelled" if state["status"] == "cancelled" else "already_terminal"
            result["message"] = "任务已结束，无需提交取消请求。"
        else:
            (Path(args.runs_dir) / args.run_id / "cancel.request").write_text("user requested cancellation\n")
            result["cancellation"] = "request_accepted"
            result["message"] = "已提交取消；活动任务将在下一个预算边界收束并保存，请用status确认实际取消。"
        return result
    elif args.command == "replay":
        engine, result = await replay(args.runs_dir, read_state(args.runs_dir, args.run_id))
        return run_result(engine.state, args, settings) | {"comparison": result}
    else:
        saved = read_state(args.runs_dir, args.run_id)
        if args.select_identity:
            if args.decision or args.continue_research or args.parameters:
                raise CLIError("argument_error", "--select-identity 不能与估值确认、参数修改或继续研究同时使用", field="--select-identity")
            if saved["status"] != "waiting" or (saved.get("pending_user") or {}).get("kind") != "identity":
                raise CLIError("invalid_run_state", "运行未在等待公司或证券选择", field="--select-identity")
            engine = Engine.load(args.runs_dir, args.run_id)
            state = await engine.select_identity(args.select_identity)
            return run_result(state, args, settings)
        if args.decision in {"confirm", "modify", "reject"} and (saved.get("pending_user") or {}).get("kind") == "identity":
            raise CLIError("identity_choice_required", "当前等待证券选择；请用 --select-identity 候选ID、序号或唯一证券代码", field="--select-identity")
        if args.decision == "modify" and not args.parameters:
            raise CLIError("missing_parameters", "modify需要参数对象", field="--parameters")
        if args.continue_research and args.decision:
            raise CLIError("argument_error", "--continue-research与 --decision 不能同时使用", field="--decision")
        if saved["status"] == "cancelled":
            raise CLIError("invalid_run_state", "已取消研究不能恢复", field="run_id")
        engine = Engine.load(args.runs_dir, args.run_id)
        if args.continue_research:
            if not engine.state.get("raw_materials") or engine.state["status"] not in {"partial", "failed"}:
                raise CLIError("invalid_run_state", "继续研究需要partial/failed状态的原始研究", field="--continue-research")
            engine.run_budget.check()
            with engine._lock():
                engine.state.setdefault("resume_history", []).append({"stop_reasons": engine.state["stop_reasons"], "status": engine.state["status"]})
                engine.state.update(status="running", stop_reasons=[])
                engine._save()
        if engine.state.get("raw_materials") and engine.state["status"] == "running" and args.decision == "modify":
            if not args.parameters:
                raise CLIError("missing_parameters", "modify需要参数对象", field="--parameters")
            target = engine.directory / "parameters.request.json"
            temporary = engine.directory / "parameters.request.tmp"
            temporary.write_text(json.dumps(args.parameters))
            temporary.replace(target)
            return run_result(engine.state, args, settings) | {"operation": "parameters_request_accepted", "message": "参数修改已提交；下一个调度边界应用新版本、重算及核验。"}
        state = await engine.respond(args.decision, args.parameters) if args.decision else await run_engine(engine)
    return run_result(state, args, settings)


async def capability_command(args,store):
    from .configuration import ask
    from .models import ModelCatalog,interactive_models,check_model
    from .extensions import ExtensionCatalog,interactive_extensions,discover_mcp
    models,extensions=ModelCatalog(store),ExtensionCatalog(store)
    action = getattr(args, "model_action", None) if args.command == "models" else getattr(args, "extension_action", None)
    interactive = args.command == "configure" or action in {None, "interactive"}
    if interactive:
        if action is None and args.command != "configure" and (args.output == "json" or not sys.stdin.isatty()):
            raise CLIError("subcommand_required", "请指定非交互子命令，或使用 interactive 进入菜单", field="subcommand")
        if args.output == "json" or not sys.stdin.isatty():
            raise CLIError("interactive_required", "交互入口需要文本输出和TTY；Agent请使用明确子命令")
        from .cli_brand import show_banner
        show_banner()
    if args.command=="configure":
        while True:
            choice=ask("配置中心：models 模型 / extensions 能力扩展 / exit 退出","models")
            if choice=="exit":return {"operation": "interactive_finished"}
            try:
                if choice=="models":interactive_models(models)
                elif choice=="extensions":await interactive_extensions(extensions)
                else:print("请输入 models、extensions 或 exit。")
            except ValueError as exc:print("配置未保存："+safe_validation_error(exc))
    elif args.command=="models":
        action=args.model_action
        if action in {None, "interactive"}:
            interactive_models(models)
            return {"operation": "interactive_finished"}
        elif action=="list":return {"models":models.entries(),"selected":store.load()["selected"],"env_llm":"MODEL_*"}
        elif action=="add":
            models.add(args.name,load_json_file(args.file, "--file"))
            return {"operation": "saved", "name": args.name}
        elif action=="select":
            models.select(args.kind,args.name)
            return {"operation": "selected", "kind": args.kind, "name": args.name}
        else:
            profile=models.entries().get(args.name)
            if args.name=="env":
                import os
                from .models import ModelProfile
                profile=ModelProfile(kind="llm",provider="openai-compatible",model_id=os.getenv("MODEL_ID",""),
                                     base_url=os.getenv("MODEL_BASE_URL",""),api_key_env="MODEL_API_KEY").model_dump()
            if profile is None:raise CLIError("model_not_found", "模型未注册", field="name")
            return await check_model({"name":args.name,**profile})
    else:
        action=args.extension_action
        if action in {None, "interactive"}:
            await interactive_extensions(extensions)
            return {"operation": "interactive_finished"}
        elif action=="list":return {k:store.load()[k] for k in ("skills","mcp","tools")}
        elif action=="add":
            if args.kind=="skills":
                if not args.path:raise CLIError("argument_error", "技能导入需要 --path", field="--path")
                data={"path":str(args.path),"loading":args.loading or "eager"}
            else:
                if args.loading:raise CLIError("argument_error", "--loading 仅适用于 skills", field="--loading")
                if not args.file:raise CLIError("argument_error", "MCP/tool导入需要 --file", field="--file")
                data=load_json_file(args.file, "--file")
            data["roles"]=[r.strip() for r in args.roles.split(",")]
            spec = await extensions.add(args.kind,args.name,data)
            return {"operation": "saved", "kind": args.kind, "name": args.name, "enabled": spec["enabled"]}
        elif action in {"enable","disable"}:
            extensions.enable(args.kind,args.name,action=="enable")
            return {"operation": "saved", "kind": args.kind, "name": args.name, "enabled": action=="enable"}
        else:
            spec=store.load()[args.kind].get(args.name)
            if spec is None:raise CLIError("extension_not_found", "扩展未注册", field="name")
            if args.kind=="mcp":
                tools=await discover_mcp(spec);store.update(lambda c:c["mcp"][args.name].__setitem__("tools",tools))
                return {"ready":True,"tools":tools}
            elif args.kind=="skills":return {"ready":Path(spec["path"]).is_file(),"roles":spec["roles"]}
            else:
                import shutil
                return {"ready":bool(shutil.which(spec["command"][0])),"input_schema":spec["input_schema"]}


def safe_validation_error(exc):
    from pydantic import ValidationError
    if isinstance(exc,ValidationError):return "；".join(".".join(map(str,e["loc"]))+": "+e["type"] for e in exc.errors())
    return str(exception_error(exc))


def read_state(storage, run_id):
    if not run_id.startswith("run-") or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in run_id):
        raise CLIError("invalid_run_id", "运行ID格式无效", field="run_id")
    try:
        state = json.loads((Path(storage) / run_id / "state.json").read_text())
    except FileNotFoundError:
        raise CLIError("run_not_found", "运行不存在，请检查run_id和 --runs-dir", field="run_id") from None
    marker = Path(storage)/run_id/"source-missing.json"
    if marker.exists():
        state["reproducibility"] = json.loads(marker.read_text())
    return state


def main(argv=None):
    from .terminal_status import TerminalStatus, current_status
    argv = list(sys.argv[1:] if argv is None else argv)
    mode, args = output_mode(argv), None
    status = TerminalStatus()
    token = current_status.set(status)
    logging.getLogger("google.adk").setLevel(logging.ERROR)
    try:
        args = parser().parse_args(argv)
        mode = args.output
        if args.command == "host" and args.host_action == "mcp":
            from .host_service import HostService
            from .host_mcp import serve
            asyncio.run(serve(HostService(args.workspace, config=args.config, runs_dir=args.runs_dir, knowledge_dir=args.knowledge_dir, env_file=args.env_file)))
            return 0
        status.report("working", "正在执行 " + command_name(args))
        # Third-party progress/prints cannot contaminate the JSON response.
        if mode == "json":
            with redirect_stdout(sys.stderr):
                result = asyncio.run(execute(args))
        else:
            result = asyncio.run(execute(args))
        emit(command_name(args), mode, data=result, text=format_text(command_name(args), result) if mode == "text" else None)
        status.finish(args.command, result)
    except Display as display:
        emit(getattr(display, "command", command_name(args, argv)), mode, data=display.data, text=display.text)
        status.report("done", "命令已完成")
    except (Exception, KeyboardInterrupt) as exc:
        error = exception_error(exc)
        emit(getattr(error, "command", command_name(args, argv)), mode, error=error)
        status.report("idle" if isinstance(exc, (KeyboardInterrupt, EOFError)) else "error", str(error))
        return error.exit_code
    finally:
        current_status.reset(token)
    return 0
