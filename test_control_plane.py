import unittest

from apps import ApplicationManifest, ApplicationRegistry
from config import get_profile
from control_plane import ControlPlane
from providers import PlacementRequest, default_provider_registry
from projects import ProjectManifest, ProjectRegistry


class ControlPlaneTests(unittest.TestCase):
    def test_profile_builds_project_and_provider_policy(self):
        plane = ControlPlane.for_profile(get_profile("fl2744"))
        snapshot = plane.snapshot()
        self.assertEqual(snapshot["project"]["manifest"]["id"], "fl2744")
        self.assertEqual(snapshot["project"]["manifest"]["allowed_providers"], ["browser", "arc"])
        self.assertEqual({item["id"] for item in snapshot["providers"]}, {"browser", "arc", "common-platform", "cloud"})

    def test_application_plan_uses_project_provider_policy(self):
        projects=ProjectRegistry()
        projects.ensure(ProjectManifest(
            id="research", name="Research", kind="research",
            allowed_providers=("browser", "arc", "common-platform"),
        ))
        projects.set_current("research")
        applications=ApplicationRegistry([
            ApplicationManifest(
                id="dashboard", name="Dashboard", project_id="research",
                application_type="service", persistent_service=True,
            )
        ])
        plane=ControlPlane(
            projects=projects,
            providers=default_provider_registry(),
            applications=applications,
        )
        plan=plane.plan_application("dashboard")
        self.assertEqual(plan.application_id,"dashboard")
        self.assertEqual(plan.provider_id,"common-platform")
        self.assertTrue(plan.requires_review)
        self.assertEqual(plane.snapshot()["applications"][0]["id"],"dashboard")

    def test_plan_and_resolution_share_project_policy(self):
        plane = ControlPlane.for_profile(get_profile("fl2744"))
        self.assertEqual(plane.plan(PlacementRequest(mode="interactive", estimated_input_mb=2)).provider_id, "browser")
        project = plane.current_project()
        project.link("job", "777", active=True)
        decision = plane.resolve([
            {"job_id": "888", "state": "RUNNING", "name": "other"},
            {"job_id": "777", "state": "RUNNING", "name": "arc-chat"},
        ])
        self.assertEqual((decision.action, decision.resource_id), ("reuse", "777"))


if __name__ == "__main__":
    unittest.main()
