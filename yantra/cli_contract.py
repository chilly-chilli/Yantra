"""Current CLI response and JSON input boundary (schema version 1)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


class CLIError(Exception):
    def __init__(self, code, message, *, field=None, retryable=False, exit_code=2, details=None):
        super().__init__(message)
        self.exit_code = exit_code
        self.error = {"code": code, "message": message, "retryable": retryable}
        if field:
            self.error["field"] = field
        if details is not None:
            self.error["details"] = details


class Display(Exception):
    def __init__(self, data, text):
        self.data, self.text = data, text


class VersionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        from . import __version__
        raise Display({"version": __version__}, "yantra " + __version__ + "\n")


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **{**kwargs, "allow_abbrev": False})

    def print_help(self, file=None):
        value = self.format_help()
        display = Display({"help": value}, value)
        display.command = self.prog.removeprefix("yantra ")
        raise display

    def error(self, message):
        # argparse messages may echo user values (including credentials).
        field = re.search(r"argument ([\w-]+)", message)
        missing = re.search(r"required: ([\w-]+)", message)
        location = field.group(1) if field else missing.group(1) if missing else None
        reason = ("缺少必需参数" if "required" in message else
                  "参数互斥，不能同时提供" if "not allowed with" in message else
                  "JSON输入必须是有限数值的对象" if "JSON object" in message else
                  "无效选项值" if "invalid choice" in message else
                  "无法识别参数；请检查命令帮助" if "unrecognized arguments" in message else
                  "参数格式无效；请检查命令帮助")
        error = CLIError("invalid_json" if "JSON object" in message else "argument_error", reason, field=location)
        error.command = self.prog.removeprefix("yantra ")
        error.usage = self.format_usage()
        raise error


def json_object(value):
    def invalid_constant(value):
        raise ValueError("nonfinite JSON")
    try:
        parsed = json.loads(value, parse_constant=invalid_constant)
        if not isinstance(parsed, dict):
            raise ValueError("object required")
        # Also rejects overflowing exponents such as 1e999.
        json.dumps(parsed, allow_nan=False)
        return parsed
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("expected finite JSON object") from None


def add_json_input(parser, name, *, required=False, default=None, help=None):
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--" + name, type=json_object, default=default, help=help or "JSON对象；与文件输入互斥")
    group.add_argument("--" + name + "-file", type=Path, help="读取UTF-8 JSON对象文件；与 --" + name + " 互斥")


def load_json_file(path, field):
    try:
        value = path.read_text(encoding="utf-8")
    except OSError:
        raise CLIError("input_file_error", "无法读取输入文件", field=field) from None
    except UnicodeError:
        raise CLIError("invalid_json", "输入文件必须是UTF-8 JSON对象", field=field) from None
    try:
        return json_object(value)
    except argparse.ArgumentTypeError:
        raise CLIError("invalid_json", "输入文件必须包含有限数值的JSON对象", field=field) from None


def load_json_inputs(args):
    for name in ("limits", "pricing", "parameters", "changes", "budget", "authorization"):
        path = getattr(args, name + "_file", None)
        if path is not None:
            setattr(args, name, load_json_file(path, "--" + name + "-file"))


def command_name(args=None, argv=()):
    if args is not None:
        action = next((getattr(args, name, None) for name in
                       ("model_action", "extension_action", "knowledge_action", "preference_action", "host_action")
                       if getattr(args, name, None)), None)
        return " ".join(filter(None, (args.command, action)))
    if "--version" in argv:
        return "version"
    # Only known command tokens are copied into the error envelope.
    commands = {"start", "demo", "resume", "status", "compare", "replay", "cancel", "configure",
                "models", "extensions", "knowledge", "preferences", "host"}
    return next((value for value in argv if value in commands), "yantra")


def output_mode(argv):
    mode = "text"
    for index, value in enumerate(argv):
        candidate = (argv[index + 1] if value == "--output" and index + 1 < len(argv) else
                     value.partition("=")[2] if value.startswith("--output=") else None)
        if candidate in {"text", "json"}:
            mode = candidate
    return mode


def exception_error(exc):
    if isinstance(exc, ModuleNotFoundError):
        return CLIError("dependency_missing", "运行依赖缺失；请使用安装环境的 Python 并安装项目固定依赖", field=exc.name, exit_code=1)
    if getattr(exc, "code", None) == "research_setup_required":
        return CLIError("research_setup_required", "首次研究前请完成 preferences setup，明确偏好并选择授权或拒绝授权", field="preferences")
    from pydantic import ValidationError
    if isinstance(exc, CLIError):
        return exc
    from .backend import BackendError
    if isinstance(exc, BackendError):
        if exc.code == "configuration":
            return CLIError("model_configuration_required", "正式研究需配置模型及凭据：运行 configure，或设置 MODEL_ID、MODEL_BASE_URL、MODEL_API_KEY 并用 --env-file 加载", field="model")
        return CLIError("model_service_error", "模型服务调用未完成；请检查保存状态及已核算预算（服务响应未输出）", retryable=False, exit_code=1)
    if isinstance(exc, ValidationError):
        fields = [{"field": ".".join(map(str, e["loc"])), "reason": e["type"]} for e in exc.errors()]
        return CLIError("validation_error", "输入字段未通过校验", details={"fields": fields})
    if isinstance(exc, EOFError):
        return CLIError("input_aborted", "输入已结束，未完成操作已中止；此前明确保存的操作仍保留", exit_code=1)
    if isinstance(exc, KeyboardInterrupt):
        return CLIError("interrupted", "执行已中断；请检查已保存运行，未完成请求不会自动重放", exit_code=130)
    if isinstance(exc, json.JSONDecodeError):
        return CLIError("invalid_json", "保存的JSON数据无效")
    if isinstance(exc, ValueError):
        credential = re.fullmatch(r"缺少环境变量 ([A-Za-z_][A-Za-z0-9_]*)；请配置后用 --env-file 加载", str(exc))
        if credential:
            return CLIError("model_credentials_required", "所选配置缺少凭据环境变量 " + credential.group(1) + "；请配置后用 --env-file 加载", field="--env-file")
        if str(exc).startswith(("未注册 llm 模型配置：", "未注册 ocr 模型配置：", "未注册 embedding 模型配置：")):
            return CLIError("model_configuration_invalid", "所选模型配置不存在或类型不符；请用 models list 检查并重新选择", field="model")
        known = {
            "真实研究需配置人民币输入/输出单价、价格来源和日期（或 --pricing JSON）；不使用固定每调用费用":
                ("model_pricing_required", "正式研究须配置模型单价及其来源和日期：设置 MODEL_INPUT_CNY_PER_MILLION、MODEL_OUTPUT_CNY_PER_MILLION、MODEL_PRICE_SOURCE、MODEL_PRICE_AS_OF，或提供 --pricing JSON", "--pricing", False),
            "identity selection not found or ambiguous":
                ("invalid_identity_choice", "候选选择不存在或有歧义；请使用返回的候选ID或序号", "--select-identity", False),
            "identity selection required":
                ("identity_choice_required", "当前等待证券选择；请提交候选ID、序号或唯一证券代码", "--select-identity", False),
            "run is being executed by another process": ("run_busy", "另一进程正在执行此运行", None, True),
            "no proposal available to confirm": ("invalid_run_state", "运行没有可确认的参数提案", "--decision", False),
            "run is not awaiting confirmation": ("invalid_run_state", "运行未处于等待确认状态", "--decision", False),
            "unknown knowledge entry": ("knowledge_not_found", "知识条目不存在", "entry_id", False),
            "unknown immutable revision": ("knowledge_not_found", "固定修订不存在", "--revision-id", False),
            "unknown source": ("source_not_found", "知识来源不存在", "source_id", False),
            "unknown requirement": ("preference_not_found", "长期要求不存在", "id", False),
            "source missing/deleted; cannot reproduce": ("source_unavailable", "来源已删除或缺失，无法完整复现", "source_id", False),
            "source missing/hash mismatch; cannot reproduce": ("source_unavailable", "来源缺失或哈希不匹配，无法完整复现", "source_id", False),
        }
        if str(exc) in known:
            code, message, field, retryable = known[str(exc)]
            return CLIError(code, message, field=field, retryable=retryable, exit_code=1 if retryable else 2)
        # Do not forward arbitrary exception strings or service bodies.
        return CLIError("validation_error", "参数或当前状态不满足操作约束；请检查命令帮助及保存状态")
    if isinstance(exc, OSError):
        return CLIError("io_error", "本地文件操作失败，请检查路径和权限", exit_code=1)
    return CLIError("execution_error", "执行失败；请检查配置与保存状态（服务响应未输出）", exit_code=1)


def emit(command, mode, *, data=None, error=None, text=None):
    payload = {"schema_version": "1", "command": command,
               "error" if error else "data": error.error if error else data}
    if mode == "json":
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    elif error:
        if getattr(error, "usage", None):
            print(error.usage, file=sys.stderr, end="")
        print("无法运行：" + str(error), file=sys.stderr)
    else:
        print(text if text is not None else json.dumps(data, ensure_ascii=False, indent=2), end="" if text is not None else "\n")
