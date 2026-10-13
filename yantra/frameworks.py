"""Immutable framework snapshots; only independently checked drafts are selectable."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .schemas import Framework

BUILTINS = {
    "value": {"name": "价值与安全边际", "scope": "正且稳定的可追溯现金流；核对价格、股本及币种", "questions": ["现金流是否可持续？", "价格与价值差距来自什么？"]},
    "quality": {"name": "经营质量与现金流", "scope": "A股、港股及尚无法可靠估值的企业", "questions": ["盈利质量与现金转化是否一致？", "竞争优势是否能持续？"]},
    "change": {"name": "经营改善与预期差", "scope": "改善或周期变化中的企业，缺共识时保留缺口", "questions": ["改善有何证据？", "当前价格或预期是否已反映改善？"]},
}


class FrameworkStore:
    def __init__(self, directory):
        self.directory = Path(directory)

    def builtin(self, identifier):
        if identifier.endswith(":v1") and identifier[:-3] in BUILTINS:
            identifier = identifier[:-3]
        if identifier not in BUILTINS:
            return self.load(identifier)
        item = BUILTINS[identifier]
        rule = Framework(id=identifier, **item, evidence_needs=["原始财务披露", "经营及竞争证据", "独立反证", "时点内市场比较基准"],
                         risks=["现金流口径错误", "周期或竞争变化", "假设缺乏依据"],
                         disconfirming=["新披露与论点矛盾", "现金转化持续恶化"], tracking=["收入、利润及现金流", "竞争变化与新披露"])
        return {"version": f"{identifier}:v1", "rules": rule.model_dump(), "verified": True,
                "original": None, "parent": None, "reason": "内置基线", "source_run": None}

    def draft(self, rule, original, parent, reason, source_run):
        rule = Framework.model_validate(rule).model_dump()
        payload = {"rules": rule, "original": original, "parent": parent, "reason": reason, "source_run": source_run}
        version = rule["id"] + ":" + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (version.replace(":", "-") + ".json")
        if not path.exists():
            path.write_text(json.dumps({**payload, "version": version, "verified": False}, ensure_ascii=False, indent=2))
        return self.load(version, allow_draft=True)

    def verify(self, version, verification):
        data = self.load(version, allow_draft=True)
        if data["verified"]:
            return data
        if not verification.get("passed") or not verification.get("review_task_id"):
            raise ValueError("framework requires a passed independent verification task")
        data.update(verified=True, verification=verification)
        self._path(version).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    def _path(self, version):
        if not version or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_:" for c in version):
            raise ValueError("invalid framework version")
        return self.directory / (version.replace(":", "-") + ".json")

    def load(self, version, allow_draft=False):
        data = json.loads(self._path(version).read_text())
        if not allow_draft and not data["verified"]:
            raise ValueError("draft framework cannot be selected")
        return data
