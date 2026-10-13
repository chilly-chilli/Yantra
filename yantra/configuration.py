"""Project-local capability configuration, with atomic updates and no credentials."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


def empty_config():
    return {"version": 1, "models": {}, "selected": {"llm": None, "ocr": "tesseract-auto", "embedding": None},
            "skills": {}, "mcp": {}, "tools": {}}


class ConfigStore:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("YANTRA_CONFIG", ".yantra/config.json")).expanduser().resolve()

    def load(self):
        if not self.path.exists():
            return empty_config()
        value = json.loads(self.path.read_text())
        if value.get("version") != 1 or set(value) != set(empty_config()) or any(not isinstance(value[k], dict) for k in ("models", "selected", "skills", "mcp", "tools")):
            raise ValueError("配置文件格式无效或版本不支持")
        return value

    def update(self, change):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = self.load()
            change(value)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
            temporary.chmod(0o600)
            temporary.replace(self.path)
        return value


def environment_values(references):
    result = {}
    for name, reference in references.items():
        value = os.getenv(reference)
        if not value:
            raise ValueError(f"缺少环境变量 {reference}；请配置后用 --env-file 加载")
        result[name] = value
    return result


def redact(value, secrets):
    if isinstance(value, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in sorted(set(secrets), key=len, reverse=True):
            if secret:
                value = value.replace(secret, "[REDACTED]")
    return value


def ask(label, default=None):
    from .terminal_status import report
    report("blocked", label, kind="question")
    value = input(label + (f" [{default}]" if default is not None else "") + "：").strip()
    report("working", "正在配置")
    return value or (default if default is not None else "")
