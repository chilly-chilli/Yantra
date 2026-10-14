"""Actual loopback ADK/agents-cli transport checks using fictional fixed decisions."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_adk_structure import test_materials
spec = importlib.util.spec_from_file_location("transport_metric", ROOT / "tests/eval/transport_metric.py")
metric = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metric)


def send(client, session, text):
    response = client.post("/run_sse", json={"appName": "yantra", "userId": "offline", "sessionId": session,
        "newMessage": {"role": "user", "parts": [{"text": text}]}})
    response.raise_for_status()
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    return next(json.loads(part["text"]) for event in reversed(events)
        for part in event.get("content", {}).get("parts", []) if part.get("text"))


def check(output, cli):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        storage = root / "runs"
        materials = test_materials(root / "materials")
        environment = {key: value for key, value in os.environ.items() if not key.startswith("YANTRA_")}
        environment.update(YANTRA_RUNS_DIR=str(storage), YANTRA_MATERIALS_DIR=str(materials), YANTRA_MODE="offline-demo")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        server = None
        log = (root / "server.log").open("w")

        def stop():
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

        def start():
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "yantra.fast_api_app:app", "--host", "127.0.0.1",
                "--port", str(port)], cwd=ROOT, env=environment, stdout=log, stderr=log)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Local offline server exited before readiness.")
                try:
                    if httpx.get(url + "/list-apps", timeout=1).json() == ["yantra"]:
                        return process
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(.1)
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError("Local offline server readiness timed out.")

        try:
            server = start()
            with httpx.Client(base_url=url, timeout=30) as client:
                session = client.post("/apps/yantra/users/offline/sessions", json={}).json()["id"]
                first = send(client, session, "DEMO")
                assert first["status"] == "waiting"
                state_path = storage / first["run_id"] / "state.json"
                before = json.loads(state_path.read_text())["budget"]
                assert send(client, session, "继续")["status"] == "waiting"
                assert json.loads(state_path.read_text())["budget"] == before
            stop()
            server = start()
            with httpx.Client(base_url=url, timeout=30) as client:
                assert client.get(f"/apps/yantra/users/offline/sessions/{session}").status_code == 200
                final = send(client, session, "确认")
                assert final["status"] == "partial"
                path = f"/apps/yantra/users/offline/sessions/{session}/research/trace"
                trace = client.get(path, params={"limit": 500}).json()
                assert {task["target"] for task in trace["tasks"]} == {"finance", "thesis", "valuation", "verification"}
                calls = [tool["name"] for event in trace["events"] for tool in event.get("tools", []) if tool["kind"] == "call"]
                assert "valuation" in calls and "check_consistency" in calls
                artifacts = client.get(f"/apps/yantra/users/offline/sessions/{session}/artifacts").json()
                assert artifacts == ["calculations.json", "report.json", "report.md"]
                assert client.get(f"/apps/yantra/users/stranger/sessions/{session}/research/trace").status_code == 404
                cli_summary = {"status": "not_requested"}
                if cli:
                    generated = root / "generated.json"
                    command = subprocess.run([cli, "eval", "generate", "--dataset", str(ROOT / "tests/eval/datasets/cli-smoke.json"),
                        "--url", url, "--app-name", "yantra", "--concurrency", "1", "--output", str(generated)],
                        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)
                    assert command.returncode == 0, "agents-cli generation failed"
                    cases = json.loads(generated.read_text())["eval_cases"]
                    assert [case["eval_case_id"] for case in cases] == ["offline_waiting_transport"]
                    assert all(metric.evaluate(case)["score"] == 1 for case in cases)
                    snapshot = json.loads(cases[0]["responses"][-1]["response"]["parts"][0]["text"])
                    assert snapshot["status"] == "waiting"
                    scopes = json.loads((storage / snapshot["run_id"] / "adk-scopes.json").read_text())
                    scope = scopes[0]
                    canonical = client.get(f"/apps/yantra/users/{scope['user_id']}/sessions/{scope['session_id']}/research/trace",
                        params={"limit": 500}).json()
                    assert len(canonical["events"]) > len(cases[0]["agent_data"]["turns"][0]["events"])
                    cli_summary = {"status": "passed", "case_ids": [case["eval_case_id"] for case in cases],
                        "converted_events": sum(len(turn["events"]) for turn in cases[0]["agent_data"]["turns"]),
                        "canonical_events": len(canonical["events"]), "roles": sorted({task["target"] for task in canonical["tasks"]})}
            result = {"scope": "actual loopback HTTP, SQLite restart, fixed fictional decisions; not LLM behavior evaluation",
                "status": "passed", "first_status": first["status"], "final_status": final["status"],
                "waiting_budget_unchanged": True, "tools": calls, "artifacts": artifacts,
                "trace": trace, "agents_cli": cli_summary, "paid_model_calls": 0}
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({key: value for key, value in result.items() if key != "trace"}, ensure_ascii=False))
        finally:
            stop()
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agents-cli", help="Existing CLI executable; never installs or upgrades it")
    args = parser.parse_args()
    check(args.output, args.agents_cli)
