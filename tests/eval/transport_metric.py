"""A transport gate; do not interpret a scripted response as LLM behavior."""
import json


def evaluate(instance):
    responses = instance.get("responses", [])
    snapshots = []
    for response in responses:
        for part in response.get("response", {}).get("parts", []):
            try:
                snapshots.append(json.loads(part.get("text", "")))
            except ValueError:
                continue
    passed = bool(snapshots) and all(isinstance(value, dict) and value.get("run_id") and
        value.get("status") in {"waiting", "partial", "completed", "failed", "cancelled"} for value in snapshots)
    return {"score": int(passed)}
