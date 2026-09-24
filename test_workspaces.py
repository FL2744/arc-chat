import unittest

from datetime import datetime, timedelta, timezone

from workspaces import WorkspaceRecord, WorkspaceRegistry, WorkspaceState, map_provider_state


class WorkspaceRegistryTests(unittest.TestCase):
    def test_workspace_roundtrip_keeps_only_provider_neutral_metadata(self):
        registry = WorkspaceRegistry()
        record = registry.ensure(
            workspace_id="workspace:0123456789abcdef",
            project_id="fl2744",
            provider_id="arc",
            kind="interactive",
            state="ready",
            display_name="ARC Jupyter workspace",
            metadata={"cluster": "Falcon"},
        )
        record.link("job", "12345")
        registry.set_current(record.id)
        restored = WorkspaceRegistry.from_records(
            registry.export_records(),
            current_workspace_id=record.id,
        )
        self.assertEqual(restored.current().provider_id, "arc")
        self.assertEqual(restored.current().job_ids, ["12345"])
        self.assertNotIn("https://", str(restored.export_records()))

    def test_workspace_identity_cannot_be_reassigned(self):
        registry = WorkspaceRegistry()
        registry.ensure(
            workspace_id="workspace:abc",
            project_id="fl2744",
            provider_id="arc",
        )
        with self.assertRaises(ValueError):
            registry.ensure(
                workspace_id="workspace:abc",
                project_id="other",
                provider_id="arc",
            )

    def test_invalid_state_and_nested_metadata_are_rejected(self):
        with self.assertRaises(ValueError):
            WorkspaceRecord(
                id="workspace:abc",
                project_id="fl2744",
                provider_id="arc",
                state="magic",
            )
        with self.assertRaises(ValueError):
            WorkspaceRecord(
                id="workspace:abc",
                project_id="fl2744",
                provider_id="arc",
                metadata={"nested": {"secret": "value"}},
            )

    def test_workspace_lifecycle_rejects_impossible_jumps_and_maps_provider_states(self):
        record = WorkspaceRecord(id="ws_0123456789abcdef0123456789abcdef", project_id="fl2744", provider_id="jupyter")
        with self.assertRaisesRegex(ValueError, "Invalid workspace transition"):
            record.transition("ready")
        record.transition("planning")
        record.transition("starting")
        self.assertEqual(record.observe_provider_state("PENDING"), WorkspaceState.QUEUED)
        self.assertEqual(record.observe_provider_state("RUNNING"), WorkspaceState.STARTING)
        record.transition("ready")
        self.assertEqual(record.observe_provider_state("busy"), WorkspaceState.BUSY)
        self.assertEqual(map_provider_state("common-platform", "Available"), WorkspaceState.READY)

    def test_registry_expires_stale_records_without_stopping_provider_resources(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        active = WorkspaceRecord(
            id="workspace:stale", project_id="fl2744", provider_id="arc",
            state="ready", created_at=old, updated_at=old, last_seen_at=old,
        )
        stopped = WorkspaceRecord(
            id="workspace:old", project_id="fl2744", provider_id="arc",
            state="stopped", created_at=old, updated_at=old, last_seen_at=old,
        )
        registry = WorkspaceRegistry([active, stopped])
        registry.set_current(active.id)
        removed = registry.expire_stale(stale_after=timedelta(days=2), terminal_after=timedelta(days=5))
        self.assertEqual(removed, [stopped.id])
        self.assertEqual(registry.get(active.id).state, "degraded")
        self.assertEqual(registry.current_workspace_id, active.id)

    def test_roundtrip_preserves_new_fields_and_rejects_secret_metadata(self):
        registry = WorkspaceRegistry()
        record = registry.ensure(
            workspace_id="ws_0123456789abcdef0123456789abcdef",
            project_id="fl2744", provider_id="common-platform", kind="service",
            state="ready", owner_id="usr_0123456789abcdef0123456789abcdef",
            metadata={"ingress_hostname": "app.example.edu"},
        )
        record.link("deployment", "dep_0123456789abcdef0123456789abcdef")
        restored = WorkspaceRegistry.from_records(registry.export_records(), current_workspace_id=record.id)
        self.assertEqual(restored.current().deployment_ids, ["dep_0123456789abcdef0123456789abcdef"])
        self.assertEqual(restored.current().owner_id, "usr_0123456789abcdef0123456789abcdef")
        self.assertTrue(restored.current().last_seen_at)
        with self.assertRaisesRegex(ValueError, "credential-like"):
            WorkspaceRecord(
                id="workspace:secret", project_id="fl2744", provider_id="arc",
                metadata={"jupyter_token": "do-not-persist"},
            )


if __name__ == "__main__":
    unittest.main()
