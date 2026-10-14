"""Synthetic worker skill/context comparison; input byte bounds are not provider tokens."""
import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[1] / "tests")]
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field
from yantra.adk_app import run_worker
from yantra.configuration import ConfigStore
from yantra.extensions import ExtensionCatalog
from yantra.policies import OfflineDemoBackend
from yantra.research import RawBackend, RawEngine
from yantra.schemas import MainAction
from test_v2 import PRICE, TEXT


class Provider(BaseLlm):
    model: str = "synthetic-measure"
    native: bool
    use_skill: bool
    input_bytes: list[int] = Field(default_factory=list)
    async def generate_content_async(self, request, stream=False):
        payload = json.loads(request.contents[-1].parts[0].text)
        task, local = payload["task"], payload["local"]
        self.input_bytes.append(len((str(request.config.system_instruction) + request.contents[-1].parts[0].text).encode()))
        demo = OfflineDemoBackend()
        count = local["actual_calls"]
        if self.native and self.use_skill and count == 0:
            action = demo._tool("load_skill", {"skill_name": local["skill_catalogue"][0]["skill_name"]})
        elif count < (2 if not self.use_skill else 1 + int(self.native)):
            action = demo._tool("read_skill_resource", {"skill": "guide0", "path": "reference.txt"})
        else:
            originals = [receipt["response"]["data"] for receipt in local["tool_outputs"] if receipt["tool"] == "read_skill_resource"]
            action = demo._result(task, local, data={"resource_hash": originals[0]["source_hash"]})
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(action))]))


async def measure(output, samples):
    cases = []
    for scale, count, body_size, resource_size in (("small", 1, 128, 256), ("large", 10, 8192, 16384)):
        for use_skill in (False, True):
            for native, compact in ((False, False), (False, True), (True, True)):
                runs = []
                for _ in range(samples):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        store = ConfigStore(root / "config.json")
                        catalogue = ExtensionCatalog(store)
                        for i in range(count):
                            source = root / f"skill{i}"
                            source.mkdir()
                            (source / "SKILL.md").write_text("# Synthetic rules\n" + "规则 " * (body_size // 7))
                            (source / "reference.txt").write_text("evidence value=123.5\n" + "x" * resource_size)
                            await catalogue.add("skills", f"guide{i}", {"path": str(source), "roles": ["finance"], "loading": "native" if native else "eager"})
                        material = root / "input.txt"
                        material.write_text(TEXT)
                        backend = RawBackend("test", "https://unused.invalid/v1", "synthetic-only")
                        provider = Provider(native=native, use_skill=use_skill)
                        backend._model = provider
                        engine = RawEngine.create_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13", "market": "CN", "security_code": "600000"},
                            [material], backend=backend, pricing=PRICE, settings=store)
                        if not compact:
                            engine.state["capabilities"].pop("worker_context")
                            engine._save()
                            engine = RawEngine(engine.directory, engine.state, backend)
                        task = engine._make_task(MainAction(action="delegate_task", target="finance", purpose="collect"))
                        started = time.perf_counter()
                        result = await run_worker(engine, task.model_dump())
                        elapsed = time.perf_counter() - started
                        runs.append({"elapsed_seconds": elapsed, "model_calls": engine.state["budget"]["model_calls"],
                            "tool_calls": engine.state["budget"]["tool_calls"], "request_text_bytes": sum(provider.input_bytes),
                            "input_token_byte_bound": sum(receipt["input_token_bound"] for receipt in engine.state["model_ledger"]),
                            "resource_hash": result["data"]["resource_hash"], "status": result["status"]})
                cases.append({"scale": scale, "skills": count, "use_skill": use_skill, "native": native, "compact_receipts": compact,
                    "runs": runs, "elapsed_median_seconds": statistics.median(run["elapsed_seconds"] for run in runs)})
    result = {"scope": "controlled synthetic worker; no real model judgment", "input_tokens": "unknown; UTF-8 byte bounds only",
        "cost": "no paid calls; real fees unknown", "cases": cases}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(measure(args.output, args.samples))
