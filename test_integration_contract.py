import unittest
from unittest.mock import AsyncMock

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

import helper
from helper import Bridge, TOKEN, create_app
from apps import ApplicationManifest
from integration import IntegrationProposal, ProposalStore


class ProposalContractTests(unittest.TestCase):
    def test_proposal_validation_and_bounded_store(self):
        proposal = IntegrationProposal.create(
            kind="artifact_handoff",
            summary="Use report.csv in the next application",
            source="example-tool",
            payload={"artifact_id": "artifact-0123456789abcdef"},
        )
        self.assertFalse(hasattr(proposal, "execute"))
        store = ProposalStore(limit=2)
        store.add(proposal)
        store.create(kind="job", summary="Review job A", payload={"profile": "falcon-l40s-small"})
        store.create(kind="pipeline", summary="Review pipeline B", payload={"steps": ["clean", "analyze"]})
        self.assertEqual(len(store.list()), 2)
        with self.assertRaises(ValueError):
            store.create(kind="execute_now", summary="bypass review")
        with self.assertRaises(ValueError):
            store.create(kind="job", summary="x", payload={"blob": "x" * 5000})


class LoopbackIntegrationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = helper.bridge
        self.bridge = helper.bridge = Bridge()
        self.bridge.key = "do-not-expose-this-secret"
        self.bridge.dispatch = AsyncMock(side_effect=AssertionError("integration API must not dispatch ARC actions"))
        self.app = create_app(include_lifecycle=False, include_launch=False)
        self.server = TestServer(self.app, host="127.0.0.1")
        await self.server.start_server()
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        helper.bridge = self.original

    def url(self, path, *, token=True):
        suffix = ("?token=" + TOKEN) if token else ""
        return self.server.make_url(path + suffix)

    async def test_api_requires_token_and_status_is_non_secret(self):
        denied = await self.client.get(self.url("/api/v1/status", token=False))
        self.assertEqual(denied.status, 403)
        response = await self.client.get(self.url("/api/v1/status"))
        self.assertEqual(response.status, 200)
        payload = await response.json()
        rendered = str(payload)
        self.assertNotIn("do-not-expose-this-secret", rendered)
        self.assertFalse(payload["capabilities"]["execute_resource_mutation"])
        self.assertTrue(payload["capabilities"]["submit_review_proposal"])
        self.assertEqual(payload["project"]["manifest"]["id"], "fl2744")
        self.assertTrue(any(item["id"] == "browser" for item in payload["providers"]))


    async def test_projects_and_providers_are_read_only(self):
        projects = await self.client.get(self.url("/api/v1/projects"))
        self.assertEqual(projects.status, 200)
        project_payload = await projects.json()
        self.assertEqual(project_payload["current_project_id"], "fl2744")
        self.assertEqual(project_payload["items"][0]["manifest"]["id"], "fl2744")

        providers = await self.client.get(self.url("/api/v1/providers"))
        self.assertEqual(providers.status, 200)
        provider_ids = {item["id"] for item in (await providers.json())["items"]}
        self.assertTrue({"browser", "arc"} <= provider_ids)

        for path in ("/api/v1/projects", "/api/v1/workspaces", "/api/v1/providers", "/api/v1/applications"):
            response = await self.client.post(self.url(path), json={})
            self.assertEqual(response.status, 405)

    async def test_workspaces_are_project_scoped_read_only_records(self):
        record = self.bridge.remember_workspace(
            "browser",
            "https://fl2744.github.io/jupyterlite/lab/index.html",
            kind="browser",
            state="ready",
            display_name="Browser / JupyterLite",
        )
        response = await self.client.get(self.url("/api/v1/workspaces"))
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["current_workspace_id"], record.id)
        self.assertEqual(payload["items"][0]["provider_id"], "browser")
        self.assertNotIn("fl2744.github.io", str(payload))
        denied = await self.client.post(self.url("/api/v1/workspaces"), json={})
        self.assertEqual(denied.status, 405)
        self.bridge.dispatch.assert_not_awaited()

    async def test_read_only_application_and_placement_planning(self):
        self.bridge.control_plane.applications.register(ApplicationManifest(
            id="browser-notebook", name="Browser Notebook", project_id="fl2744",
            application_type="browser", provider="browser", audience="course",
        ))

        apps = await self.client.get(self.url("/api/v1/applications"))
        self.assertEqual(apps.status, 200)
        app_payload = await apps.json()
        self.assertEqual(app_payload["items"][0]["id"], "browser-notebook")

        placement = await self.client.post(
            self.url("/api/v1/placement"),
            json={"mode": "interactive", "estimated_input_mb": 2},
        )
        self.assertEqual(placement.status, 200)
        placement_payload = await placement.json()
        self.assertFalse(placement_payload["executed"])
        self.assertEqual(placement_payload["placement"]["provider_id"], "browser")

        plan = await self.client.post(self.url("/api/v1/applications/browser-notebook/plan"), json={})
        self.assertEqual(plan.status, 200)
        plan_payload = await plan.json()
        self.assertFalse(plan_payload["executed"])
        self.assertEqual(plan_payload["plan"]["provider_id"], "browser")
        self.bridge.dispatch.assert_not_awaited()

    async def test_external_post_only_creates_review_proposal(self):
        response = await self.client.post(
            self.url("/api/v1/proposals"),
            json={
                "kind": "job",
                "summary": "Analyze dataset on one L40S",
                "source": "example-client",
                "payload": {"resource_profile": "falcon-l40s-small", "artifact_id": "artifact-0123456789abcdef"},
            },
        )
        self.assertEqual(response.status, 202)
        payload = await response.json()
        self.assertFalse(payload["executed"])
        proposal_id = payload["proposal"]["id"]
        self.assertEqual(len(self.bridge.integration_proposals.list()), 1)
        self.bridge.dispatch.assert_not_awaited()

        delete = await self.client.delete(self.url(f"/api/v1/proposals/{proposal_id}"))
        self.assertEqual(delete.status, 200)
        self.assertFalse((await delete.json())["executed"])
        self.assertEqual(self.bridge.integration_proposals.list(), [])
        self.bridge.dispatch.assert_not_awaited()

    async def test_invalid_proposal_and_resource_mutation_routes_are_rejected(self):
        bad = await self.client.post(
            self.url("/api/v1/proposals"),
            json={"kind": "run_python", "summary": "execute this"},
        )
        self.assertEqual(bad.status, 400)
        for path in ("/api/v1/jobs", "/api/v1/artifacts", "/api/v1/status", "/api/v1/projects", "/api/v1/providers"):
            response = await self.client.post(self.url(path), json={})
            self.assertEqual(response.status, 405)


if __name__ == "__main__":
    unittest.main()
