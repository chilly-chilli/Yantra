"""LLM/OCR profiles, selection and executable OCR adapters."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import Field, model_validator

from .configuration import ask, environment_values, redact
from .schemas import Contract, Pricing


class ModelProfile(Contract):
    kind: Literal["llm", "ocr"]
    provider: Literal["openai-compatible", "tesseract", "vision", "command"]
    model_id: str = ""
    base_url: str = ""
    api_key_env: str = "MODEL_API_KEY"
    pricing: Pricing | None = None
    language: str = "auto"
    data_dir: str | None = None
    command: list[str] = Field(default_factory=list)
    env_refs: dict[str, str] = Field(default_factory=dict)
    max_image_tokens: int | None = Field(default=None, gt=0)
    call_cost_cny: float = Field(default=0, ge=0)
    cost_source: str | None = None
    cost_as_of: str | None = None

    @model_validator(mode="after")
    def adapter(self):
        if self.kind == "llm" and self.provider != "openai-compatible":
            raise ValueError("LLM 使用 openai-compatible 适配器")
        if self.kind == "ocr" and self.provider == "openai-compatible":
            raise ValueError("OCR 请选择 tesseract、vision 或 command")
        if self.provider in {"openai-compatible", "vision"}:
            url = urlparse(self.base_url)
            if not self.model_id or url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query:
                raise ValueError("模型需要 model_id 及不含凭据的 HTTP(S) base_url")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
                raise ValueError("api_key_env 必须是环境变量名")
        if self.provider == "vision" and (self.pricing is None or self.max_image_tokens is None):
            raise ValueError("视觉 OCR 需配置单价与供应商声明的单图 token 上界")
        if self.provider == "command" and not self.command:
            raise ValueError("command OCR 需提供命令参数数组")
        if self.call_cost_cny and not (self.cost_source and self.cost_as_of):
            raise ValueError("收费 OCR 需声明费用上界的来源和日期")
        return self


BUILTIN_MODELS = {
    "tesseract-auto": ModelProfile(kind="ocr", provider="tesseract", model_id="tesseract", language="auto").model_dump(mode="json"),
    "tesseract-eng": ModelProfile(kind="ocr", provider="tesseract", model_id="tesseract", language="eng").model_dump(mode="json"),
}


class ModelCatalog:
    def __init__(self, store):
        self.store = store

    def entries(self):
        return BUILTIN_MODELS | self.store.load()["models"]

    def add(self, name, data):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name) or name in BUILTIN_MODELS or name == "env":
            raise ValueError("名称需为小写字母、数字、下划线或连字符，且不能覆盖内置项")
        profile = ModelProfile.model_validate(data).model_dump(mode="json")
        if profile["data_dir"]:profile["data_dir"]=str(Path(profile["data_dir"]).expanduser().resolve())
        self.store.update(lambda c: c["models"].__setitem__(name, profile))
        return profile

    def resolve(self, kind, name=None):
        name = name or self.store.load()["selected"].get(kind)
        if not name or (kind == "llm" and name == "env"):
            return None
        profile = self.entries().get(name)
        if profile is None or profile["kind"] != kind:
            raise ValueError(f"未注册 {kind} 模型配置：{name}")
        return {"name": name, **ModelProfile.model_validate(profile).model_dump(mode="json")}

    def select(self, kind, name):
        if kind not in {"llm", "ocr"}:
            raise ValueError("模型类型只能是 llm/ocr")
        self.resolve(kind, name)
        self.store.update(lambda c: c["selected"].__setitem__(kind, name))


def create_backend(profile, backend_class):
    values = environment_values({"api_key": profile["api_key_env"]})
    backend = backend_class(profile["model_id"], profile["base_url"], values["api_key"], timeout_seconds=90)
    backend.profile_name = profile["name"]
    return backend


class OCRLine(Contract):
    text: str
    bbox: list[int] = Field(min_length=4, max_length=4)


class OCROutput(Contract):
    words: list[OCRLine]
    warnings: list[str] = Field(default_factory=list)


def tesseract_config(profile):
    directory = profile.get("data_dir") or os.getenv("TESSDATA_PREFIX")
    bundled = Path(sys.prefix) / "share/tessdata"
    if not directory and (bundled / "eng.traineddata").exists():
        directory = str(bundled)
    return "--tessdata-dir " + shlex.quote(directory) if directory else ""


def tesseract_recognize(image, profile, timeout):
    import pytesseract
    config=tesseract_config(profile)
    languages = set(pytesseract.get_languages(config=config))
    requested = profile["language"]
    warnings = []
    if requested == "auto":
        requested = "chi_sim+eng" if "chi_sim" in languages else "eng"
        if "chi_sim" not in languages:
            warnings.append("未安装 chi_sim，中文 OCR 能力未满足")
    elif set(requested.split("+")) - languages:
        raise ValueError("所选 Tesseract 语言包未安装")
    data = pytesseract.image_to_data(image, lang=requested, config=config, timeout=timeout, output_type=pytesseract.Output.DICT)
    groups = {}
    for i, word in enumerate(data["text"]):
        if word.strip():
            groups.setdefault((data["block_num"][i], data["par_num"][i], data["line_num"][i]), []).append(i)
    words = []
    for indices in groups.values():
        words.append({"text": " ".join(data["text"][i] for i in indices), "bbox": [
            min(data["left"][i] for i in indices), min(data["top"][i] for i in indices),
            max(data["left"][i]+data["width"][i] for i in indices), max(data["top"][i]+data["height"][i] for i in indices)]})
    return {"words": words, "warnings": warnings}


class OCRRecognizer:
    def __init__(self, profile, engine):
        self.profile, self.engine = profile, engine

    def __call__(self, image, timeout):
        self.engine.run_budget.check()
        # A dedicated loop keeps parsing usable both at synchronous creation and in a worker thread.
        if self.profile["provider"] == "tesseract":
            result = tesseract_recognize(image, self.profile, timeout)
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(lambda: asyncio.run(self._remote(image, timeout))).result()
        output = OCROutput.model_validate(result)
        width, height = image.size
        if any(not (0 <= w.bbox[0] < w.bbox[2] <= width and 0 <= w.bbox[1] < w.bbox[3] <= height) for w in output.words):
            raise ValueError("OCR 图像区域坐标超出原图")
        self.engine.state["budget"]["ocr_calls"] = self.engine.state["budget"].get("ocr_calls", 0)+1
        self.engine._log("ocr_result", {"profile": self.profile["name"], "provider": self.profile["provider"], "lines": len(output.words)})
        self.engine._save()
        return output.model_dump()

    async def _remote(self, image, timeout):
        from .backend import RealModelBackend
        from .extensions import run_command
        engine, profile = self.engine, self.profile
        if engine.state["budget"]["model_calls"] >= engine.state["limits"]["max_models"]:
            raise ValueError("模型调用预算耗尽")
        if profile["provider"] == "command":
            stream = io.BytesIO(); image.save(stream, format="PNG")
            import base64
            payload = {"image_base64": base64.b64encode(stream.getvalue()).decode(), "mime_type": "image/png", "width": image.width, "height": image.height, "language": profile["language"]}
            receipt = engine.run_budget.reserve_fixed(profile["call_cost_cny"], "ocr_command", {"source":profile["cost_source"],"as_of":profile["cost_as_of"]})
            engine.state["budget"]["model_calls"] += 1
            try:
                return await run_command(profile["command"], payload, timeout, profile["env_refs"])
            finally:
                engine.run_budget.settle(receipt, None)
        backend = create_backend(profile, RealModelBackend)
        stream = io.BytesIO(); image.save(stream, format="PNG")
        payload = {"width": image.width, "height": image.height, "language": profile["language"]}
        instruction = "忠实转写图像中的每行文字，返回 words（每项 text、bbox=[左,上,右,下]原图像素坐标）及 warnings。不能推断缺失数字或分析财务。无法识别则留空并说明。"
        receipt = engine.run_budget.reserve(json.dumps(payload), instruction+json.dumps(OCROutput.model_json_schema()), backend.max_output_tokens,
                                           pricing=profile["pricing"], extra_input_tokens=profile["max_image_tokens"])
        receipt.update(method="ocr_vision", profile=profile["name"])
        engine.state["budget"]["model_calls"] += 1
        engine._save()
        try:
            from google.genai import types
            return await asyncio.wait_for(backend._decision("ocr_transcriber", instruction, OCROutput, payload,
                image_parts=[types.Part.from_bytes(data=stream.getvalue(), mime_type="image/png")]), timeout)
        finally:
            engine.run_budget.settle(receipt, backend.last_usage)


async def check_model(profile):
    if profile["provider"] == "tesseract":
        import pytesseract
        languages=pytesseract.get_languages(config=tesseract_config(profile))
        return {"ready":profile["language"]=="auto" or set(profile["language"].split("+"))<=set(languages),
                "version": str(pytesseract.get_tesseract_version()), "languages":languages}
    if profile["provider"] == "command":
        import shutil
        return {"ready": bool(shutil.which(profile["command"][0])), "provider": "command"}
    values = environment_values({"api_key": profile["api_key_env"]})
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(profile["base_url"].rstrip("/")+"/models", headers={"Authorization":"Bearer "+values["api_key"]})
            if response.status_code != 200:
                return {"ready": False, "http_status": response.status_code}
            identifiers = [m["id"] for m in response.json().get("data", [])]
            return redact({"ready": profile["model_id"].removeprefix("openai/") in identifiers, "available_models": identifiers},values.values())
    except (httpx.HTTPError, ValueError, KeyError):
        return {"ready": False, "message": "无法读取模型列表，请检查地址和凭据"}


def interactive_models(catalog):
    while True:
        action = ask("模型模块：list 列表 / add 添加 / select 选择 / back 返回", "list")
        if action == "back":
            return
        if action == "list":
            print("LLM env：沿用 MODEL_* 环境配置")
            for name, profile in catalog.entries().items():
                print(f"{name}: {profile['kind']} / {profile['provider']} / {profile['model_id']}")
            print("当前选择："+json.dumps(catalog.store.load()["selected"],ensure_ascii=False))
        elif action == "select":
            kind=ask("类型 llm/ocr", "llm"); name=ask("配置名称（LLM 可选 env）")
            catalog.select(kind,name); print("已保存选择。")
        elif action == "add":
            name=ask("配置名称"); kind=ask("类型 llm/ocr", "llm")
            provider="openai-compatible" if kind=="llm" else ask("OCR 适配器 tesseract/vision/command", "tesseract")
            data={"kind":kind,"provider":provider}
            if provider in {"openai-compatible","vision"}:
                data.update(model_id=ask("模型 ID"),base_url=ask("API base URL"),api_key_env=ask("密钥环境变量名", "MODEL_API_KEY"))
                data["pricing"]={"input_per_million":float(ask("每百万输入 tokens 人民币单价")),"output_per_million":float(ask("每百万输出 tokens 人民币单价")),"source":ask("单价来源"),"as_of":ask("单价日期 YYYY-MM-DD")}
                if provider=="vision":data["max_image_tokens"]=int(ask("供应商声明的单图输入 token 上界"))
                print("凭据通过对应环境变量或 --env-file 提供。")
            if provider in {"tesseract","command"}:
                data["language"]=ask("语言", "auto")
                if provider=="tesseract":data["data_dir"]=ask("语言模型目录（空值用已安装目录）")
                else:data["command"]=json.loads(ask("命令参数 JSON 数组"))
            catalog.add(name,data); print("已注册，可用 select 选择。")
        else:
            print("请输入 list、add、select 或 back。")
