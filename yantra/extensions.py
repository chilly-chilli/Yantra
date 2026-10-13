"""User skills, MCP connections and JSON command tools with role-scoped execution."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
from typing import Literal

import jsonschema
from pydantic import Field, model_validator

from .configuration import ask, environment_values, redact
from .schemas import Contract

ExtensionRole = Literal["main","finance","thesis","valuation","verification"]


class ScopedExtension(Contract):
    roles: list[Literal["finance","thesis","valuation","verification"]] = Field(default_factory=lambda:["finance","thesis","verification"],min_length=1)
    enabled: bool = True


class SkillSpec(ScopedExtension):
    path: str
    roles: list[ExtensionRole] = Field(default_factory=lambda:["finance","thesis","verification"],min_length=1)


class ToolSpec(ScopedExtension):
    description: str = Field(min_length=1,max_length=4000)
    command: list[str] = Field(min_length=1)
    input_schema: dict = Field(default_factory=lambda:{"type":"object","additionalProperties":True})
    env_refs: dict[str,str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30,gt=0,le=120)
    cost_cny: float = Field(default=0,ge=0)
    cost_kind: Literal["model","service"] = "service"
    cost_source: str | None = None
    cost_as_of: str | None = None

    @model_validator(mode="after")
    def contract(self):
        jsonschema.Draft202012Validator.check_schema(self.input_schema)
        if self.input_schema.get("type") != "object":raise ValueError("工具参数 schema 必须是 object")
        if self.cost_cny and not (self.cost_source and self.cost_as_of):raise ValueError("收费工具需声明费用上界来源和日期")
        return self


class MCPSpec(ScopedExtension):
    transport: Literal["stdio","streamable-http"] = "stdio"
    command: list[str] = Field(default_factory=list)
    url: str | None = None
    env_refs: dict[str,str] = Field(default_factory=dict)
    headers_env: dict[str,str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30,gt=0,le=120)
    cost_cny: float = Field(default=0,ge=0)
    cost_kind: Literal["model","service"] = "service"
    cost_source: str | None = None
    cost_as_of: str | None = None
    tools: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def transport_fields(self):
        from urllib.parse import urlparse
        if self.transport=="stdio" and not self.command:raise ValueError("stdio MCP 需命令参数数组")
        if self.transport=="streamable-http":
            parsed=urlparse(self.url or "")
            if parsed.scheme not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query:
                raise ValueError("MCP HTTP 地址须为不含凭据的 HTTP(S) URL")
        if self.cost_cny and not (self.cost_source and self.cost_as_of):raise ValueError("收费 MCP 需声明费用上界来源和日期")
        return self


@asynccontextmanager
async def mcp_session(spec, timeout=None):
    import httpx
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    timeout=min(timeout or spec["timeout_seconds"],spec["timeout_seconds"])
    async with asyncio.timeout(timeout):
        if spec["transport"]=="stdio":
            params=StdioServerParameters(command=spec["command"][0],args=spec["command"][1:],env=environment_values(spec["env_refs"]))
            with open(os.devnull,"w") as errors:
                async with stdio_client(params,errlog=errors) as streams:
                    async with ClientSession(*streams,read_timeout_seconds=timedelta(seconds=timeout)) as session:
                        await session.initialize();yield session
        else:
            async with httpx.AsyncClient(headers=environment_values(spec["headers_env"]),timeout=timeout) as client:
                async with streamable_http_client(spec["url"],http_client=client) as streams:
                    async with ClientSession(streams[0],streams[1],read_timeout_seconds=timedelta(seconds=timeout)) as session:
                        await session.initialize();yield session


async def discover_mcp(spec):
    tools=[]
    async with mcp_session(spec) as session:
        cursor=None
        while True:
            page=await session.list_tools(cursor=cursor)
            for tool in page.tools:
                jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                tools.append({"name":tool.name,"description":tool.description or tool.name,"input_schema":tool.inputSchema})
            cursor=page.nextCursor
            if not cursor:break
    secrets=list(environment_values(spec["env_refs"]).values())+list(environment_values(spec["headers_env"]).values())
    return redact(tools,secrets)


def copy_skill(source,destination):
    source=source.resolve()
    if destination.exists():raise ValueError("技能目标目录已存在，请使用新名称导入")
    excluded={".git",".venv","__pycache__",".env"}
    for path in source.rglob("*"):
        if any(part in excluded for part in path.relative_to(source).parts):continue
        if not path.resolve().is_relative_to(source):raise ValueError("技能资源不能指向导入目录以外")
    try:
        shutil.copytree(source,destination,ignore=shutil.ignore_patterns(*excluded))
    except Exception:
        if destination.exists():shutil.rmtree(destination)
        raise


async def run_command(command, arguments, timeout, env_refs=None):
    from mcp.client.stdio import get_default_environment
    credentials=environment_values(env_refs or {})
    process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,env=get_default_environment()|credentials,start_new_session=True)
    try:
        async with asyncio.timeout(timeout):
            process.stdin.write(json.dumps(arguments,ensure_ascii=False,allow_nan=False).encode()+b"\n")
            await process.stdin.drain();process.stdin.close()
            output=bytearray()
            while chunk:=await process.stdout.read(65_536):
                output.extend(chunk)
                if len(output)>1_000_000:raise ValueError("工具输出超过 1 MB 上限")
            await process.wait()
            if process.returncode:raise ValueError(f"命令工具退出码 {process.returncode}")
            value=json.loads(output)
            if not isinstance(value,dict):raise ValueError("命令工具需输出一个 JSON 对象")
            return redact(value,credentials.values())
    finally:
        if process.returncode is None:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            await process.wait()


class ExtensionCatalog:
    def __init__(self,store):self.store=store

    async def add(self,kind,name,data):
        if kind not in {"skills","mcp","tools"} or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",name):
            raise ValueError("扩展类别或名称无效")
        schema={"skills":SkillSpec,"mcp":MCPSpec,"tools":ToolSpec}[kind]
        spec=schema.model_validate(data).model_dump(mode="json")
        if kind=="skills":
            source=Path(spec["path"]).expanduser().resolve()
            if source.is_dir():source=source/"SKILL.md"
            if not source.is_file() or source.name!="SKILL.md":raise ValueError("skill 需 SKILL.md 文件或目录")
            if source.stat().st_size>128_000:raise ValueError("SKILL.md 超过 128 KB 上限")
            source.read_text(encoding="utf-8")
            destination=self.store.path.parent/"skills"/name
            copy_skill(source.parent,destination)
            spec["path"]=str(destination/"SKILL.md")
        elif kind=="mcp":
            spec["tools"]=await discover_mcp(spec)
        self.store.update(lambda c:c[kind].__setitem__(name,spec))
        return spec

    def enable(self,kind,name,enabled):
        if kind not in {"skills","mcp","tools"} or name not in self.store.load()[kind]:raise ValueError("扩展未注册")
        self.store.update(lambda c:c[kind][name].__setitem__("enabled",enabled))

    def snapshot(self,directory):
        config=self.store.load();snapshot={"skills":[],"mcp":{},"tools":{}}
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}",name) for kind in ("skills","mcp","tools") for name in config[kind]):
            raise ValueError("扩展配置中存在无效名称")
        for name,spec in config["skills"].items():
            spec=SkillSpec.model_validate(spec).model_dump()
            if not spec["enabled"]:continue
            source=Path(spec["path"])
            destination=Path(directory)/"capabilities/skills"/name
            copy_skill(source.parent,destination)
            text=(destination/"SKILL.md").read_text()
            hashes={str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.rglob("*") if p.is_file()}
            snapshot["skills"].append({"name":name,"roles":spec["roles"],"content":text,"source_hash":hashes["SKILL.md"],"resource_path":str(destination.resolve()),"resource_hashes":hashes})
        for kind,schema in (("mcp",MCPSpec),("tools",ToolSpec)):
            snapshot[kind]={name:schema.model_validate(spec).model_dump(mode="json") for name,spec in config[kind].items() if spec["enabled"]}
        return snapshot


class ExtensionRuntime:
    def __init__(self,snapshot):
        self.snapshot=snapshot;self.bindings={}
        for name,spec in snapshot.get("tools",{}).items():
            alias="ext_"+name if len(name)<=60 else "ext_"+name[:40]+"_"+hashlib.sha256(name.encode()).hexdigest()[:8]
            self.bindings[alias]=(spec,None)
        for server,spec in snapshot.get("mcp",{}).items():
            for tool in spec["tools"]:
                suffix=re.sub(r"[^A-Za-z0-9_]","_",tool["name"])[:24]
                digest=hashlib.sha256((server+":"+tool["name"]).encode()).hexdigest()[:8]
                alias=f"mcp_{server[:20]}_{suffix}_{digest}"
                self.bindings[alias]=(spec,tool)

    def contracts(self):
        return {name:{"description":(tool or spec)["description"],"arguments_schema":(tool or spec)["input_schema"]} for name,(spec,tool) in self.bindings.items()}

    def permissions(self,role):
        return [name for name,(spec,_) in self.bindings.items() if role in spec["roles"]]

    async def call(self,name,arguments,timeout):
        spec,tool=self.bindings[name]
        try:jsonschema.validate(arguments,(tool or spec)["input_schema"])
        except jsonschema.ValidationError:raise ValueError("扩展工具参数未通过 JSON Schema") from None
        timeout=min(timeout,spec["timeout_seconds"])
        if tool is None:return await run_command(spec["command"],arguments,timeout,spec["env_refs"])
        async with mcp_session(spec,timeout) as session:
            result=await session.call_tool(tool["name"],arguments)
            value=result.model_dump(mode="json",by_alias=True)
        secrets=list(environment_values(spec["env_refs"]).values())+list(environment_values(spec["headers_env"]).values())
        value=redact(value,secrets)
        if result.isError:raise ValueError("MCP 工具返回错误（已脱敏）")
        return value

    def read_resource(self,skill,path,role):
        spec=next((s for s in self.snapshot.get("skills",[]) if s["name"]==skill and role in s["roles"]),None)
        if spec is None:raise ValueError("技能或角色未授权")
        root=Path(spec["resource_path"]).resolve();file=(root/path).resolve()
        if not file.is_relative_to(root) or path not in spec["resource_hashes"]:raise ValueError("技能资源路径未授权")
        raw=file.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=spec["resource_hashes"][path]:raise ValueError("运行中的技能资源已改变")
        if len(raw)>128_000:raise ValueError("技能文本资源超过 128 KB")
        return {"skill":skill,"path":path,"text":raw.decode("utf-8"),"source_hash":spec["resource_hashes"][path]}


async def interactive_extensions(catalog):
    while True:
        action=ask("扩展模块：list 列表 / add 导入 / enable 启用 / disable 停用 / back 返回","list")
        if action=="back":return
        if action=="list":
            for kind in ("skills","mcp","tools"):
                for name,spec in catalog.store.load()[kind].items():print(f"{kind} {name}: {'启用' if spec['enabled'] else '停用'} / {','.join(spec['roles'])}")
        elif action in {"enable","disable"}:
            catalog.enable(ask("类别 skills/mcp/tools"),ask("名称"),action=="enable");print("已保存。")
        elif action=="add":
            kind=ask("类别 skills/mcp/tools","skills");name=ask("名称")
            if kind=="skills":data={"path":ask("SKILL.md 或技能目录路径")}
            else:
                path=Path(ask("扩展 JSON 描述文件路径"));data=json.loads(path.read_text())
            data["roles"]=[role.strip() for role in ask("可使用角色（逗号分隔）","finance,thesis,verification").split(",")]
            spec=await catalog.add(kind,name,data);print("已导入并启用。" if spec["enabled"] else "已导入，保持停用。")
        else:print("请输入 list、add、enable、disable 或 back。")
