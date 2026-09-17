import asyncio
import base64
import json
import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config import CourseProfile, get_profile
from diagnostics import Doctor
from helper import Bridge, MAX_UPLOAD_BYTES
from model_providers import ARC_ENDPOINT, EndpointPolicy, OpenAICompatibleProvider
from state import AppState, AppStateMachine, TRANSITIONS


class StateMachineStressTests(unittest.TestCase):
    def test_every_declared_transition_is_executable(self):
        for source, targets in TRANSITIONS.items():
            for target in targets:
                with self.subTest(source=source, target=target):
                    machine = AppStateMachine(source)
                    snapshot = machine.transition(target, "stress")
                    self.assertEqual(snapshot.state, target)
                    self.assertEqual(machine.state, target)
                    self.assertEqual(machine.history[-1], snapshot)

    def test_seeded_random_walks_remain_valid(self):
        rng = random.Random(2744)
        for walk in range(64):
            machine = AppStateMachine(AppState.READY_LOCAL)
            for _ in range(256):
                targets = tuple(TRANSITIONS[machine.state])
                self.assertTrue(targets, machine.state)
                target = rng.choice(targets)
                snapshot = machine.transition(target, f"walk-{walk}")
                self.assertIsInstance(snapshot.state, AppState)
                self.assertEqual(machine.state, snapshot.state)
            self.assertEqual(len(machine.history), 256)


class EndpointPolicyStressTests(unittest.TestCase):
    def test_non_global_literal_addresses_are_rejected(self):
        blocked = (
            "https://127.0.0.1/v1",
            "https://0.0.0.0/v1",
            "https://10.1.2.3/v1",
            "https://100.64.0.1/v1",
            "https://169.254.169.254/v1",
            "https://192.168.1.1/v1",
            "https://198.18.0.1/v1",
            "https://224.0.0.1/v1",
            "https://[::1]/v1",
            "https://[fc00::1]/v1",
            "https://[fe80::1]/v1",
        )
        for endpoint in blocked:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)

    def test_local_names_and_embedded_credentials_are_rejected(self):
        blocked = (
            "https://localhost/v1",
            "https://thing.local/v1",
            "https://user@example.com/v1",
            "https://user:pass@example.com/v1",
            "http://example.com/v1",
        )
        for endpoint in blocked:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)
        self.assertEqual(EndpointPolicy.validate("https://api.example.com/v1/"), "https://api.example.com/v1")


class RetryStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_failures_are_bounded_and_eventually_succeed(self):
        class Response:
            def __init__(self, status, payload=None):
                self.status = status
                self.payload = payload or {}
                self.headers = {"Retry-After": "0"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def text(self):
                return "transient"

            async def json(self):
                return self.payload

        class Http:
            def __init__(self):
                self.calls = 0
                self.responses = [Response(429), Response(503), Response(200, {"choices": []})]

            def post(self, *args, **kwargs):
                self.calls += 1
                return self.responses.pop(0)

        delays = []

        async def sleep(value):
            delays.append(value)

        http = Http()
        result = await OpenAICompatibleProvider(
            http, "https://api.example.com/v1", "key", max_attempts=3, sleep=sleep
        ).complete({"model": "mock"})
        self.assertEqual(result, {"choices": []})
        self.assertEqual(http.calls, 3)
        self.assertEqual(delays, [0.0, 0.0])

    async def test_permanent_failure_is_not_retried(self):
        class Response:
            status = 401
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def text(self):
                return "no"

        class Http:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                return Response()

        http = Http()
        with self.assertRaisesRegex(RuntimeError, "401"):
            await OpenAICompatibleProvider(http, "https://api.example.com/v1", "key", max_attempts=5).complete({})
        self.assertEqual(http.calls, 1)


class CoursePolicyStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_student_profile_cannot_be_overridden_by_client_fields(self):
        bridge = Bridge()
        bridge.kernel = "synthetic-kernel"
        bridge.profile = get_profile("fl2744")
        malicious = [
            {"provider": "openai", "endpoint": "https://api.openai.com/v1", "model": "gpt-oss-120b", "advanced": True},
            {"provider": "custom", "endpoint": "https://api.example.com/v1", "model": "gpt-oss-120b", "advanced": True},
            {"provider": "arc", "endpoint": ARC_ENDPOINT, "model": "unapproved-model", "advanced": True},
        ]
        for payload in malicious:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                await bridge.chat(payload | {"key": "not-a-secret", "text": "test", "profile": "default"})

    async def test_arc_provider_cannot_redirect_course_key_to_other_host(self):
        bridge = Bridge()
        bridge.kernel = "synthetic-kernel"
        bridge.profile = get_profile("fl2744")
        with self.assertRaisesRegex(ValueError, "configured ARC endpoint"):
            await bridge.chat({
                "provider": "arc",
                "endpoint": "https://api.example.com/v1",
                "model": "gpt-oss-120b",
                "advanced": False,
                "key": "not-a-secret",
                "text": "test",
            })


class StudentFlowStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_start_during_login_is_graceful_and_retryable(self):
        bridge = Bridge()
        bridge.profile = CourseProfile(
            id="stress", name="Stress Course", allocation="course-allocation", allowed_models=("gpt-oss-120b",)
        )

        class FakeOOD:
            def __init__(self):
                self.open_calls = 0
                self.prepare_calls = 0

            async def open(self):
                self.open_calls += 1
                bridge.context = SimpleNamespace(pages=[SimpleNamespace(url="https://login.vt.edu/")])
                return "ARC navigation started. Complete VT login/MFA."

            async def prepare(self, account):
                self.prepare_calls += 1
                pages = [p for p in bridge.context.pages if "ood.arc.vt.edu" in p.url]
                if not pages:
                    raise ValueError("Finish VT login first.")
                return "prepared " + account

        fake = FakeOOD()
        bridge.ood = fake
        with self.assertRaisesRegex(ValueError, "login"):
            await bridge.start_workspace()
        self.assertEqual(fake.prepare_calls, 1)

        bridge.context.pages = [SimpleNamespace(url="https://ood.arc.vt.edu/pun/sys/dashboard")]
        second = await bridge.start_workspace()
        self.assertEqual(second, "prepared course-allocation")
        self.assertEqual(fake.prepare_calls, 2)

    async def test_240_independent_virtual_students_complete_orchestration(self):
        async def virtual_student(index):
            bridge = Bridge()
            bridge.profile = CourseProfile(
                id=f"s{index}", name=f"Student {index}", allocation=f"course-{index}", allowed_models=("gpt-oss-120b",)
            )
            calls = []

            class OOD:
                async def open(self):
                    calls.append("open")
                    bridge.context = SimpleNamespace(pages=[SimpleNamespace(url="https://ood.arc.vt.edu/pun/sys/dashboard")])
                    return "open"

                async def prepare(self, account):
                    calls.append(("prepare", account))
                    return "prepared"

                async def launch(self):
                    calls.append("launch")
                    return "launched"

                async def connect(self):
                    calls.append("connect")
                    return "connected-browser"

                async def discover_jupyter(self):
                    calls.append("discover")
                    return f"https://ood.arc.vt.edu/node/{index}/tree"

            class Workspace:
                async def start(self, url, kernel_name="python3"):
                    calls.append(("workspace", url, kernel_name))
                    return "attached"

            bridge.ood = OOD()
            bridge.workspace = Workspace()
            self.assertEqual(await bridge.dispatch("start_workspace", {}), "prepared")
            self.assertEqual(await bridge.dispatch("launch", {}), "launched")
            self.assertEqual(await bridge.dispatch("connect", {"kernel": "python3"}), "attached")
            return calls

        results = await asyncio.gather(*(virtual_student(i) for i in range(240)))
        self.assertEqual(len(results), 240)
        for index, calls in enumerate(results):
            self.assertEqual(calls[0], "open")
            self.assertEqual(calls[1], ("prepare", f"course-{index}"))
            self.assertIn("launch", calls)
            self.assertIn("connect", calls)
            self.assertIn("discover", calls)
            self.assertTrue(any(isinstance(call, tuple) and call[0] == "workspace" for call in calls))


class FileBoundaryStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_filename_and_size_boundaries_are_enforced(self):
        for name in ("../x.bin", "a/x.bin", "a\\x.bin", ".", ".."):
            bridge = Bridge()
            bridge.api = AsyncMock(return_value={})
            with self.subTest(name=name), self.assertRaises(ValueError):
                await bridge.dispatch("upload", {"name": name, "content": "eA=="})
            bridge.api.assert_not_awaited()

        bridge = Bridge()
        bridge.api = AsyncMock(return_value={})
        too_large = base64.b64encode(b"x" * (MAX_UPLOAD_BYTES + 4096)).decode("ascii")
        with self.assertRaises(ValueError):
            await bridge.dispatch("upload", {"name": "x.bin", "content": too_large})
        bridge.api.assert_not_awaited()

    async def test_remote_file_paths_are_encoded_before_jupyter_api_use(self):
        bridge = Bridge()
        bridge.api = AsyncMock(return_value={"content": []})
        await bridge.dispatch("files", {"path": "folder name/sub folder"})
        bridge.api.assert_awaited_once_with("GET", "api/contents/folder%20name/sub%20folder")


class DoctorStressTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_never_contains_known_sensitive_values(self):
        bridge = Bridge()
        bridge.key = "API-KEY-VERY-SECRET"
        bridge.history = [{"role": "user", "content": "CHAT-CONTENT-VERY-SECRET"}]
        bridge.notebook_path = "NOTEBOOK-SECRET-NAME.ipynb"
        report = await Doctor(bridge).run(full=False)
        rendered = json.dumps(report)
        for secret in (bridge.key, "CHAT-CONTENT-VERY-SECRET", "NOTEBOOK-SECRET-NAME.ipynb"):
            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
