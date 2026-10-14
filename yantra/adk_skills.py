"""Read-only SkillToolset on run-frozen, role-authorized resources."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re

from google.adk.skills.models import Frontmatter, Resources, Skill
from google.adk.tools.skill_toolset import SkillToolset, ListSkillsTool, LoadSkillTool, LoadSkillResourceTool
import jsonschema
import yaml


NATIVE_SKILL_TOOLS = ("list_skills", "load_skill", "load_skill_resource")


def alias(name):
    base = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:40].rstrip("-")
    return base + "-" + hashlib.sha256(name.encode()).hexdigest()[:8]


def description(spec):
    text = spec["content"]
    if text.startswith("---\n"):
        try:
            metadata = yaml.safe_load(text.split("---", 2)[1])
            if isinstance(metadata, dict) and isinstance(metadata.get("description"), str) and metadata["description"].strip():
                return metadata["description"].strip()[:1024]
        except yaml.YAMLError:
            pass
    return next((line.lstrip("# ")[:160] for line in text.splitlines() if line.strip()), spec["name"])


def resource_name(path):
    # Preserve the entire original path, avoiding root/reference name collisions.
    # Scripts and assets are text references, never executable Script objects.
    return "references/" + path


def contracts():
    prototypes = (ListSkillsTool(None), LoadSkillTool(None), LoadSkillResourceTool(None))
    return {tool.name: {"description": tool.description, "arguments_schema": tool._get_declaration().parameters_json_schema}
            for tool in prototypes}


class FrozenSkills:
    def __init__(self, runtime, role, allowed_tools=None):
        self.runtime, self.role = runtime, role
        self.allowed_tools = [name for name in NATIVE_SKILL_TOOLS if allowed_tools is None or name in allowed_tools]
        self.specs = {alias(spec["name"]): spec for spec in runtime.snapshot.get("skills", []) if role in spec["roles"] and spec.get("loading")=="native" and self.allowed_tools}
        self.skills = {name: Skill(frontmatter=Frontmatter(name=name, description=description(spec)),
                                  instructions=spec["content"], resources=Resources()) for name, spec in self.specs.items()}
        # ADK treats an empty list as no filter; the predicate must deny all.
        self.toolset = SkillToolset(skills=list(self.skills.values()), tool_filter=lambda tool, context: tool.name in self.allowed_tools)
        self.loaded = set()

    def catalogue(self):
        return [{"skill_name": name, "name": spec["name"], "description": description(spec), "source_hash": spec["source_hash"],
                 "resources": [{"file_path": resource_name(path), "original_path": path, "source_hash": digest}
                               for path, digest in spec["resource_hashes"].items() if path != "SKILL.md"]}
                for name, spec in self.specs.items()]

    def instructions(self):
        return [self.specs[name] for name in self.specs if name in self.loaded]

    async def call(self, name, arguments, context):
        if name not in self.allowed_tools:
            raise ValueError("技能工具未授权")
        try:
            jsonschema.validate(arguments, contracts()[name]["arguments_schema"])
        except jsonschema.ValidationError:
            raise ValueError("技能工具参数未通过冻结接口校验") from None
        source_hash = None
        if name != "list_skills":
            skill_name = arguments["skill_name"]
            spec = self.specs.get(skill_name)
            if spec is None:
                raise ValueError("技能或角色未授权")
            # Revalidate the original bytes even if the native model is already cached.
            self.runtime.read_resource(spec["name"], "SKILL.md", self.role)
            source_hash = spec["source_hash"]
            if name == "load_skill_resource":
                path = next((path for path in spec["resource_hashes"] if path != "SKILL.md" and resource_name(path) == arguments["file_path"]), None)
                if path is None or ".." in PurePosixPath(arguments["file_path"]).parts:
                    raise ValueError("技能资源路径未授权")
                resource = self.runtime.read_resource(spec["name"], path, self.role)
                prefix, key = resource_name(path).split("/", 1)
                getattr(self.skills[skill_name].resources, prefix)[key] = resource["text"]
                source_hash = resource["source_hash"]
        tools = {tool.name: tool for tool in await self.toolset.get_tools()}
        result = await tools[name].run_async(args=arguments, tool_context=context)
        if isinstance(result, dict) and result.get("error"):
            raise ValueError("冻结技能读取失败")
        if name == "load_skill":
            self.loaded.add(arguments["skill_name"])
        return {"result": result, "source_hash": source_hash}


def compact_receipts(local, *, loaded_skills=False):
    """Keep the first exact receipt; reference duplicate bytes without dropping facts."""
    result = dict(local)
    receipts, seen = [], {}
    for index, original in enumerate(local.get("tool_outputs", [])):
        receipt = json.loads(json.dumps(original, ensure_ascii=False, default=str))
        digest = hashlib.sha256(json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest in seen:
            response = receipt["response"]
            receipt["response"] = {key: response[key] for key in ("ok", "evidence_refs", "calculation_refs") if key in response}
            receipt["response"]["data"] = {"receipt_ref": {"index": seen[digest], "sha256": digest}}
        else:
            seen[digest] = index
        if loaded_skills and receipt["tool"] == "load_skill" and receipt["response"].get("ok") and "result" in receipt["response"].get("data", {}):
            data = receipt["response"]["data"]
            loaded = data["result"]
            data["result"] = {"skill_name": loaded["skill_name"], "instructions_ref": data["source_hash"]}
        receipts.append(receipt)
    result["tool_outputs"] = receipts
    return result
