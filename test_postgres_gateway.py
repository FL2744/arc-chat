"""End-to-end acceptance checks for the hosted gateway against PostgreSQL.

Set ARC_CHAT_TEST_DATABASE_URL to an isolated, disposable PostgreSQL database.
The GitHub Actions integration job provisions a fresh service database for this
suite; local unit runs skip it when no database is configured.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import unittest
import uuid

from aiohttp.test_utils import TestClient, TestServer

from hosted_gateway.admin import provision, validate_project_spec
from hosted_gateway.config import GatewayConfig
from hosted_gateway.server import create_app
from hosted_gateway.store import PostgresStore
from identifiers import new_id


DATABASE_ENV = "ARC_CHAT_TEST_DATABASE_URL"


@unittest.skipUnless(os.environ.get(DATABASE_ENV), "Set ARC_CHAT_TEST_DATABASE_URL to run PostgreSQL integration checks.")
class PostgresGatewayAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database_url = os.environ[DATABASE_ENV]
        self.store = await PostgresStore.connect(self.database_url)
        await self.store.migrate()
        self.origin = "https://static.integration.example.edu"
        self.group = "gateway-integration-" + uuid.uuid4().hex[:12]
        self.subject = "test-oidc-subject-" + uuid.uuid4().hex
        self.email = "student@example.edu"
        self.config = GatewayConfig(
            database_url=self.database_url,
            allowed_origins=(self.origin,),
            trusted_proxy_networks=(ipaddress.ip_network("127.0.0.0/8"),),
            identity_hmac_key=b"integration identity key with 32 bytes minimum",
            csrf_hmac_key=b"integration csrf key with at least 32 bytes",
            jupyterlite_url="https://notebooks.example.edu/lab/index.html",
            environment="development",
        )

        suffix = uuid.uuid4().hex[:12]
        self.project = await self._provision_project(
            slug=f"gateway-test-{suffix}", group=self.group, name="Authorized integration project",
        )
        self.other_project = await self._provision_project(
            slug=f"gateway-other-{suffix}", group=f"unassigned-{suffix}", name="Unassigned integration project",
            with_application=False,
        )
        self.client: TestClient | None = None
        self.store_open = True
        await self._start_gateway()

    async def asyncTearDown(self):
        await self._stop_gateway()
        if getattr(self, "store_open", False):
            await self.store.close()
            self.store_open = False

    async def _provision_project(self, *, slug: str, group: str, name: str, with_application: bool = True):
        applications = []
        if with_application:
            applications.append({
                "key": "browser-notebook", "name": "Browser notebook",
                "application_type": "jupyterlite", "provider": "browser", "audience": "course",
            })
        spec = validate_project_spec({
            "slug": slug,
            "name": name,
            "kind": "course",
            "audience": "course",
            "allowed_providers": ["browser", "arc"],
            "course_groups": [{"name": group, "role": "student"}],
            "applications": applications,
        })
        return await provision(self.store, spec)

    async def _start_gateway(self):
        self.client = TestClient(TestServer(await create_app(self.config, self.store)))
        await self.client.start_server()

    async def _stop_gateway(self):
        if self.client is not None:
            await self.client.close()
            self.client = None

    def _auth_headers(self, **extra: str) -> dict[str, str]:
        headers = {
            "X-Auth-Request-User": self.subject,
            "X-Auth-Request-Email": self.email,
            "X-Auth-Request-Groups": self.group,
            "Origin": self.origin,
        }
        headers.update(extra)
        return headers

    async def _sign_in(self) -> dict[str, str]:
        assert self.client is not None
        response = await self.client.get("/api/v1/session", headers=self._auth_headers())
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.actor_id = payload["user"]["id"]
        self.csrf = payload["csrf_token"]
        return payload

    def _mutation_headers(self, key: str) -> dict[str, str]:
        return self._auth_headers(
            Cookie=f"arcchat_csrf={self.csrf}",
            **{"X-CSRF-Token": self.csrf, "Idempotency-Key": key},
        )

    async def _create_workspace(self, payload: dict, key: str):
        assert self.client is not None
        response = await self.client.post(
            "/api/v1/workspaces", headers=self._mutation_headers(key), json=payload,
        )
        return response.status, await response.json()

    async def test_migration_ledger_is_current_and_reapplying_v1_is_safe(self):
        # PostgreSQL v0.5 has one checked-in schema revision. Recovery JSON's
        # independent v1-v4 migration cases are covered by test_registry_serialization.
        versions = await self.store.pool.fetch("SELECT version FROM schema_migrations ORDER BY version")
        self.assertEqual([int(row["version"]) for row in versions], [1])
        required_tables = {
            "users", "projects", "project_memberships", "applications", "workspaces",
            "provider_resources", "jobs", "endpoints", "artifacts", "deployments",
            "deployment_revisions", "provider_connections", "audit_events", "idempotency_keys",
            "platform_events", "rate_limits",
        }
        tables = await self.store.pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()",
        )
        self.assertTrue(required_tables.issubset({str(row["tablename"]) for row in tables}))

        await self.store.migrate()
        versions = await self.store.pool.fetch("SELECT version FROM schema_migrations ORDER BY version")
        self.assertEqual([int(row["version"]) for row in versions], [1])
        trigger_exists = await self.store.pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='audit_events_immutable' AND NOT tgisinternal)",
        )
        self.assertTrue(trigger_exists)

    async def test_authorization_workspace_races_resolution_audit_events_and_restart(self):
        assert self.client is not None
        await self._sign_in()

        projects_response = await self.client.get("/api/v1/projects", headers=self._auth_headers())
        self.assertEqual(projects_response.status, 200)
        visible_projects = (await projects_response.json())["items"]
        visible_ids = {item["id"] for item in visible_projects}
        self.assertIn(self.project["project_id"], visible_ids)
        self.assertNotIn(self.other_project["project_id"], visible_ids)

        allowed = await self.client.get(
            f"/api/v1/projects/{self.project['project_id']}", headers=self._auth_headers(),
        )
        denied = await self.client.get(
            f"/api/v1/projects/{self.other_project['project_id']}", headers=self._auth_headers(),
        )
        self.assertEqual(allowed.status, 200)
        self.assertEqual(denied.status, 404)
        denied_workspace = await self.client.post(
            "/api/v1/workspaces",
            headers=self._mutation_headers("unauthorized-" + uuid.uuid4().hex),
            json={
                "project_id": self.other_project["project_id"],
                "provider_id": "browser",
                "display_name": "Must not be created",
            },
        )
        self.assertEqual(denied_workspace.status, 404)

        workspace_payload = {
            "project_id": self.project["project_id"],
            "provider_id": "browser",
            "display_name": "Idempotent browser workspace",
        }
        same_key = uuid.uuid4().hex
        concurrent_retries = await asyncio.gather(
            self._create_workspace(workspace_payload, same_key),
            self._create_workspace(workspace_payload, same_key),
        )
        self.assertEqual([status for status, _ in concurrent_retries], [201, 201])
        workspace_id = concurrent_retries[0][1]["workspace"]["id"]
        self.assertEqual(concurrent_retries[1][1]["workspace"]["id"], workspace_id)

        changed_request = await self._create_workspace(
            {**workspace_payload, "display_name": "Changed payload"}, same_key,
        )
        self.assertEqual(changed_request[0], 409)
        self.assertEqual(changed_request[1]["code"], "IDEMPOTENCY_CONFLICT")

        duplicate = await self._create_workspace(workspace_payload, uuid.uuid4().hex)
        self.assertEqual(duplicate[0], 409)
        self.assertEqual(duplicate[1]["code"], "WORKSPACE_ALREADY_EXISTS")

        application_id = self.project["application_ids"]["browser-notebook"]
        application_payload = {
            **workspace_payload,
            "application_id": application_id,
            "display_name": "Concurrent application workspace",
        }
        raced_creates = await asyncio.gather(
            self._create_workspace(application_payload, uuid.uuid4().hex),
            self._create_workspace(application_payload, uuid.uuid4().hex),
        )
        self.assertEqual(sorted(status for status, _ in raced_creates), [201, 409])
        application_workspaces = await self.store.pool.fetchval(
            """SELECT count(*) FROM workspaces WHERE owner_id=$1 AND project_id=$2
               AND application_id=$3 AND deleted_at IS NULL""",
            self.actor_id, self.project["project_id"], application_id,
        )
        self.assertEqual(application_workspaces, 1)

        # Seed a server-discovered ARC record with deliberately sensitive
        # provider metadata; the resolver's public projection must omit it.
        arc_workspace_id = new_id("workspace")
        await self.store.pool.execute(
            """INSERT INTO workspaces(id,owner_id,project_id,provider_id,kind,state,display_name)
               VALUES($1,$2,$3,'arc','interactive','ready','ARC resolver fixture')""",
            arc_workspace_id, self.actor_id, self.project["project_id"],
        )
        opaque_resource_id = new_id("provider_resource")
        raw_job_id = "slurm-private-" + uuid.uuid4().hex[:10]
        canary = "fixture-secret-" + uuid.uuid4().hex
        private_metadata = {
            "access_token": canary,
            "provider_secret": canary,
            "database_password": canary,
            "notebook_url": f"https://user:{canary}@notebook.invalid/lab?token={canary}",
        }
        await self.store.pool.execute(
            """INSERT INTO provider_resources(
                   id,provider_id,provider_resource_id,resource_kind,owner_id,project_id,workspace_id,
                   state,allocation,application_type,cluster,name,created_at,metadata)
               VALUES($1,'arc',$2,'job',$3,$4,$5,'RUNNING','course-allocation','jupyter','test-cluster',
                      'private fixture',now(),$6::jsonb)""",
            opaque_resource_id, raw_job_id, self.actor_id, self.project["project_id"], arc_workspace_id,
            json.dumps(private_metadata),
        )

        resolution = await self.client.post(
            "/api/v1/workspaces/resolve",
            headers=self._mutation_headers("resolve-" + uuid.uuid4().hex),
            json={
                "project_id": self.project["project_id"],
                "provider_id": "arc",
                "workspace_id": arc_workspace_id,
                "expectation": {"workspace_id": arc_workspace_id},
            },
        )
        self.assertEqual(resolution.status, 200)
        resolution_text = await resolution.text()
        resolution_body = json.loads(resolution_text)
        self.assertEqual(resolution_body["action"], "reuse")
        self.assertEqual(resolution_body["resource_id"], opaque_resource_id)
        for private_value in (canary, raw_job_id, "notebook.invalid", "database_password"):
            self.assertNotIn(private_value, resolution_text)

        audit_rows = await self.store.pool.fetch(
            "SELECT id,actor_id,action,metadata::text AS metadata FROM audit_events WHERE project_id=$1",
            self.project["project_id"],
        )
        self.assertTrue(any(row["action"] == "workspace.create" for row in audit_rows))
        audit_text = json.dumps([dict(row) for row in audit_rows], default=str)
        for private_value in (canary, raw_job_id, self.email, self.group):
            self.assertNotIn(private_value, audit_text)
        self.assertTrue(any(row["actor_id"] == self.actor_id for row in audit_rows))

        event_rows = await self.store.events_since(self.actor_id, 0)
        self.assertTrue(any(row["event_type"] == "workspace.ready" for row in event_rows))
        stream = await self.client.get(
            "/api/v1/events", headers=self._auth_headers(**{"Last-Event-ID": "0"}),
        )
        try:
            stream_lines = [
                await asyncio.wait_for(stream.content.readline(), timeout=5)
                for _ in range(3)
            ]
        finally:
            stream.close()
        self.assertIn(b"workspace.ready", b"".join(stream_lines))
        self.assertIn(workspace_id.encode(), b"".join(stream_lines))

        workspace_count = await self.store.pool.fetchval(
            "SELECT count(*) FROM workspaces WHERE project_id=$1 AND deleted_at IS NULL",
            self.project["project_id"],
        )
        audit_count = len(audit_rows)
        event_count = await self.store.pool.fetchval(
            "SELECT count(*) FROM platform_events WHERE project_id=$1", self.project["project_id"],
        )
        audit_id = str(audit_rows[0]["id"])
        with self.assertRaises(Exception):
            await self.store.pool.execute("DELETE FROM audit_events WHERE id=$1", audit_id)

        await self._stop_gateway()
        await self.store.close()
        self.store_open = False
        self.store = await PostgresStore.connect(self.database_url)
        self.store_open = True
        await self._start_gateway()

        restarted_session = await self._sign_in()
        self.assertEqual(restarted_session["user"]["id"], self.actor_id)
        restarted_workspaces = await self.client.get(
            f"/api/v1/workspaces?project_id={self.project['project_id']}",
            headers=self._auth_headers(),
        )
        self.assertEqual(restarted_workspaces.status, 200)
        restarted_ids = {item["id"] for item in (await restarted_workspaces.json())["items"]}
        self.assertEqual(len(restarted_ids), workspace_count)
        self.assertIn(workspace_id, restarted_ids)
        self.assertEqual(
            await self.store.pool.fetchval("SELECT count(*) FROM audit_events WHERE project_id=$1", self.project["project_id"]),
            audit_count,
        )
        self.assertEqual(
            await self.store.pool.fetchval("SELECT count(*) FROM platform_events WHERE project_id=$1", self.project["project_id"]),
            event_count,
        )


if __name__ == "__main__":
    unittest.main()
