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


if __name__ == "__main__":
    unittest.main()
