"""Reproducible synthetic HTTP measurement; never calls a remote provider."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument("--samples", type=int, default=5)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
sys.path[:0] = [str(args.checkout.resolve()), str(args.checkout.resolve() / "tests")]

from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService
from google.genai import types
from test_adk_runtime import ScriptedProvider
from test_adk_structure import test_materials
from yantra.api import create_app
from yantra.backend import RealModelBackend
from yantra.engine import Engine


class MeasuredProvider(ScriptedProvider):
    input_bytes: list[int] = []

    async def generate_content_async(self, llm_request, stream=False):
        instruction = llm_request.config.system_instruction
        if not isinstance(instruction, str):
            instruction = instruction.model_dump_json() if instruction else ""
        texts = [part.text or "" for content in llm_request.contents for part in content.parts or []]
        self.input_bytes.append(len((instruction + "".join(texts)).encode()))
        async for response in super().generate_content_async(llm_request, stream):
            response.usage_metadata = types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5, total_token_count=15)
            yield response


samples = []
for _ in range(args.samples):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        storage = root / "runs"
        inputs = test_materials(root / "inputs")
        backend = RealModelBackend("test", "https://unused.invalid/v1", "synthetic-only")
        provider = MeasuredProvider()
        backend._model = provider

        def factory(company="DEMO", run_id=None):
            return Engine.load(storage, run_id, backend=backend) if run_id else Engine.create(storage,
                {"company": company, "horizon": "12个月", "cutoff": "2025-12-31"},
                mode="test", backend=backend, materials_dir=inputs)

        started = time.perf_counter()
        with patch.dict(os.environ, {"YANTRA_RUNS_DIR": str(storage)}), patch("yantra.engine.create_adk_engine", side_effect=factory):
            with TestClient(create_app(session_service=InMemorySessionService())) as client:
                session = client.post("/apps/yantra/users/measure/sessions", json={}).json()["id"]
                for text in ("DEMO", "继续", "确认"):
                    response = client.post("/run_sse", json={"appName": "yantra", "userId": "measure", "sessionId": session,
                        "newMessage": {"role": "user", "parts": [{"text": text}]}})
                    response.raise_for_status()
                trace = client.get(f"/apps/yantra/users/measure/sessions/{session}/research/trace", params={"limit": 500}).json()
        elapsed = time.perf_counter() - started
        state = Engine.load(storage, trace["run_id"]).state
        semantic = {key: state[key] for key in ("status", "report", "hard_checks", "calculations", "issues")}
        encoded = json.dumps(semantic, ensure_ascii=False, sort_keys=True)
        for index, task in enumerate(state["tasks"]):
            encoded = encoded.replace(task["task_id"], f"TASK-{index}")
        encoded = encoded.replace(str(root), "TEMP").replace(state["run_id"], "RUN")
        encoded = re.sub(r"adk-[a-f0-9-]+", "CALL", encoded)
        samples.append({"elapsed_seconds": elapsed, "status": state["status"],
            "semantic_hash": hashlib.sha256(encoded.encode()).hexdigest(),
            "provider_calls": len(provider.payloads), "model_calls": state["budget"]["model_calls"],
            "tool_calls": state["budget"]["tool_calls"], "input_utf8_bytes": sum(provider.input_bytes),
            "invocations": sum(e["kind"] == "invocation_started" for e in trace["events"]),
            "decision_invocations": sum(e["kind"] == "invocation_started" and e["runner_kind"] == "decision" for e in trace["events"]),
            "trace_bytes": (storage / trace["run_id"] / "adk-trace.jsonl").stat().st_size})

result = {"scope": "synthetic ADK/ASGI HTTP, not remote model performance", "samples": samples,
    "elapsed_median_seconds": statistics.median(s["elapsed_seconds"] for s in samples),
    "usage": "10 input / 5 output synthetic tokens per request; no fees; provider token count unknown"}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(result, ensure_ascii=False))
