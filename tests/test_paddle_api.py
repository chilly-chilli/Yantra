"""Controlled offline jobs responses; these tests never contact PaddleOCR."""
from research_fixtures import create_test_raw
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from pydantic import ValidationError

from yantra.configuration import ConfigStore
from yantra.models import ModelCatalog, ModelProfile, check_model
from yantra.paddle_api import OPTIONS, normalize_jsonl, recognize
from test_v2 import V2TestBackend

PROFILE = {"kind": "ocr", "provider": "paddleocr-api", "model_id": "PaddleOCR-VL-1.6",
           "base_url": "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs",
           "api_key_env": "TEST_PADDLE_TOKEN", "call_cost_cny": .1,
           "cost_source": "controlled synthetic per-image fee", "cost_as_of": "2026-10-13"}
PRICE = {"input_per_million": 1, "output_per_million": 1, "source": "test fixture", "as_of": "2026-10-13"}
TOKEN = "private-synthetic-token"


def jsonl(page):
    return json.dumps({"result": {"layoutParsingResults": [page]}})


def blocks_page():
    return {"prunedResult": {"parsing_res_list": [
        {"block_content": "营业收入 1,234.50", "block_bbox": [1.2, 2.3, 198.1, 39.1]}]}}


class PaddleAPITests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self, handler, source=b"synthetic-image", timeout=1, check=lambda: None):
        factory = httpx.AsyncClient
        with patch("yantra.paddle_api.httpx.AsyncClient", side_effect=lambda **kw:
                   factory(transport=httpx.MockTransport(handler), **kw)):
            return await recognize(source, PROFILE, TOKEN, (500, 150), timeout, check)

    async def test_local_upload_polling_and_result_auth_isolation(self):
        requests = []
        states = iter(["pending", "running", "done"])
        def handler(request):
            requests.append(request)
            if request.method == "POST":
                self.assertIn(b'filename="page.png"', request.content)
                self.assertIn(b"synthetic-image", request.content)
                self.assertIn(b"PaddleOCR-VL-1.6", request.content)
                self.assertIn(b'"useChartRecognition": false', request.content)
                return httpx.Response(200, json={"code": 0, "data": {"jobId": "job-1"}})
            if request.url.host == "result.invalid":
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(200, text=jsonl(blocks_page()))
            state = next(states)
            return httpx.Response(200, json={"code": 0, "data": {"state": state,
                "resultUrl": {"jsonUrl": "https://result.invalid/data?signature=synthetic"}}})
        checks = []
        async def no_sleep(seconds):
            return None
        with patch("yantra.paddle_api.asyncio.sleep", no_sleep):
            output = await self.run_job(handler, check=lambda: checks.append(True))
        self.assertEqual(output["words"][0]["bbox"], [1, 2, 199, 40])
        self.assertEqual(len(requests), 5)
        self.assertGreaterEqual(len(checks), 10)
        self.assertTrue(all(r.headers["authorization"] == "Bearer " + TOKEN for r in requests[:-1]))

    async def test_url_submission_and_markdown_full_image_warning(self):
        def handler(request):
            if request.method == "POST":
                payload = json.loads(request.content)
                self.assertEqual(payload, {"fileUrl": "https://source.invalid/image.png",
                    "model": PROFILE["model_id"], "optionalPayload": OPTIONS})
                return httpx.Response(200, json={"data": {"jobId": "1"}})
            if request.url.host == "result.invalid":
                return httpx.Response(200, text=jsonl({"markdown": {"text": "现金流 123"}}))
            return httpx.Response(200, json={"data": {"state": "done",
                "resultUrl": {"jsonUrl": "https://result.invalid/data"}}})
        output = await self.run_job(handler, source="https://source.invalid/image.png")
        self.assertEqual(output["words"], [{"text": "现金流 123", "bbox": [0, 0, 500, 150]}])
        self.assertIn("整张原图", output["warnings"][0])

    async def test_http_error_failed_job_bad_code_and_bad_state_are_redacted(self):
        for status, payload in [(401, {"msg": TOKEN}), (200, {"code": 10007, "msg": TOKEN}),
                                (200, {"data": {"state": "failed", "errorMsg": TOKEN}}),
                                (200, {"data": {"state": "unknown-" + TOKEN}})]:
            def handler(request):
                if request.method == "POST" and "data" in payload:
                    return httpx.Response(200, json={"data": {"jobId": "1"}})
                return httpx.Response(status, json=payload)
            with self.subTest(status=status, payload=payload), self.assertRaises(ValueError) as caught:
                await self.run_job(handler)
            self.assertNotIn(TOKEN, str(caught.exception))

    async def test_pending_and_transport_timeouts_are_bounded(self):
        def pending(request):
            return httpx.Response(200, json={"data": {"jobId": "1"} if request.method == "POST" else {"state": "pending"}})
        with self.assertRaises(TimeoutError):
            await self.run_job(pending, timeout=.01)
        def timeout(request):
            raise httpx.ReadTimeout("request with " + TOKEN, request=request)
        with self.assertRaises(TimeoutError) as caught:
            await self.run_job(timeout)
        self.assertNotIn(TOKEN, str(caught.exception))

    async def test_cancellation_stops_polling(self):
        from yantra.budget import BudgetExceeded
        count = 0
        def check():
            nonlocal count
            count += 1
            if count == 3:
                raise BudgetExceeded("controlled cancellation")
        with self.assertRaises(BudgetExceeded):
            await self.run_job(lambda r: httpx.Response(200, json={"data": {"jobId": "1"}}), check=check)

    async def test_redirect_is_not_followed_with_credentials(self):
        calls = []
        def redirect(request):
            calls.append(request)
            return httpx.Response(307, headers={"Location": "https://other.invalid/jobs"})
        with self.assertRaisesRegex(ValueError, "HTTP 307"):
            await self.run_job(redirect)
        self.assertEqual(len(calls), 1)

    async def test_result_download_uses_the_same_deadline(self):
        async def slow_result(request):
            if request.method == "POST":
                return httpx.Response(200, json={"data": {"jobId": "1"}})
            if request.url.host == "result.invalid":
                await asyncio.sleep(.1)
                return httpx.Response(200, text=jsonl(blocks_page()))
            return httpx.Response(200, json={"data": {"state": "done",
                "resultUrl": {"jsonUrl": "https://result.invalid/data"}}})
        with self.assertRaises(TimeoutError):
            await self.run_job(slow_result, timeout=.01)

    async def test_malformed_response_and_unsafe_url_fail_cleanly(self):
        for content in ('not JSON', '{}', '[]', '{"data":{"jobId":"../other"}}'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                await self.run_job(lambda r: httpx.Response(200, text=content))
        with self.assertRaises(ValueError):
            await self.run_job(lambda r: self.fail("unsafe URL must not be submitted"),
                               source="https://token@source.invalid/file.png")

    async def test_profile_validation_and_check_never_submit_job(self):
        self.assertEqual(ModelProfile.model_validate(PROFILE).provider, "paddleocr-api")
        for update in ({"api_key": TOKEN}, {"api_key_env": TOKEN}, {"cost_source": None},
                       {"base_url": "https://token@api.invalid/jobs"}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                ModelProfile.model_validate(PROFILE | update)
        with patch.dict(os.environ, {"TEST_PADDLE_TOKEN": TOKEN}), patch("httpx.AsyncClient") as factory:
            result = await check_model(PROFILE)
            self.assertTrue(result["ready"])
            self.assertEqual(result["check"], "credential_presence_only")
            self.assertFalse(result["live_api_verified"])
            self.assertNotIn(TOKEN, json.dumps(result))
            factory.assert_not_called()
        with patch.dict(os.environ, {"TEST_PADDLE_TOKEN": "YOUR_PADDLEOCR_API_TOKEN"}):
            self.assertFalse((await check_model(PROFILE))["ready"])

    async def test_result_download_size_limit_and_encoding(self):
        for content, limit in [(b"x" * 101, 100), (b"\xff", 100)]:
            def handler(request):
                if request.method == "POST":
                    return httpx.Response(200, json={"data": {"jobId": "1"}})
                if request.url.host == "result.invalid":
                    return httpx.Response(200, content=content)
                return httpx.Response(200, json={"data": {"state": "done",
                    "resultUrl": {"jsonUrl": "https://result.invalid/data"}}})
            with self.subTest(content=content), patch("yantra.paddle_api.RESULT_LIMIT", limit), self.assertRaises(ValueError):
                await self.run_job(handler)

    async def test_actual_material_pipeline_budget_and_snapshot(self):
        async def mock_recognize(source, profile, token, size, timeout, check):
            self.assertTrue(source.startswith(b"\x89PNG"))
            self.assertEqual(token, TOKEN)
            check()
            return normalize_jsonl(jsonl(blocks_page()), size)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TEST_PADDLE_TOKEN": TOKEN}):
            root = Path(directory)
            image = root / "scan.png"
            Image.new("RGB", (500, 150), "white").save(image)
            store = ConfigStore(root / "config.json")
            catalog = ModelCatalog(store)
            catalog.add("paddle", PROFILE)
            catalog.select("ocr", "paddle")
            with patch("yantra.paddle_api.recognize", mock_recognize):
                engine = create_test_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13"},
                    [image], backend=V2TestBackend(), pricing=PRICE, settings=store)
            self.assertEqual(engine.state["budget"]["ocr_calls"], 1)
            self.assertEqual(engine.state["budget"]["model_calls"], 1)
            self.assertAlmostEqual(engine.state["budget"]["model_cost_cny"], .1)
            self.assertEqual(engine.state["model_ledger"][0]["method"], "ocr_paddle_api")
            doc = next(iter(engine.state["input_snapshot"]["materials"].values()))
            self.assertIn("1,234.50", doc["text"])
            self.assertEqual(doc["chunks"][0]["location"]["region_pixels"], [1, 2, 199, 40])
            self.assertNotIn(TOKEN, (engine.directory / "state.json").read_text())

    async def test_failed_job_retains_budget_reservation(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TEST_PADDLE_TOKEN": TOKEN}):
            root = Path(directory)
            image = root / "scan.png"
            Image.new("RGB", (500, 150), "white").save(image)
            store = ConfigStore(root / "config.json")
            catalog = ModelCatalog(store)
            catalog.add("paddle", PROFILE)
            catalog.select("ocr", "paddle")
            with patch("yantra.paddle_api.recognize", side_effect=TimeoutError("controlled timeout")):
                engine = create_test_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13"},
                    [image], backend=V2TestBackend(), pricing=PRICE, settings=store)
            self.assertEqual(engine.state["budget"].get("ocr_calls", 0), 0)
            self.assertEqual(engine.state["budget"]["model_calls"], 1)
            self.assertAlmostEqual(engine.state["budget"]["model_cost_cny"], .1)
            self.assertEqual(engine.state["model_ledger"][0]["status"], "usage_missing_reservation_retained")

    async def test_placeholder_token_cannot_submit_or_reserve_job(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TEST_PADDLE_TOKEN": "YOUR_PADDLEOCR_API_TOKEN"}):
            root = Path(directory)
            image = root / "scan.png"
            Image.new("RGB", (500, 150), "white").save(image)
            store = ConfigStore(root / "config.json")
            catalog = ModelCatalog(store)
            catalog.add("paddle", PROFILE)
            catalog.select("ocr", "paddle")
            with patch("yantra.paddle_api.recognize") as request:
                engine = create_test_raw(root / "runs", {"company": "测试公司", "horizon": "12个月", "cutoff": "2026-10-13"},
                    [image], backend=V2TestBackend(), pricing=PRICE, settings=store)
                request.assert_not_called()
            self.assertEqual(engine.state["budget"]["model_calls"], 0)
            self.assertEqual(engine.state["model_ledger"], [])


class PaddleResultTests(unittest.TestCase):
    def test_invalid_result_cannot_invent_anchors(self):
        for page in [blocks_page() | {"prunedResult": {"parsing_res_list": [
                        {"block_content": "value", "block_bbox": box}]}}
                     for box in ([-1, 0, 10, 10], [0, 0, 501, 20], [10, 2, 1, 5], [1, 2, 3], [1, 2, float("nan"), 5])]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                normalize_jsonl(jsonl(page), (500, 150))
        for text in ('invalid', '{}', json.dumps({"result": {"layoutParsingResults": [blocks_page(), blocks_page()]}})):
            with self.subTest(text=text), self.assertRaises(ValueError):
                normalize_jsonl(text, (500, 150))
