import unittest

from workspaces import WorkspaceRecord, WorkspaceRegistry


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


if __name__ == "__main__":
    unittest.main()
