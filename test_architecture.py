import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from config import CourseProfile, get_profile, load_profiles
from diagnostics import Doctor
from model_providers import EndpointPolicy, OpenAICompatibleProvider
from state import AppState, AppStateMachine, InvalidTransition
from helper import Bridge


class ProfileTests(unittest.TestCase):
    def test_builtin_course_profile_has_no_personal_allocation(self):
        profile = get_profile("fl2744")
        self.assertEqual(profile.allocation, "${ARC_COURSE_ALLOCATION}")
        self.assertEqual(profile.resolved_allocation({}), "")
        self.assertEqual(profile.resolved_allocation({"ARC_COURSE_ALLOCATION": "course-project"}), "course-project")
        self.assertTrue(profile.allows_model("arc", "gpt-oss-120b", False))
        self.assertFalse(profile.allows_model("openai", "gpt-oss-120b", False))
        self.assertFalse(profile.allows_model("arc", "unapproved-model", False))

    def test_profile_file_overlays_builtin_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps({"profiles": [{
                "id": "seminar", "name": "Research Seminar", "workspace_backend": "local",
                "model_provider": "openai", "advanced_mode": True,
            }]}), encoding="utf-8")
            profiles = load_profiles(path)
            self.assertEqual(profiles["seminar"].workspace_backend, "local")
            self.assertIn("fl2744", profiles)


class StateTests(unittest.TestCase):
    def test_state_machine_rejects_unplanned_transition(self):
        machine = AppStateMachine(AppState.READY_LOCAL)
        with self.assertRaises(InvalidTransition):
            machine.transition(AppState.EXECUTING)
        snapshot = machine.transition(AppState.AUTHENTICATING)
        self.assertEqual(snapshot.display, "Signing in")


class EndpointTests(unittest.TestCase):
    def test_endpoint_policy_blocks_local_targets_and_credentials(self):
        for endpoint in ("http://example.org/v1", "https://127.0.0.1/v1", "https://user:pass@example.org/v1"):
            with self.assertRaises(ValueError):
                EndpointPolicy.validate(endpoint)
        self.assertEqual(EndpointPolicy.validate("https://example.org/v1"), "https://example.org/v1")


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_after_is_honored_for_rate_limit(self):
        class Response:
            def __init__(self, status, result=None, headers=None):
                self.status = status
                self.result = result
                self.headers = headers or {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def text(self):
                return "busy"

            async def json(self):
                return self.result

        class Http:
            def __init__(self):
                self.responses = [Response(429, headers={"Retry-After": "0"}), Response(200, {"choices": []})]

            def post(self, *args, **kwargs):
                return self.responses.pop(0)

        delays = []

        async def sleep(value):
            delays.append(value)

        result = await OpenAICompatibleProvider(
            Http(), "https://example.org/v1", "test-key", max_attempts=2, sleep=sleep
        ).complete({"model": "mock"})
        self.assertEqual(result, {"choices": []})
        self.assertEqual(delays, [0.0])


class DoctorTests(unittest.IsolatedAsyncioTestCase):
    async def test_doctor_report_does_not_contain_secret(self):
        bridge = Bridge()
        bridge.key = "secret-key"
        report = await Doctor(bridge).run()
        rendered = json.dumps(report)
        self.assertNotIn("secret-key", rendered)
        self.assertIn("checks", report)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_state_excludes_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.json"
            previous = os.environ.get("ARC_CHAT_RECOVERY_STATE")
            os.environ["ARC_CHAT_RECOVERY_STATE"] = str(path)
            try:
                bridge = Bridge(enable_recovery=True)
                bridge.key = "model-secret-key"
                bridge.remember_secret("another-secret-value")
                bridge.base = "https://example.org/node/job/"
                bridge.notebook_path = "ARC-chat-test.ipynb"
                bridge.session = "session-123"
                bridge.last_job_id = "4567"
                bridge.persist_recovery_state()
                rendered = path.read_text(encoding="utf-8")
                self.assertNotIn("model-secret-key", rendered)
                self.assertNotIn("another-secret-value", rendered)
                saved = json.loads(rendered)
                self.assertEqual(saved["job_id"], "4567")
                self.assertEqual(saved["notebook_path"], "ARC-chat-test.ipynb")
            finally:
                if previous is None:
                    os.environ.pop("ARC_CHAT_RECOVERY_STATE", None)
                else:
                    os.environ["ARC_CHAT_RECOVERY_STATE"] = previous

    async def test_attach_can_resume_prior_kernel_without_replaying_code(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("ARC_CHAT_RECOVERY_STATE")
            os.environ["ARC_CHAT_RECOVERY_STATE"] = str(Path(directory) / "recovery.json")
            try:
                bridge = Bridge(enable_recovery=True)
                bridge.context = object()
                base = "https://example.org/node/job/"
                bridge.recovery_metadata = {
                    "version": 1,
                    "workspace_base": base,
                    "notebook_path": "ARC-chat-prior.ipynb",
                    "session_id": "session-old",
                    "job_id": "",
                }
                calls = []

                async def api(method, path, data=None):
                    calls.append((method, path))
                    if path == "api/sessions":
                        return [{"id": "session-old", "path": "ARC-chat-prior.ipynb", "kernel": {"id": "kernel-old"}}]
                    if path == "api/contents/ARC-chat-prior.ipynb":
                        return {"content": {"cells": [{"cell_type": "code", "source": "x=1", "outputs": []}]}}
                    raise AssertionError(path)

                opened = []
                async def open_channel():
                    opened.append(True)

                bridge.api = api
                bridge.open_channel = open_channel
                result = await bridge.attach(base + "tree/ARC-chat-prior.ipynb", "python3")
                self.assertIn("Recovered", result)
                self.assertEqual(bridge.kernel, "kernel-old")
                self.assertEqual(bridge.session, "session-old")
                self.assertEqual(bridge.cells[0]["source"], "x=1")
                self.assertTrue(opened)
                self.assertFalse(any(path == "api/sessions" and method == "POST" for method, path in calls))
            finally:
                if previous is None:
                    os.environ.pop("ARC_CHAT_RECOVERY_STATE", None)
                else:
                    os.environ["ARC_CHAT_RECOVERY_STATE"] = previous


if __name__ == "__main__":
    unittest.main()
