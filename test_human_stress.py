"""Human-behavior stress cases for classroom ARC Chat usage.

These tests intentionally model individual user mistakes/recovery sequences rather
than only exercising isolated helpers.  A failure name should describe the
classroom story that broke.
"""

import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientSession, WSMsgType, web
from aiohttp import ServerDisconnectedError
from aiohttp.test_utils import TestServer

import helper
from helper import BrowserTimeout, Bridge, message
from model_providers import EndpointPolicy, OpenAICompatibleProvider
from state import AppState


class HumanLoginStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_then_login_then_arc_dashboard_reuses_one_visible_tab(self):
        b = Bridge()
        page = SimpleNamespace(
            url="about:blank",
            goto=AsyncMock(side_effect=BrowserTimeout("synthetic slow VPN")),
            is_closed=Mock(return_value=False),
            bring_to_front=AsyncMock(),
        )
        b.context = SimpleNamespace(new_page=AsyncMock(return_value=page))

        first = await b.browser_open()
        self.assertIn("VPN", first)
        self.assertEqual(b.state_machine.state, AppState.DEGRADED)

        page.url = "https://login.vt.edu/profile/SAML2/Redirect/SSO"
        second = await b.browser_open()
        self.assertIn("login/MFA", second)
        self.assertEqual(b.state_machine.state, AppState.AUTH_REQUIRED)

        page.url = "https://ood.arc.vt.edu/pun/sys/dashboard"
        third = await b.browser_open()
        self.assertIn("Existing ARC tab", third)
        self.assertEqual(b.state_machine.state, AppState.ARC_READY)
        b.context.new_page.assert_awaited_once()


class HumanSocketStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_click_while_action_is_running_does_not_poison_app_state(self):
        original = helper.bridge
        b = helper.bridge = Bridge()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_dispatch(action, data):
            started.set()
            await release.wait()
            return "first action completed"

        b.dispatch = slow_dispatch
        app = web.Application()
        app.router.add_get("/ws", helper.socket)
        try:
            async with TestServer(app) as server, ClientSession() as client:
                ws = await client.ws_connect(server.make_url("/ws"))
                # status, busy, state snapshot
                for expected in ("status", "busy", "state"):
                    self.assertEqual((await ws.receive_json())["type"], expected)

                await ws.send_json({"action": "doctor"})
                await asyncio.wait_for(started.wait(), timeout=1)
                # Consume the first action's busy=true broadcast.
                self.assertEqual((await ws.receive_json())["type"], "busy")

                # A real user can double-click before the first operation finishes.
                await ws.send_json({"action": "doctor"})
                event = await ws.receive_json()
                self.assertEqual(event["type"], "error")
                self.assertIn("Wait for the current action", event["text"])
                self.assertEqual(b.state_machine.state, AppState.READY_LOCAL)

                release.set()
                # First action still completes normally after the rejected duplicate.
                kinds = {(await ws.receive_json())["type"], (await ws.receive_json())["type"]}
                self.assertEqual(kinds, {"status", "busy"})
                await ws.close()
        finally:
            release.set()
            helper.bridge = original

    async def test_malformed_client_packet_reports_error_without_destroying_recoverable_state(self):
        original = helper.bridge
        b = helper.bridge = Bridge()
        await b.set_state(AppState.DEGRADED, "synthetic recoverable network issue", force=True)
        app = web.Application()
        app.router.add_get("/ws", helper.socket)
        try:
            async with TestServer(app) as server, ClientSession() as client:
                ws = await client.ws_connect(server.make_url("/ws"))
                for _ in range(3):
                    await ws.receive_json()
                await ws.send_str("this is not json")
                event = await ws.receive_json()
                self.assertEqual(event["type"], "error")
                self.assertEqual(b.state_machine.state, AppState.DEGRADED)
                await ws.close()
        finally:
            helper.bridge = original

    async def test_one_stale_browser_tab_cannot_break_updates_to_live_tabs(self):
        b = Bridge()

        class StaleClient:
            closed = False

            async def send_json(self, data):
                raise ConnectionResetError("synthetic stale tab")

        class LiveClient:
            closed = False

            def __init__(self):
                self.messages = []

            async def send_json(self, data):
                self.messages.append(data)

        stale = StaleClient()
        live = LiveClient()
        b.clients = {stale, live}
        await b.emit("status", text="still alive")
        self.assertEqual(live.messages, [{"type": "status", "text": "still alive"}])
        self.assertNotIn(stale, b.clients)
        self.assertIn(live, b.clients)

    async def test_two_tabs_submitting_same_input_prompt_send_only_one_kernel_reply(self):
        original = helper.bridge
        b = helper.bridge = Bridge()
        b.input_header = {"msg_id": "prompt-1"}
        b.input_content = {"prompt": "Name?", "password": False}

        class SlowChannel:
            def __init__(self):
                self.calls = 0
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def send_json(self, data):
                self.calls += 1
                self.started.set()
                await self.release.wait()

        channel = SlowChannel()
        b.channel = channel
        app = web.Application()
        app.router.add_get("/ws", helper.socket)
        try:
            async with TestServer(app) as server, ClientSession() as client:
                first = await client.ws_connect(server.make_url("/ws"))
                second = await client.ws_connect(server.make_url("/ws"))
                for ws in (first, second):
                    for expected in ("status", "busy", "input"):
                        self.assertEqual((await ws.receive_json())["type"], expected)

                await first.send_json({"action": "input", "value": "Ada"})
                await asyncio.wait_for(channel.started.wait(), timeout=1)
                await second.send_json({"action": "input", "value": "Ada"})
                await asyncio.sleep(0.05)
                self.assertEqual(channel.calls, 1)
                channel.release.set()
                await first.close()
                await second.close()
        finally:
            channel.release.set()
            helper.bridge = original

    async def test_failed_input_send_restores_prompt_for_safe_manual_retry(self):
        original = helper.bridge
        b = helper.bridge = Bridge()
        b.input_header = {"msg_id": "prompt-restore"}
        b.input_content = {"prompt": "Password?", "password": True}

        class BrokenChannel:
            async def send_json(self, data):
                raise ConnectionResetError("synthetic kernel disconnect")

        b.channel = BrokenChannel()
        app = web.Application()
        app.router.add_get("/ws", helper.socket)
        try:
            async with TestServer(app) as server, ClientSession() as client:
                ws = await client.ws_connect(server.make_url("/ws"))
                for expected in ("status", "busy", "input"):
                    self.assertEqual((await ws.receive_json())["type"], expected)
                await ws.send_json({"action": "input", "value": "secret"})
                event = await ws.receive_json()
                self.assertEqual(event["type"], "error")
                self.assertEqual(b.input_header, {"msg_id": "prompt-restore"})
                self.assertEqual(b.input_content, {"prompt": "Password?", "password": True})
                await ws.close()
        finally:
            helper.bridge = original

    async def test_repeated_open_close_tab_churn_does_not_leak_clients_or_kernel(self):
        original = helper.bridge
        b = helper.bridge = Bridge()
        b.kernel = "persistent-kernel"
        b.session = "persistent-session"
        app = web.Application()
        app.router.add_get("/ws", helper.socket)
        try:
            async with TestServer(app) as server, ClientSession() as client:
                for _ in range(25):
                    ws = await client.ws_connect(server.make_url("/ws"))
                    for expected in ("status", "busy", "state"):
                        self.assertEqual((await ws.receive_json())["type"], expected)
                    await ws.close()
                    await asyncio.sleep(0)
                self.assertEqual(b.clients, set())
                self.assertEqual(b.kernel, "persistent-kernel")
                self.assertEqual(b.session, "persistent-session")
        finally:
            helper.bridge = original


class HumanFileStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_rejects_empty_control_and_pathlike_filenames_before_remote_io(self):
        b = Bridge()
        b.api = AsyncMock()
        payload = base64.b64encode(b"safe").decode("ascii")
        bad_names = ("", ".", "..", "../report.csv", r"folder\report.csv", "bad\x00name.csv", "bad\nname.csv")
        for name in bad_names:
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                await b.dispatch("upload", {"name": name, "content": payload})
        b.api.assert_not_awaited()

    async def test_unicode_and_space_filename_survives_upload_round_trip_request(self):
        b = Bridge()
        b.api = AsyncMock(return_value={})
        payload = base64.b64encode("café".encode()).decode("ascii")
        result = await b.dispatch("upload", {"name": "résumé data 01.csv", "content": payload})
        self.assertIn("résumé data 01.csv", result)
        args = b.api.await_args.args
        self.assertEqual(args[0], "PUT")
        self.assertNotIn(" ", args[1])


class HumanEndpointStressTests(unittest.TestCase):
    def test_typo_ports_fail_during_configuration_not_during_model_use(self):
        for endpoint in ("https://example.org:0/v1", "https://example.org:99999/v1", "https://example.org:notaport/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)

    def test_ipv4_mapped_and_ipv6_local_targets_are_blocked(self):
        for endpoint in (
            "https://[::ffff:127.0.0.1]/v1",
            "https://[::ffff:10.0.0.1]/v1",
            "https://[ff02::1]/v1",
            "https://[2001:db8::1]/v1",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)


class HumanProviderStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_503_then_429_then_success_is_bounded_and_returns_once(self):
        class Response:
            def __init__(self, status, *, headers=None, body=None):
                self.status = status
                self.headers = headers or {}
                self.body = body or {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return self.body

            async def text(self):
                return "synthetic transient"

        class HTTP:
            def __init__(self):
                self.responses = [Response(503), Response(429, headers={"Retry-After": "0"}), Response(200)]
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                return self.responses.pop(0)

        http = HTTP()
        sleep = AsyncMock()
        provider = OpenAICompatibleProvider(http, "https://example.org/v1", "key", max_attempts=3, sleep=sleep)
        result = await provider.complete({"messages": []})
        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(http.calls, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_server_disconnect_before_response_is_retried(self):
        class Response:
            status = 200
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def json(self):
                return {"choices": [{"message": {"role": "assistant", "content": "recovered"}}]}

            async def text(self):
                return ""

        class HTTP:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ServerDisconnectedError("synthetic disconnect")
                return Response()

        http = HTTP()
        sleep = AsyncMock()
        provider = OpenAICompatibleProvider(http, "https://example.org/v1", "key", max_attempts=2, sleep=sleep)
        result = await provider.complete({"messages": []})
        self.assertEqual(result["choices"][0]["message"]["content"], "recovered")
        self.assertEqual(http.calls, 2)
        sleep.assert_awaited_once()


class HumanExecutionLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_serial_cells_keep_unique_notebook_cells_and_ready_state(self):
        b = Bridge()
        b.kernel = "kernel"
        b.session = "session"
        b.notebook_path = "stress.ipynb"
        b.save = AsyncMock()
        b.emit = AsyncMock()

        class Channel:
            closed = False

            def __init__(self):
                self.events = []
                self.execution_count = 0

            async def send_json(self, request):
                self.execution_count += 1
                parent = request["header"]["msg_id"]
                self.events = [
                    SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(message(
                        "execute_reply", {"status": "ok", "execution_count": self.execution_count}, parent={"msg_id": parent}
                    ))),
                    SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(message(
                        "status", {"execution_state": "idle"}, parent={"msg_id": parent}
                    ))),
                ]

            async def receive(self):
                return self.events.pop(0)

        b.channel = Channel()
        for i in range(100):
            result = await b.execute(f"value_{i} = {i}")
            self.assertEqual(result, "(completed without text output)")

        self.assertEqual(len(b.cells), 100)
        self.assertEqual(len({cell["id"] for cell in b.cells}), 100)
        self.assertEqual(b.save.await_count, 200)
        self.assertEqual(b.state_machine.state, AppState.WORKSPACE_READY)


if __name__ == "__main__":
    unittest.main()
