"""HTTP boundary checks for the optional hosted gateway."""

from __future__ import annotations

import ipaddress
import unittest
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from hosted_gateway.config import GatewayConfig, _https_origin
from hosted_gateway.server import _csrf_token, _valid_csrf, create_app


class FakeStore:
    def __init__(self):
        self.groups: list[tuple[str, list[str]]] = []

    async def user_for_subject(self, subject, email, identity_key):
        return {"id": "usr_0123456789abcdef0123456789abcdef", "email": email}

    async def sync_course_group_memberships(self, user_id, group_names):
        self.groups.append((user_id, list(group_names)))

    async def check_rate_limit(self, actor_id, limit):
        return True

    async def list_projects(self, user_id):
        return []

    async def get_project(self, user_id, project_id):
        if project_id != "prj_0123456789abcdef0123456789abcdef":
            return None
        return {
            "id": project_id, "slug": "research", "name": "Research",
            "kind": "research", "audience": "private", "allowed_providers": ["arc", "browser"],
            "default_provider": "auto", "data_classification": "low", "role": "student", "version": 1,
        }

    async def list_provider_resources(self, user_id, project_id, provider_id=""):
        return [{
            "resource_id": "provider-job-123",
            "provider_resource_id": "prsrc_0123456789abcdef0123456789abcdef",
            "provider": "arc", "kind": "job", "owner_id": user_id,
            "project_id": project_id, "workspace_id": "", "state": "RUNNING",
            "allocation": "lab-allocation", "application_type": "jupyter", "cluster": "Falcon",
            "name": "analysis", "created_at": "2026-09-24T12:00:00+00:00",
            "last_seen_at": "2026-09-24T12:00:00+00:00", "metadata": {"access_token": "never return"},
        }]

    async def list_workspaces(self, user_id, project_id=None):
        return []

    async def get_workspace(self, user_id, workspace_id):
        return None

    async def ready(self):
        return True


class HostedGatewayBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def test_https_origin_normalizes_default_port_and_rejects_url_components(self):
        self.assertEqual(_https_origin("https://STATIC.example.edu:443/"), "https://static.example.edu")
        with self.assertRaises(ValueError):
            _https_origin("https://static.example.edu/path")
        with self.assertRaises(ValueError):
            _https_origin("https://static.example.edu/?next=/")
        with self.assertRaises(ValueError):
            _https_origin("https://@static.example.edu")
        values = {
            "DATABASE_URL": "postgresql://gateway.invalid/database",
            "STATIC_ALLOWED_ORIGINS": "https://static.example.edu",
            "TRUSTED_AUTH_PROXY_CIDRS": "127.0.0.1/32",
            "IDENTITY_HMAC_KEY": "identity test key that is at least 32 bytes",
            "CSRF_HMAC_KEY": "csrf test key that is at least 32 bytes long",
            "JUPYTERLITE_URL": "https://notebooks.example.edu:bad/lab",
        }
        with self.assertRaisesRegex(ValueError, "invalid port"):
            GatewayConfig.from_env(values)

    async def asyncSetUp(self):
        self.store = FakeStore()
        self.origin = "https://static.example.edu"
        self.config = GatewayConfig(
            database_url="postgresql://gateway.invalid/database",
            allowed_origins=(self.origin,),
            trusted_proxy_networks=(ipaddress.ip_network("127.0.0.0/8"),),
            identity_hmac_key=b"identity test key that is at least 32 bytes",
            csrf_hmac_key=b"csrf test key that is at least 32 bytes long",
            environment="production",
        )
        self.client = TestClient(TestServer(await create_app(self.config, self.store)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def auth_headers(self, **overrides):
        headers = {
            "X-Auth-Request-User": "immutable-oidc-subject",
            "X-Auth-Request-Email": "student@vt.edu",
            "X-Auth-Request-Groups": "FL2744-students, ARC-users",
            "Origin": self.origin,
        }
        headers.update(overrides)
        return headers

    async def test_session_syncs_only_proxy_supplied_group_claims(self):
        response = await self.client.get("/api/v1/session", headers=self.auth_headers())
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(self.store.groups, [
            ("usr_0123456789abcdef0123456789abcdef", ["FL2744-students", "ARC-users"]),
        ])
        cookie = response.headers["Set-Cookie"].lower()
        self.assertIn("secure", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=none", cookie)
        self.assertIn("no-store", response.headers["Cache-Control"])

    async def test_disallowed_origin_is_rejected_before_identity_sync(self):
        response = await self.client.get(
            "/api/v1/session", headers=self.auth_headers(Origin="https://attacker.example"),
        )
        self.assertEqual(response.status, 403)
        self.assertEqual(self.store.groups, [])

    async def test_group_claim_overflow_fails_closed(self):
        config = GatewayConfig(
            database_url=self.config.database_url,
            allowed_origins=self.config.allowed_origins,
            trusted_proxy_networks=self.config.trusted_proxy_networks,
            identity_hmac_key=self.config.identity_hmac_key,
            csrf_hmac_key=self.config.csrf_hmac_key,
            max_auth_groups=1,
        )
        client = TestClient(TestServer(await create_app(config, self.store)))
        await client.start_server()
        try:
            response = await client.get("/api/v1/session", headers=self.auth_headers())
            self.assertEqual(response.status, 401)
            self.assertEqual(self.store.groups, [])
        finally:
            await client.close()

    async def test_preflight_is_limited_to_configured_static_origin(self):
        accepted = await self.client.options("/api/v1/workspaces", headers={"Origin": self.origin})
        rejected = await self.client.options("/api/v1/workspaces", headers={"Origin": "https://attacker.example"})
        self.assertEqual(accepted.status, 204)
        self.assertEqual(accepted.headers["Access-Control-Allow-Origin"], self.origin)
        self.assertEqual(rejected.status, 403)

    async def test_mutation_requires_session_csrf_cookie_and_header(self):
        response = await self.client.post(
            "/api/v1/placement", headers=self.auth_headers(), json={"mode": "interactive"},
        )
        self.assertEqual(response.status, 403)
        self.assertEqual((await response.json())["code"], "CSRF_REJECTED")

    async def test_resolution_uses_server_owned_candidates_and_returns_opaque_ids(self):
        session_response = await self.client.get("/api/v1/session", headers=self.auth_headers())
        csrf = (await session_response.json())["csrf_token"]
        headers = self.auth_headers(**{
            "Cookie": f"arcchat_csrf={csrf}",
            "X-CSRF-Token": csrf,
        })
        response = await self.client.post(
            "/api/v1/workspaces/resolve",
            headers=headers,
            json={"project_id": "prj_0123456789abcdef0123456789abcdef", "provider_id": "arc"},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["action"], "reuse")
        self.assertEqual(payload["resource_id"], "prsrc_0123456789abcdef0123456789abcdef")
        self.assertNotIn("provider-job-123", str(payload))
        self.assertNotIn("access_token", str(payload))
        self.assertNotIn("owner_id", payload["candidates"][0])

    async def test_resolution_rejects_client_supplied_provider_candidates(self):
        session_response = await self.client.get("/api/v1/session", headers=self.auth_headers())
        csrf = (await session_response.json())["csrf_token"]
        headers = self.auth_headers(**{"Cookie": f"arcchat_csrf={csrf}", "X-CSRF-Token": csrf})
        response = await self.client.post(
            "/api/v1/workspaces/resolve", headers=headers,
            json={"project_id": "prj_0123456789abcdef0123456789abcdef", "candidates": [{"resource_id": "spoofed"}]},
        )
        self.assertEqual(response.status, 400)

    def test_csrf_token_is_bound_to_actor_and_current_or_previous_utc_day(self):
        actor_id = "usr_0123456789abcdef0123456789abcdef"
        token = ".".join(_csrf_token(actor_id, self.config))
        request = SimpleNamespace(headers={"X-CSRF-Token": token}, cookies={"arcchat_csrf": token})
        self.assertTrue(_valid_csrf(request, actor_id, self.config))
        self.assertFalse(_valid_csrf(request, "usr_ffffffffffffffffffffffffffffffff", self.config))
        request.headers["X-CSRF-Token"] = "not-the-cookie"
        self.assertFalse(_valid_csrf(request, actor_id, self.config))


if __name__ == "__main__":
    unittest.main()
