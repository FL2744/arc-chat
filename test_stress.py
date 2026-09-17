import base64
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from helper import Bridge, remote_path, validated_upload
from model_providers import EndpointPolicy


class SyntheticStressTests(unittest.IsolatedAsyncioTestCase):
    def test_path_and_upload_corpus(self):
        for i in range(2000):
            path = f"dataset-{i % 31}/chunk-{i:05d}.bin"
            self.assertEqual(remote_path(path), path)
        for i in range(0, 8193, 257):
            raw = os.urandom(i)
            encoded = base64.b64encode(raw).decode("ascii")
            self.assertEqual(validated_upload(encoded), raw)

    def test_endpoint_corpus_is_stable(self):
        for i in range(1, 255):
            with self.assertRaises(ValueError):
                EndpointPolicy.validate(f"https://127.0.0.{i}/v1")
            with self.assertRaises(ValueError):
                EndpointPolicy.validate(f"https://10.42.0.{i}/v1")
        for host in ("example.org", "api.openai.com", "llm-api.arc.vt.edu"):
            self.assertTrue(EndpointPolicy.validate(f"https://{host}/v1").startswith("https://"))

    async def test_reconnect_loop_never_recreates_kernel_or_history(self):
        b = Bridge()
        b.kernel = "persistent-kernel"
        b.session = "persistent-session"
        b.history = [{"role": "user", "content": "keep"}]
        b.api = AsyncMock(return_value={"execution_state": "idle"})
        b.open_channel = AsyncMock()
        for _ in range(250):
            await b.reconnect()
        self.assertEqual(b.kernel, "persistent-kernel")
        self.assertEqual(b.session, "persistent-session")
        self.assertEqual(b.history, [{"role": "user", "content": "keep"}])
        self.assertEqual(b.open_channel.await_count, 250)

    async def test_busy_reconnect_loop_never_opens_second_channel(self):
        b = Bridge()
        b.kernel = "busy-kernel"
        b.api = AsyncMock(return_value={"execution_state": "busy"})
        b.open_channel = AsyncMock()
        for _ in range(100):
            with self.assertRaises(ValueError):
                await b.reconnect()
        b.open_channel.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
