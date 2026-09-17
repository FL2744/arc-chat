import base64
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from helper import BrowserError, BrowserTimeout, Bridge, MAX_UPLOAD_BYTES, remote_path, validated_upload
from model_providers import EndpointPolicy, OpenAICompatibleProvider
from state import AppState


class RemotePathHardeningTests(unittest.TestCase):
    def test_accepts_only_relative_jupyter_paths(self):
        self.assertEqual(remote_path("results/plot.png"), "results/plot.png")
        self.assertEqual(remote_path(""), "")
        for value in ("../secret", "a/../secret", "./file", "/etc/passwd", r"C:\\secret", r"a\\b", "a//b", "a\x00b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                remote_path(value)

    def test_download_requires_nonempty_path(self):
        with self.assertRaises(ValueError):
            remote_path("", allow_empty=False)


class UploadHardeningTests(unittest.TestCase):
    def test_strict_base64_validation(self):
        payload = base64.b64encode(b"hello\x00world").decode("ascii")
        self.assertEqual(validated_upload(payload), b"hello\x00world")
        for malformed in ("%%%", "a", "a===", "YWJj\nZA==", "not base64!"):
            with self.subTest(malformed=malformed), self.assertRaisesRegex(ValueError, "valid base64"):
                validated_upload(malformed)

    def test_decoded_size_limit_is_exact(self):
        exact = base64.b64encode(b"x" * MAX_UPLOAD_BYTES).decode("ascii")
        self.assertEqual(len(validated_upload(exact)), MAX_UPLOAD_BYTES)
        over = base64.b64encode(b"x" * (MAX_UPLOAD_BYTES + 1)).decode("ascii")
        with self.assertRaisesRegex(ValueError, "larger than 20 MB"):
            validated_upload(over)


class EndpointHardeningTests(unittest.TestCase):
    def test_blocks_every_explicit_non_global_ip_class(self):
        blocked = (
            "https://0.0.0.0/v1", "https://10.0.0.1/v1", "https://100.64.0.1/v1",
            "https://127.0.0.1/v1", "https://169.254.1.1/v1", "https://192.0.2.1/v1",
            "https://224.0.0.1/v1", "https://255.255.255.255/v1", "https://[::1]/v1",
            "https://[fe80::1]/v1", "https://[fc00::1]/v1",
        )
        for endpoint in blocked:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)
        self.assertEqual(EndpointPolicy.validate("https://8.8.8.8/v1"), "https://8.8.8.8/v1")

    def test_blocks_local_names_ambiguous_numeric_hosts_and_url_suffixes(self):
        for endpoint in (
            "https://service.internal/v1", "https://foo.home.arpa/v1", "https://2130706433/v1",
            "https://0x7f000001/v1", "https://example.org/v1?next=http://127.0.0.1",
            "https://example.org/v1#fragment",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)


class LoginRecoveryHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_records_recoverable_state_and_preserves_tab(self):
        b = Bridge()
        page = SimpleNamespace(url="about:blank", goto=AsyncMock(side_effect=BrowserTimeout("timeout")))
        b.context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        await b.browser_open()
        self.assertIs(b.ood_page, page)
        self.assertEqual(b.state_machine.state, AppState.DEGRADED)

    async def test_timeout_on_login_page_records_auth_required(self):
        b = Bridge()
        page = SimpleNamespace(url="https://login.vt.edu/", goto=AsyncMock(side_effect=BrowserTimeout("timeout")))
        b.context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        await b.browser_open()
        self.assertEqual(b.state_machine.state, AppState.AUTH_REQUIRED)

    async def test_network_error_records_degraded_and_retry_reuses_same_tab(self):
        b = Bridge()
        page = SimpleNamespace(
            url="chrome-error://chromewebdata/",
            goto=AsyncMock(side_effect=[BrowserError("net::ERR_NAME_NOT_RESOLVED"), None]),
            is_closed=Mock(return_value=False),
            bring_to_front=AsyncMock(),
        )
        b.context = SimpleNamespace(new_page=AsyncMock(return_value=page))
        with self.assertRaises(RuntimeError):
            await b.browser_open()
        self.assertEqual(b.state_machine.state, AppState.DEGRADED)
        await b.browser_open()
        b.context.new_page.assert_awaited_once()
        self.assertIs(b.ood_page, page)

    async def test_existing_authenticated_arc_tab_is_not_mislabeled_as_login(self):
        b = Bridge()
        b.context = SimpleNamespace(new_page=AsyncMock())
        b.ood_page = SimpleNamespace(
            is_closed=Mock(return_value=False),
            url="https://ood.arc.vt.edu/pun/sys/dashboard",
            bring_to_front=AsyncMock(),
        )
        result = await b.browser_open()
        self.assertEqual(b.state_machine.state, AppState.ARC_READY)
        self.assertIn("Existing ARC tab", result)


class FailureInjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_retries_bounded_connection_failures_only(self):
        sleeps = []
        class HTTP:
            def post(self, *args, **kwargs):
                raise OSError("synthetic disconnect")
        provider = OpenAICompatibleProvider(HTTP(), "https://example.org/v1", "key", max_attempts=3, sleep=AsyncMock(side_effect=lambda x: sleeps.append(x)))
        with self.assertRaisesRegex(RuntimeError, "bounded retries"):
            await provider.complete({"messages": []})
        self.assertEqual(provider.sleep.await_count, 2)

    async def test_failed_model_request_does_not_duplicate_history_on_retry(self):
        b = Bridge()
        b.kernel = "kernel"
        from config import get_profile
        b.profile = get_profile("default")
        b.history = [{"role": "user", "content": "existing"}]
        class HTTP:
            def post(self, *args, **kwargs):
                raise OSError("synthetic disconnect")
        b.http = HTTP()
        before = list(b.history)
        with self.assertRaises(RuntimeError):
            await b.chat({
                "provider": "custom", "endpoint": "https://example.org/v1",
                "key": "test", "model": "mock", "advanced": True,
                "text": "do not commit me",
            })
        self.assertEqual(b.history, before)
        self.assertIsNone(b.pending)

    async def test_execution_disconnect_enters_recovery_without_replay(self):
        b = Bridge()
        b.kernel = "kernel"
        b.session = "session"
        b.notebook_path = "test.ipynb"
        b.save = AsyncMock()
        b.emit = AsyncMock()
        class Channel:
            closed = False
            async def send_json(self, request):
                self.sent = request
            async def receive(self):
                return SimpleNamespace(type=999, data="")
        b.channel = Channel()
        with self.assertRaisesRegex(RuntimeError, "connection lost"):
            await b.execute("x = 1")
        self.assertEqual(b.state_machine.state, AppState.RECOVERING)
        self.assertEqual(len(b.cells), 1)
        self.assertEqual(b.cells[0]["source"], "x = 1")
        self.assertEqual(b.save.await_count, 2)


if __name__ == "__main__":
    unittest.main()
