"""Pinned MCP 1.30 stdio transport over the shared local host service."""
from __future__ import annotations

import asyncio
import json

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .cli_contract import exception_error
from .host_contract import INPUTS


DESCRIPTIONS = {
    "schema": "Discover versioned Yantra inputs and trust boundary. No model calls.",
    "doctor": "Local configuration and path diagnosis; no network or research calls. Host command permissions are caller-reported.",
    "submit": "Persist an idempotent research intent and start a detached local executor; return before research completes. Formal research uses Yantra's configured paid services and budget.",
    "list": "Find local workspace research with stable pagination; no model calls.",
    "status": "Read status, revision, pending ID, immutable outputs and operation receipts. Failed/partial research remains a successful query.",
    "read": "Read bounded bytes of an indexed immutable report or evidence version, with hash and qualification. Never read arbitrary local files.",
    "respond": "Explicitly respond to a pending identity/authorization or change assumptions using operation ID and state revision. Accepted is distinct from applied/rejected; changes trigger recalculation and independent verification.",
    "cancel": "Request cancellation; query status to confirm actual stop. Disconnecting MCP does not cancel research.",
    "resume": "Explicitly restart interrupted research, preserving original budget/deadline. Never replay an unknown external operation. New materials require a linked new submission.",
}


async def serve(service):
    server = Server("yantra_mcp", version="1")

    @server.list_tools()
    async def list_tools():
        tools = []
        for operation, model in INPUTS.items():
            request_schema = model.model_json_schema()
            definitions = request_schema.pop("$defs", {})
            tools.append(types.Tool(name="yantra_" + operation, description=DESCRIPTIONS[operation],
            inputSchema={"type": "object", "properties": {"request": request_schema},
                         "required": ["request"], "additionalProperties": False, "$defs": definitions},
            annotations=types.ToolAnnotations(readOnlyHint=operation in {"schema", "doctor", "list", "status", "read"},
                destructiveHint=False, idempotentHint=True, openWorldHint=operation in {"submit", "respond", "resume"})))
        return tools

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        from .cli_contract import CLIError
        operation = name.removeprefix("yantra_")
        result = {"schema_version": "1", "command": "host " + operation if operation in INPUTS else "host"}
        try:
            if not name.startswith("yantra_") or set(arguments or {}) != {"request"}:
                raise CLIError("argument_error", "工具输入需要且只接受 request 对象")
            result["data"] = await asyncio.to_thread(service.call, operation, arguments["request"])
        except Exception as exc:
            result["error"] = exception_error(exc).error
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, allow_nan=False))],
                                    structuredContent=result, isError="error" in result)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
