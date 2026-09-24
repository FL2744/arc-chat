import unittest

from apps import ApplicationManifest
from projects import ProjectManifest, ProjectRegistry, stable_resource_id
from providers import PlacementEngine, PlacementRequest, default_provider_registry
from resolver import ResourceResolver


class ProjectFoundationTests(unittest.TestCase):
    def test_registry_groups_resources_without_storing_source_url(self):
        registry = ProjectRegistry()
        project = registry.ensure(ProjectManifest(id="fl2744", name="FL 2744", kind="course", audience="course"))
        registry.set_current(project.manifest.id)
        workspace_id = stable_resource_id("workspace", "https://ood.arc.vt.edu/node/1234/")
        registry.link("fl2744", "workspace", workspace_id, active=True)
        registry.link("fl2744", "job", "1234", active=True)
        exported = registry.export_records()
        self.assertEqual(exported[0]["active_job_id"], "1234")
        self.assertNotIn("ood.arc.vt.edu", str(exported))

    def test_registry_roundtrip_preserves_current_resources(self):
        registry = ProjectRegistry()
        registry.ensure(ProjectManifest(id="pineland", name="Pineland"))
        registry.set_current("pineland")
        registry.link("pineland", "job", "54321", active=True)
        restored = ProjectRegistry.from_records(registry.export_records(), current_project_id="pineland")
        self.assertEqual(restored.current().active_job_id, "54321")


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.engine = PlacementEngine(default_provider_registry(jupyterlite_url="https://example.edu/lab"))

    def test_small_browser_workload_prefers_jupyterlite(self):
        decision = self.engine.decide(PlacementRequest(mode="interactive", estimated_input_mb=2), allowed_providers=("browser", "arc"))
        self.assertEqual(decision.provider_id, "browser")
        self.assertFalse(decision.requires_review)

    def test_gpu_or_server_packages_go_to_arc(self):
        for request in (
            PlacementRequest(mode="interactive", needs_gpu=True),
            PlacementRequest(mode="interactive", requires_server_packages=True),
        ):
            self.assertEqual(self.engine.decide(request, allowed_providers=("browser", "arc")).provider_id, "arc")

    def test_persistent_service_stays_review_only_until_provider_is_enabled(self):
        manifest = ApplicationManifest(
            id="discourse-map", name="Discourse Map", project_id="fl2744",
            application_type="service", persistent_service=True,
        )
        decision = self.engine.decide(manifest.placement_request(), allowed_providers=("common-platform", "cloud"))
        self.assertEqual(decision.action, "review")
        self.assertTrue(decision.requires_review)


class ResolverTests(unittest.TestCase):
    def test_resolver_reuses_previous_project_job(self):
        registry = ProjectRegistry()
        project = registry.ensure(ProjectManifest(id="pineland", name="Pineland"))
        project.link("job", "111", active=True)
        decision = ResourceResolver().resolve(project, [
            {"job_id": "111", "state": "RUNNING", "name": "arc-chat"},
            {"job_id": "222", "state": "RUNNING", "name": "other-work"},
        ])
        self.assertEqual((decision.action, decision.resource_id, decision.confidence), ("reuse", "111", "high"))

    def test_resolver_never_guesses_between_unlinked_jobs(self):
        registry = ProjectRegistry()
        project = registry.ensure(ProjectManifest(id="fl2744", name="FL 2744", kind="course"))
        decision = ResourceResolver().resolve(project, [
            {"job_id": "111", "state": "RUNNING", "name": "jupyter"},
            {"job_id": "222", "state": "RUNNING", "name": "jupyter"},
        ])
        self.assertEqual(decision.action, "choose")
        self.assertEqual(decision.resource_id, "")

    def test_resolver_can_use_explicit_project_metadata(self):
        registry = ProjectRegistry()
        project = registry.ensure(ProjectManifest(id="fl2744", name="FL 2744", kind="course"))
        decision = ResourceResolver().resolve(project, [
            {"job_id": "333", "state": "PENDING", "name": "jupyter", "project_id": "fl2744"},
        ])
        self.assertEqual((decision.action, decision.resource_id), ("wait", "333"))


if __name__ == "__main__":
    unittest.main()
