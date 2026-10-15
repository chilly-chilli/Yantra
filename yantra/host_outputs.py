"""Controller-published immutable output generations for the host boundary."""
from __future__ import annotations

import base64
import hashlib
import json

from .cli_contract import CLIError


def digest(content):
    return hashlib.sha256(content).hexdigest()


def qualification(state):
    summary = (state.get("report") or {}).get("投资结论摘要", {})
    verification = state.get("artifacts", {}).get("verification", {})
    current_verification = verification.get("assumptions_version") == state["assumptions"]["version"]
    return {"research_status": state["status"], "assumptions_version": state["assumptions"]["version"],
            "confirmed": state["assumptions"]["confirmed"],
            "authorization_source": state["assumptions"].get("authorization_source", "none"),
            "valuation_status": summary.get("valuation_status", "unverified"),
            "verification_passed": bool(current_verification and verification.get("data", {}).get("passed")
                                        and (state.get("hard_checks") or {}).get("verification_passed")),
            "gaps": summary.get("gaps", [])}


def index(directory):
    path = directory / "host-outputs.json"
    return json.loads(path.read_text()) if path.exists() else []


def publish(engine):
    """Called only by the controller while it owns the run lock."""
    if not engine.state.get("host_contract_version"):
        return
    directory, state = engine.directory, engine.state
    files = {}
    version = state["assumptions"]["version"]

    def save(name, content, mime, **metadata):
        sha = digest(content)
        target = directory / "outputs" / sha
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            if digest(target.read_bytes()) != sha:
                raise ValueError("immutable output content was modified")
        else:
            with target.open("xb") as handle:
                handle.write(content)
                handle.flush()
                import os
                os.fsync(handle.fileno())
        files[name] = {"source": "outputs/" + sha, "sha256": sha, "size": len(content),
                       "mime_type": mime, **metadata}

    for name, mime in (("report.json", "application/json"), ("report.md", "text/markdown"),
                       ("calculations.json", "application/json")):
        stem, suffix = name.rsplit(".", 1)
        save(name, (directory / f"{stem}-v{version}.{suffix}").read_bytes(), mime)
    for material in state["input_snapshot"].get("manifest", {}).get("materials", []):
        identifier = material["id"]
        source = (directory / "materials" / material["file"]).resolve()
        if not source.is_relative_to((directory / "materials").resolve()):
            raise ValueError("evidence path outside materials")
        content = source.read_bytes()
        snapshot = state["input_snapshot"].get("materials", {}).get(identifier, {})
        expected = material.get("source_hash") or snapshot.get("source_hash")
        if expected and digest(content) != expected:
            raise ValueError("source hash mismatch")
        save("evidence/" + identifier, content, "application/octet-stream",
             locator={"material_id": identifier, "url": material.get("url"),
                      "source_hash": expected or digest(content)})
        # Parsed anchors are explicitly separate from original bytes.
        save("anchors/" + identifier, json.dumps(snapshot.get("chunks", snapshot.get("text", "")),
             ensure_ascii=False, sort_keys=True).encode(), "application/json",
             locator={"material_id": identifier, "original_artifact": "evidence/" + identifier})
    records = index(directory)
    candidate = {"assumptions_version": version, "qualification": qualification(state), "files": files,
                 "assumptions": state["assumptions"]}
    if records and all(records[-1].get(k) == v for k, v in candidate.items()):
        return
    records.append({"version": len(records) + 1, **candidate})
    temporary = directory / "host-outputs.tmp"
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(directory / "host-outputs.json")


def public_index(directory):
    return [{k: v for k, v in row.items() if k != "assumptions"} for row in index(directory)]


def read(directory, request):
    records = index(directory)
    record = next((row for row in reversed(records) if request.version is None or row["version"] == request.version), None)
    if record is None:
        raise CLIError("output_not_found", "成果版本不存在；先查询 status.outputs", field="version")
    entry = record["files"].get(request.artifact)
    if entry is None:
        raise CLIError("artifact_not_found", "产物标识不存在；只能读取 status.outputs 中的标识", field="artifact")
    target = (directory / entry["source"]).resolve()
    if not target.is_relative_to(directory.resolve()) or not target.is_file():
        raise CLIError("artifact_unavailable", "成果缺失或路径越界，不能读取")
    content = target.read_bytes()
    if digest(content) != entry["sha256"]:
        raise CLIError("artifact_hash_mismatch", "成果字节已改变，拒绝以历史版本返回")
    chunk = content[request.offset:request.offset + request.length]
    textual = entry["mime_type"] in {"application/json", "text/markdown", "text/plain"}
    # Base64 preserves exact requested byte boundaries, including split UTF-8 characters.
    result = {"run_id": request.run_id, "version": record["version"], "artifact": request.artifact,
              "qualification": record["qualification"], "sha256": entry["sha256"],
              "size": len(content), "offset": request.offset, "bytes_returned": len(chunk),
              "complete": request.offset == 0 and len(chunk) == len(content),
              "next_offset": request.offset + len(chunk) if request.offset + len(chunk) < len(content) else None,
              "encoding": "base64", "content": base64.b64encode(chunk).decode(),
              "mime_type": entry["mime_type"], "locator": entry.get("locator")}
    if textual:
        result["text"] = chunk.decode("utf-8", errors="replace")
    return result
