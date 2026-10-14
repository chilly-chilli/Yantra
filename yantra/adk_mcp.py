"""Frozen MCP permissions around ADK's native connection/toolset lifecycle."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import json
import hashlib
import logging
import os
import httpx

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_tool import McpTool
from google.adk.tools.mcp_tool.mcp_session_manager import MCPSessionManager, StdioConnectionParams, StreamableHTTPConnectionParams
from mcp import StdioServerParameters

from .configuration import environment_values, redact


_mcp_observation = ContextVar("yantra_mcp_observation", default=False)


def _declared_http_client(headers=None, timeout=None, auth=None):
    return httpx.AsyncClient(headers=headers, timeout=timeout, auth=auth, follow_redirects=False)


class _SafeDiagnostic(logging.Filter):
    def filter(self, record):
        if _mcp_observation.get():
            # Native handlers can log arbitrary provider errors and tracebacks.
            record.msg = "MCP transport diagnostic; provider details suppressed."
            record.args = ()
            record.exc_info = None
            record.stack_info = None
        return True


class FrozenMCPError(ValueError):
    pass


class _DeclaredCredentials(MCPSessionManager):
    async def _get_mtls_transport(self):
        # ADK 2.11.0 otherwise probes ADC for every HTTP endpoint by default.
        # The Yantra contract authorizes only the declared environment refs.
        return None


class _DataMcpTool(McpTool):
    @property
    def mcp_app_resource_uri(self):
        # Metadata remains data; no remote widget can write ADK event actions.
        return None


class _FrozenMcpToolset(McpToolset):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.frozen_names = kwargs["tool_filter"]
        self._mcp_session_manager = _DeclaredCredentials(self.connection_params, errlog=self.errlog)

    async def get_tools(self, readonly_context=None):
        # Fixed ADK get_tools reads one page and drops reserved raw names.
        # Aliased business tools need all pages, without native transfer dispatch.
        async def pages(session):
            tools, cursor = [], None
            while True:
                page = await session.list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.nextCursor
                if not cursor:
                    return tools
        raw = await self._execute_with_session(pages, "MCP tool discovery failed", readonly_context)
        tools = []
        for definition in raw:
            if self.frozen_names is not None and definition.name not in self.frozen_names:
                continue
            alias = "yantra_rpc_" + hashlib.sha256(definition.name.encode()).hexdigest()[:16]
            tool = _DataMcpTool(mcp_tool=definition.model_copy(update={"name": alias}), mcp_session_manager=self._mcp_session_manager)
            tool._mcp_tool = definition  # RPC identity, distinct from the safe ADK declaration name.
            tools.append(tool)
        return tools


class NativeMCP:
    def __init__(self, spec, *, frozen=True):
        self.spec = spec
        self.secrets = list(environment_values(spec["env_refs"]).values()) + list(environment_values(spec["headers_env"]).values())
        for name in ("google.adk.tools.mcp_tool.mcp_tool", "google.adk.tools.mcp_tool.mcp_toolset",
                     "google.adk.tools.mcp_tool.mcp_session_manager", "mcp.shared.session",
                     "mcp.client.stdio", "mcp.client.streamable_http"):
            logger = logging.getLogger(name)
            if not any(isinstance(f, _SafeDiagnostic) for f in logger.filters):
                logger.addFilter(_SafeDiagnostic())
        self.errors = open(os.devnull, "w")
        if spec["transport"] == "stdio":
            connection = StdioConnectionParams(server_params=StdioServerParameters(
                command=spec["command"][0], args=spec["command"][1:], env=environment_values(spec["env_refs"])),
                timeout=spec["timeout_seconds"])
        else:
            connection = StreamableHTTPConnectionParams(url=spec["url"],
                headers=environment_values(spec["headers_env"]), timeout=spec["timeout_seconds"],
                sse_read_timeout=spec["timeout_seconds"], httpx_client_factory=_declared_http_client)
        token = _mcp_observation.set(True)
        try:
            self.toolset = _FrozenMcpToolset(connection_params=connection,
                tool_filter=[tool["name"] for tool in spec["tools"]] if frozen else None,
                tool_list_cache_ttl_seconds=None, errlog=self.errors, use_mcp_resources=False)
        finally:
            _mcp_observation.reset(token)

    async def discover(self):
        token = _mcp_observation.set(True)
        try:
            async with asyncio.timeout(self.spec["timeout_seconds"]):
                tools = await self.toolset.get_tools()
                return redact([{"name": tool.raw_mcp_tool.name,
                    "description": tool.raw_mcp_tool.description or tool.raw_mcp_tool.name,
                    "input_schema": tool.raw_mcp_tool.inputSchema} for tool in tools], self.secrets)
        except Exception:
            raise ValueError("MCP 工具发现失败，服务详情未输出。") from None
        finally:
            _mcp_observation.reset(token)

    async def call(self, frozen_tool, arguments, timeout):
        token = _mcp_observation.set(True)
        try:
            async with asyncio.timeout(timeout):
                tools = await self.toolset.get_tools()
                tool = next((tool for tool in tools if tool.raw_mcp_tool.name == frozen_tool["name"]), None)
                if tool is None:
                    raise FrozenMCPError("冻结的 MCP 工具不可用")
                live = redact({"name": tool.raw_mcp_tool.name,
                    "description": tool.raw_mcp_tool.description or tool.raw_mcp_tool.name,
                    "input_schema": tool.raw_mcp_tool.inputSchema}, self.secrets)
                if live != frozen_tool:
                    raise FrozenMCPError("MCP 工具定义与运行快照不一致")
                value = redact(await tool.run_async(args=arguments, tool_context=None), self.secrets)
                if value.get("isError") or value.get("error"):
                    raise FrozenMCPError("MCP 工具返回错误（已脱敏）")
                if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 1_000_000:
                    raise FrozenMCPError("MCP 工具输出超过 1 MB")
                return value
        except (TimeoutError, FrozenMCPError):
            raise
        except Exception:
            raise ValueError("MCP 调用失败，服务详情未输出。") from None
        finally:
            _mcp_observation.reset(token)

    async def close(self):
        token = _mcp_observation.set(True)
        try:
            await self.toolset.close()
        finally:
            self.errors.close()
            _mcp_observation.reset(token)
