"""Read-only native ADK view of controller-owned reports and calculations."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import time

from google.adk.artifacts.base_artifact_service import ArtifactVersion, BaseArtifactService
from google.genai import types

from .adk_runtime import TraceStore


def publish_outputs(engine):
    """Index existing bytes; never save a competing artifact payload."""
    directory = engine.directory
    business_version = engine.state["assumptions"]["version"]
    files = {"report.json": (f"report-v{business_version}.json", "application/json"),
             "report.md": (f"report-v{business_version}.md", "text/markdown"),
             "calculations.json": (f"calculations-v{business_version}.json", "application/json")}
    with (directory / ".adk-artifacts.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "adk-artifacts.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        for name, (filename, mime) in files.items():
            file = directory / filename
            if not file.is_file():
                continue
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            versions = manifest.setdefault(name, [])
            if not any(v["source"] == filename and v["sha256"] == digest for v in versions):
                versions.append({"version": len(versions), "source": filename, "sha256": digest,
                                 "business_version": business_version, "mime_type": mime, "create_time": time.time()})
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)


class ResearchArtifactService(BaseArtifactService):
    """Scope and hash checks resolve only indexed final outputs, never materials."""

    def __init__(self, storage):
        self.storage = Path(storage).resolve()

    def _manifest(self, app_name, user_id, session_id):
        if session_id is None:
            return None, {}
        matches = [path.parent for path in self.storage.glob("run-*/adk-scopes.json")
                   if path.parent.resolve().is_relative_to(self.storage)
                   and TraceStore(path.parent).permits(app_name, user_id, session_id)]
        if len(matches) != 1:
            return None, {}
        directory = matches[0]
        path = directory / "adk-artifacts.json"
        manifest = json.loads(path.read_text()) if path.exists() else {}
        prefixes = {"report.json": ("report", "json"), "report.md": ("report", "md"), "calculations.json": ("calculations", "json")}
        allowed = {}
        for name, (prefix, suffix) in prefixes.items():
            records = [record for record in manifest.get(name, []) if record["source"] ==
                       f"{prefix}-v{record['business_version']}.{suffix}"]
            if records:
                allowed[name] = records
        return directory, allowed

    @staticmethod
    def _metadata(directory, record):
        return ArtifactVersion(version=record["version"],
            canonical_uri=f"yantra://{directory.name}/{record['source']}#sha256={record['sha256']}",
            custom_metadata={"run_id": directory.name, "business_version": record["business_version"],
                             "sha256": record["sha256"], "owner": "research_controller"},
            create_time=record["create_time"], mime_type=record["mime_type"])

    async def save_artifact(self, *, app_name, user_id, filename, artifact, session_id=None, custom_metadata=None):
        raise PermissionError("Research artifacts are written only by the controller.")

    async def delete_artifact(self, *, app_name, user_id, filename, session_id=None):
        raise PermissionError("Research artifact deletion uses the business storage policy.")

    async def list_artifact_keys(self, *, app_name, user_id, session_id=None):
        _, manifest = self._manifest(app_name, user_id, session_id)
        return sorted(manifest)

    async def list_versions(self, *, app_name, user_id, filename, session_id=None):
        _, manifest = self._manifest(app_name, user_id, session_id)
        return [record["version"] for record in manifest.get(filename, [])]

    async def list_artifact_versions(self, *, app_name, user_id, filename, session_id=None):
        directory, manifest = self._manifest(app_name, user_id, session_id)
        return [self._metadata(directory, record) for record in manifest.get(filename, [])]

    async def get_artifact_version(self, *, app_name, user_id, filename, session_id=None, version=None):
        records = await self.list_artifact_versions(app_name=app_name, user_id=user_id,
            filename=filename, session_id=session_id)
        return next((record for record in reversed(records) if version is None or record.version == version), None)

    async def load_artifact(self, *, app_name, user_id, filename, session_id=None, version=None):
        directory, manifest = self._manifest(app_name, user_id, session_id)
        record = next((r for r in reversed(manifest.get(filename, [])) if version is None or r["version"] == version), None)
        if record is None:
            return None
        path = (directory / record["source"]).resolve()
        if path.parent != directory.resolve() or not path.is_file():
            return None
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            return None
        return types.Part.from_bytes(data=content, mime_type=record["mime_type"])
