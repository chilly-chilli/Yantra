"""PaddleOCR asynchronous jobs API, normalized to original-image OCR anchors."""
from __future__ import annotations

import asyncio
import json
import math
import re
from urllib.parse import urlparse

import httpx

OPTIONS = {"useDocOrientationClassify": False, "useDocUnwarping": False, "useChartRecognition": False}
RESULT_LIMIT = 16 * 1024 * 1024


def public_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("PaddleOCR 文件／结果需要无凭据的 HTTP(S) URL")
    return value


def normalize_jsonl(text, size):
    """A call uploads one image, so multiple returned pages cannot share its anchors."""
    try:
        pages = [page for line in text.splitlines() if line.strip()
                 for page in json.loads(line)["result"]["layoutParsingResults"]]
        if len(pages) != 1:
            raise ValueError("PaddleOCR 单图任务结果页数不是 1")
        page = pages[0]
        blocks = page.get("prunedResult", {}).get("parsing_res_list")
        if blocks is None:
            markdown = page["markdown"]["text"]
            if not isinstance(markdown, str):
                raise ValueError("PaddleOCR Markdown 格式无效")
            return {"words": [{"text": markdown, "bbox": [0, 0, *size]}] if markdown.strip() else [],
                    "warnings": ["PaddleOCR 未提供版面坐标；正文仅定位至整张原图，需原图核对"]}
        words = []
        for block in blocks:
            text, box = block["block_content"], block["block_bbox"]
            if not isinstance(text, str) or len(box) != 4 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box
            ):
                raise ValueError("PaddleOCR 版面内容／坐标格式无效")
            if not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1]):
                raise ValueError("PaddleOCR 版面坐标超出原图")
            if text.strip():
                words.append({"text": text, "bbox": [math.floor(box[0]), math.floor(box[1]),
                                                        math.ceil(box[2]), math.ceil(box[3])]})
        return {"words": words, "warnings": []}
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError("PaddleOCR JSONL 结果格式无效") from None


async def recognize(source, profile, token, size, timeout, check=lambda: None):
    """Submit PNG bytes or an image URL; never forward Bearer auth to result storage."""
    async def execute():
        jobs = profile["base_url"].rstrip("/")
        headers = {"Authorization": "Bearer " + token}
        async with httpx.AsyncClient(timeout=min(15, timeout), follow_redirects=False) as client:
            async def request(method, url, **kwargs):
                check()
                response = await client.request(method, url, **kwargs)
                check()
                if response.status_code != 200:
                    raise ValueError(f"PaddleOCR API HTTP {response.status_code}")
                return response

            def data(response):
                payload = response.json()
                if payload.get("code", 0) != 0:
                    raise ValueError("PaddleOCR API 返回失败状态码")
                return payload["data"]

            if isinstance(source, str):
                response = await request("POST", jobs, headers=headers,
                    json={"fileUrl": public_url(source), "model": profile["model_id"], "optionalPayload": OPTIONS})
            else:
                response = await request("POST", jobs, headers=headers,
                    data={"model": profile["model_id"], "optionalPayload": json.dumps(OPTIONS)},
                    files={"file": ("page.png", source, "image/png")})
            job_id = data(response)["jobId"]
            if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", job_id):
                raise ValueError("PaddleOCR jobId 格式无效")
            while True:
                job = data(await request("GET", jobs + "/" + job_id, headers=headers))
                state = job["state"]
                if state == "done":
                    result_url = public_url(job["resultUrl"]["jsonUrl"])
                    check()
                    async with client.stream("GET", result_url) as result:
                        check()
                        if result.status_code != 200:
                            raise ValueError(f"PaddleOCR 结果 HTTP {result.status_code}")
                        content = bytearray()
                        async for chunk in result.aiter_bytes(chunk_size=64 * 1024):
                            check()
                            if len(content) + len(chunk) > RESULT_LIMIT:
                                raise ValueError("PaddleOCR 单图 JSONL 结果超过 16 MiB 上限")
                            content.extend(chunk)
                    return normalize_jsonl(content.decode("utf-8-sig"), size)
                if state == "failed":
                    raise ValueError("PaddleOCR 任务失败；请在供应商控制台检查任务")
                if state not in {"pending", "running"}:
                    raise ValueError("PaddleOCR 返回未知任务状态")
                await asyncio.sleep(min(5, timeout))
                check()
    try:
        return await asyncio.wait_for(execute(), timeout)
    except (httpx.TimeoutException, TimeoutError):
        raise TimeoutError("PaddleOCR 任务超过 OCR 时间上限") from None
    except httpx.HTTPError:
        raise ValueError("PaddleOCR API 网络请求失败") from None
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("PaddleOCR API 响应格式无效") from None
