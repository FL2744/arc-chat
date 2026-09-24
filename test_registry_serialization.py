"""Round-trip and recovery compatibility checks for persisted registries."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from apps import ApplicationManifest, ApplicationRegistry
from helper import Bridge
from providers import default_provider_registry
from projects import ProjectManifest, ProjectRegistry
from services import EndpointRegistry
from workspaces import WorkspaceRecord, WorkspaceRegistry


class RegistryRoundTripTests(unittest.TestCase):
    def test_project_application_provider_workspace_and_endpoint_round_trip(self):
        projects = ProjectRegistry()
        project = projects.ensure(ProjectManifest(
            id="research-a", name="Research A", allowed_providers=("browser", "arc"),
        ))
        projects.set_current(project.manifest.id)
        projects.link(project.manifest.id, "job", "12345", active=True)
        self.assertEqual(
            ProjectRegistry.from_records(projects.export_records(), current_project_id="research-a").current().active_job_id,
            "12345",
        )

        applications = ApplicationRegistry([ApplicationManifest(
            id="demo", name="Demo", project_id="research-a", application_type="service",
            persistent_service=True, metadata={"template": "fastapi"},
        )])
        restored_apps = ApplicationRegistry.from_records(applications.export_records())
        self.assertEqual(restored_apps.get("demo").public_dict(), applications.get("demo").public_dict())

        providers = default_provider_registry(jupyterlite_url="https://notebooks.example.edu/lab")
        restored_providers = type(providers).from_records(providers.export_records())
        self.assertEqual(restored_providers.public_dicts(), providers.public_dicts())

        workspaces = WorkspaceRegistry()
        workspace = workspaces.ensure(
            workspace_id="ws_0123456789abcdef0123456789abcdef",
            project_id="research-a", provider_id="arc", kind="interactive", state="ready",
        )
        workspace.link("job", "12345")
        restored_workspaces = WorkspaceRegistry.from_records(
            workspaces.export_records(), current_workspace_id=workspace.id,
        )
        self.assertEqual(restored_workspaces.current().public_dict(), workspace.public_dict())

        endpoints = EndpointRegistry()
        endpoint = endpoints.upsert(
            provider="managed", model="gpt", endpoint="http://127.0.0.1:8000/v1",
            reachability="loopback_tunnel", job_id="12345",
        )
        restored_endpoints = EndpointRegistry.from_records(endpoints.export_records())
        self.assertEqual(restored_endpoints.list()[0].public_dict(), endpoint.public_dict())

    def test_registries_reject_secret_metadata_and_provider_url_primary_ids(self):
        with self.assertRaisesRegex(ValueError, "credential-like"):
            ProjectManifest(id="research-a", name="Research A", metadata={"api_key": "should-not-persist"})
        with self.assertRaisesRegex(ValueError, "credential-like"):
            ApplicationManifest(id="demo", name="Demo", project_id="research-a", metadata={"token": "bad"})
        endpoints = EndpointRegistry()
        with self.assertRaisesRegex(ValueError, "credentials"):
            endpoints.upsert(
                provider="managed", model="gpt", endpoint="https://user:pass@example.edu/v1",
                reachability="direct",
            )


class RecoveryMigrationTests(unittest.TestCase):
    def test_v1_v2_v3_and_v4_recovery_files_round_trip_as_v4(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recovery.json"
            previous = os.environ.get("ARC_CHAT_RECOVERY_STATE")
            os.environ["ARC_CHAT_RECOVERY_STATE"] = str(path)
            try:
                workspace = WorkspaceRecord(
                    id="workspace:legacy-v4", project_id="fl2744", provider_id="arc",
                    state="ready", display_name="Recovered workspace",
                )
                project = ProjectManifest(id="fl2744", name="FL 2744", kind="course", audience="course")
                project_registry = ProjectRegistry()
                project_registry.ensure(project)
                for version in (1, 2, 3, 4):
                    payload = {
                        "version": version,
                        "profile": "fl2744",
                        "job_id": "12345",
                        "notebook_path": "prior.ipynb",
                    }
                    if version >= 2:
                        payload["jobs"] = []
                        payload["artifacts"] = []
                    if version >= 3:
                        payload["projects"] = project_registry.export_records()
                        payload["current_project_id"] = "fl2744"
                    if version >= 4:
                        workspaces = WorkspaceRegistry([workspace])
                        workspaces.set_current(workspace.id)
                        payload["workspaces"] = workspaces.export_records()
                        payload["current_workspace_id"] = workspace.id
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    bridge = Bridge(enable_recovery=True)
                    self.assertEqual(bridge.last_job_id, "12345")
                    if version >= 3:
                        self.assertEqual(bridge.project_registry.current().manifest.id, "fl2744")
                    bridge.persist_recovery_state()
                    migrated = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(migrated["version"], 4)
                    self.assertNotIn("https://", json.dumps(migrated["workspaces"]))
                    self.assertEqual(migrated["job_id"], "12345")
            finally:
                if previous is None:
                    os.environ.pop("ARC_CHAT_RECOVERY_STATE", None)
                else:
                    os.environ["ARC_CHAT_RECOVERY_STATE"] = previous


if __name__ == "__main__":
    unittest.main()
