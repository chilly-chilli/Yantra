"""Single-request provider lifecycle failures; no model wording assertions."""
import asyncio
import unittest

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from yantra.backend import BackendError, RealModelBackend


class FailureProvider(BaseLlm):
    model: str = "synthetic-failure"
    behavior: str
    calls: int = 0
    closed: bool = False

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        try:
            if self.behavior == "slow":
                await asyncio.sleep(10)
            if self.behavior == "exception":
                raise ValueError("synthetic-sensitive-provider-response")
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="invalid-json")]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=5, total_token_count=15))
        finally:
            self.closed = True


class InlineFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_provider_timeout_and_cancel_close_without_retry(self):
        for behavior, code in (("invalid", "invalid_action"), ("exception", "provider"), ("slow", "timeout")):
            with self.subTest(behavior=behavior):
                backend = RealModelBackend("test", "https://unused.invalid/v1", "synthetic-only", timeout_seconds=.02)
                provider = FailureProvider(behavior=behavior)
                backend._model = provider
                with self.assertRaises(BackendError) as caught:
                    await backend.worker({"target": "finance"}, {})
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("synthetic-sensitive", str(caught.exception))
                self.assertEqual(provider.calls, 1)
                self.assertTrue(provider.closed)
                if behavior == "invalid":
                    self.assertEqual(backend.last_usage["total_tokens"], 15)
        backend = RealModelBackend("test", "https://unused.invalid/v1", "synthetic-only")
        provider = FailureProvider(behavior="slow")
        backend._model = provider
        operation = asyncio.create_task(backend.worker({"target": "finance"}, {}))
        await asyncio.sleep(.01)
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        self.assertEqual(provider.calls, 1)
        self.assertTrue(provider.closed)
