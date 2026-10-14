"""Compare controlled stdio MCP calls, process reuse and cleanup; no paid services."""
import argparse
import ast
import asyncio
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--samples", type=int, default=5)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
fixture = Path(__file__).resolve().parents[1] / "tests/test_adk_mcp.py"
tree = ast.parse(fixture.read_text())
server_text = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
    and any(isinstance(target, ast.Name) and target.id == "SERVER" for target in node.targets))
sys.path.insert(0, str(args.checkout.resolve()))
from yantra.extensions import ExtensionRuntime, MCPSpec, discover_mcp


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def measure():
    samples = []
    for _ in range(args.samples):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = root / "server.py"
            server.write_text(server_text)
            spec = MCPSpec(transport="stdio", command=[sys.executable, str(server), str(root)], roles=["finance"]).model_dump()
            spec["tools"] = await discover_mcp(spec)
            runtime = ExtensionRuntime({"mcp": {"local": spec}})
            name = next(iter(runtime.bindings))
            started = time.perf_counter()
            try:
                values = [await runtime.call(name, {"text": text}, 10) for text in ("one", "two")]
            finally:
                if hasattr(runtime, "close"):
                    await runtime.close()
            elapsed = time.perf_counter() - started
            data = [value.get("structuredContent") or json.loads(value["content"][0]["text"]) for value in values]
            pids = [int(pid) for pid in (root / "pids").read_text().splitlines()]
            samples.append({"elapsed_seconds": elapsed, "runtime_processes": len(pids) - 1,
                "reuse": data[0]["pid"] == data[1]["pid"], "tool_calls": len(data),
                "values": [value["text"] for value in data], "all_processes_closed": all(not alive(pid) for pid in pids)})
    result = {"scope": "synthetic local stdio, excluding catalog discovery from elapsed time", "samples": samples,
        "elapsed_median_seconds": statistics.median(sample["elapsed_seconds"] for sample in samples),
        "model_calls": 0, "cost": "no paid service; remote costs and latency unknown"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


asyncio.run(measure())
