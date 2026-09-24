import json
import tempfile
import unittest
from pathlib import Path

from apps import ApplicationManifest, ApplicationRegistry, DeploymentPlan, load_applications
from providers import PlacementDecision


class ApplicationManifestTests(unittest.TestCase):
    def test_loader_validates_schema_and_registry_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apps.json"
            path.write_text(json.dumps({
                "version": 1,
                "applications": [
                    {
                        "id": "notebook",
                        "name": "Notebook",
                        "project_id": "fl2744",
                        "application_type": "browser",
                        "provider": "browser",
                        "audience": "course",
                    },
                    {
                        "id": "dashboard",
                        "name": "Dashboard",
                        "project_id": "research",
                        "application_type": "service",
                        "persistent_service": True,
                    },
                ],
            }), encoding="utf-8")
            registry = load_applications(path)
            self.assertEqual([item.id for item in registry.list(project_id="fl2744")], ["notebook"])
            self.assertEqual(registry.get("dashboard").application_type, "service")

    def test_unknown_fields_and_provider_are_rejected(self):
        with self.assertRaises(ValueError):
            ApplicationManifest(id="x", name="X", project_id="p", provider="mystery")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apps.json"
            path.write_text(json.dumps({
                "version": 1,
                "applications": [{"id": "x", "name": "X", "project_id": "p", "unexpected": True}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown application fields"):
                load_applications(path)

    def test_deployment_plan_is_non_mutating_metadata(self):
        manifest = ApplicationManifest(
            id="dashboard", name="Dashboard", project_id="research",
            application_type="service", persistent_service=True,
        )
        decision = PlacementDecision(
            provider_id="common-platform",
            action="review",
            reason="Planned target",
            confidence="medium",
            requires_review=True,
        )
        plan = DeploymentPlan.from_decision(manifest, decision)
        self.assertEqual(plan.provider_id, "common-platform")
        self.assertTrue(plan.requires_review)
        self.assertFalse(hasattr(plan, "deploy"))


if __name__ == "__main__":
    unittest.main()
