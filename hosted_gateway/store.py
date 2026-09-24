"""PostgreSQL persistence boundary for the hosted control plane."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from apps import ApplicationManifest
from identifiers import new_id
from security import validate_public_metadata


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def safe_public_metadata(value: Any) -> dict[str, Any]:
    candidate = json_value(value)
    try:
        return validate_public_metadata(candidate, label="Stored public metadata")
    except ValueError:
        # Historical provider payloads are not allowed to leak credentials or
        # nested private state through a newer read API.
        return {}


def safe_resource_request(value: Any) -> dict[str, Any]:
    candidate = json_value(value)
    if not isinstance(candidate, dict):
        return {}
    allowed = {
        "mode", "cpus", "cores", "memory_gb", "memory_mb", "gpu_count", "gpu_type",
        "walltime", "estimated_input_mb", "requires_server_packages", "persistent_service",
        "allocation", "partition", "queue",
    }
    return {
        key: item for key, item in candidate.items()
        if key in allowed and isinstance(item, (str, int, float, bool, type(None)))
    }


class PostgresStore:
    def __init__(self, pool: Any):
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> "PostgresStore":
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("The hosted gateway requires asyncpg; install requirements-gateway.txt.") from exc
        pool = await asyncpg.create_pool(
            dsn=database_url,
            min_size=1,
            max_size=12,
            command_timeout=15,
            server_settings={"application_name": "arc-chat-gateway"},
        )
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> None:
        sql_path = Path(__file__).resolve().parent / "migrations" / "0001_core.sql"
        sql = sql_path.read_text(encoding="utf-8")
        async with self.pool.acquire() as connection:
            await connection.execute(sql)

    async def ready(self) -> bool:
        async with self.pool.acquire() as connection:
            return await connection.fetchval("SELECT 1") == 1

    async def user_for_subject(self, subject: str, email: str, identity_key: bytes) -> dict[str, str]:
        subject_hash = hmac.new(identity_key, subject.encode("utf-8"), hashlib.sha256).hexdigest()
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                """INSERT INTO users(id, subject_hash, preferred_email, updated_at)
                   VALUES ($1, $2, $3, now())
                   ON CONFLICT(subject_hash) DO UPDATE
                     SET preferred_email=COALESCE(NULLIF(EXCLUDED.preferred_email, ''), users.preferred_email),
                         updated_at=now()
                   RETURNING id, preferred_email, disabled_at""",
                new_id("user"), subject_hash, email or None,
            )
        if not row or row["disabled_at"] is not None:
            raise PermissionError("This account is disabled for the hosted gateway.")
        return {"id": str(row["id"]), "email": str(row["preferred_email"] or "")}

    async def project_role(self, user_id: str, project_id: str) -> str | None:
        return await self.pool.fetchval(
            """SELECT m.role FROM project_memberships m
               JOIN projects p ON p.id=m.project_id
               WHERE m.user_id=$1 AND m.project_id=$2 AND p.deleted_at IS NULL""",
            user_id, project_id,
        )

    async def sync_course_group_memberships(self, user_id: str, group_names: list[str]) -> None:
        """Apply only operator-configured ED group mappings from the trusted proxy."""
        groups = sorted(set(group_names))
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                # A row lock serializes overlapping logins so a slow stale group
                # snapshot cannot overwrite a newer one for the same user.
                await connection.fetchrow("SELECT id FROM users WHERE id=$1 FOR UPDATE", user_id)
                rows = await connection.fetch(
                    """SELECT project_id,role,auth_group FROM course_group_mappings
                       WHERE auth_group=ANY($1::text[]) ORDER BY project_id,role,auth_group""",
                    groups,
                ) if groups else []
                mapping_fingerprint = [
                    [str(row["project_id"]), str(row["role"]), str(row["auth_group"])]
                    for row in rows
                ]
                snapshot = json.dumps(
                    {"groups": groups, "mappings": mapping_fingerprint},
                    sort_keys=True, separators=(",", ":"),
                )
                groups_digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
                prior_digest = await connection.fetchval(
                    "SELECT groups_sha256 FROM user_group_snapshots WHERE user_id=$1 FOR UPDATE",
                    user_id,
                )
                if prior_digest and hmac.compare_digest(str(prior_digest), groups_digest):
                    return
                role_rank = {"student": 1, "researcher": 2, "instructor": 3, "project_admin": 4, "platform_admin": 5, "service": 6}
                chosen: dict[str, Any] = {}
                for row in rows:
                    project_id = str(row["project_id"])
                    current = chosen.get(project_id)
                    if current is None or role_rank[str(row["role"])] > role_rank[str(current["role"])]:
                        chosen[project_id] = row
                await connection.execute(
                    "DELETE FROM project_memberships WHERE user_id=$1 AND membership_source='course_group'",
                    user_id,
                )
                for project_id, row in chosen.items():
                    await connection.execute(
                        """INSERT INTO project_memberships(project_id,user_id,role,membership_source,source_group)
                           VALUES($1,$2,$3,'course_group',$4)
                           ON CONFLICT(project_id,user_id) DO UPDATE SET role=EXCLUDED.role,
                             membership_source='course_group',source_group=EXCLUDED.source_group
                           WHERE project_memberships.membership_source='course_group'""",
                        project_id, user_id, str(row["role"]), str(row["auth_group"]),
                    )
                await connection.execute(
                    """INSERT INTO user_group_snapshots(user_id,groups_sha256,synced_at)
                       VALUES($1,$2,now()) ON CONFLICT(user_id) DO UPDATE
                         SET groups_sha256=EXCLUDED.groups_sha256,synced_at=now()""",
                    user_id, groups_digest,
                )

    async def list_projects(self, user_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT p.*, m.role FROM projects p
               JOIN project_memberships m ON m.project_id=p.id
               WHERE m.user_id=$1 AND p.deleted_at IS NULL
               ORDER BY p.name, p.id""",
            user_id,
        )
        return [self._project(row) for row in rows]

    async def get_project(self, user_id: str, project_id: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            """SELECT p.*, m.role FROM projects p
               JOIN project_memberships m ON m.project_id=p.id
               WHERE m.user_id=$1 AND p.id=$2 AND p.deleted_at IS NULL""",
            user_id, project_id,
        )
        return self._project(row) if row else None

    @staticmethod
    def _project(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "slug": str(row["slug"]), "name": str(row["name"]),
            "kind": str(row["kind"]), "audience": str(row["audience"]),
            "allowed_providers": list(row["allowed_providers"]),
            "default_provider": str(row["default_provider"]),
            "data_classification": str(row["data_classification"]),
            "role": str(row["role"]), "version": int(row["version"]),
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
        }

    async def list_applications(self, user_id: str, project_id: str) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT a.id, a.project_id, a.manifest, a.version, a.created_at, a.updated_at
               FROM applications a JOIN project_memberships m ON m.project_id=a.project_id
               WHERE m.user_id=$1 AND a.project_id=$2 AND a.deleted_at IS NULL
               ORDER BY a.id""",
            user_id, project_id,
        )
        return [self._application(row) for row in rows]

    async def get_application(self, user_id: str, application_id: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            """SELECT a.id, a.project_id, a.manifest, a.version, a.created_at, a.updated_at
               FROM applications a JOIN project_memberships m ON m.project_id=a.project_id
               WHERE m.user_id=$1 AND a.id=$2 AND a.deleted_at IS NULL""",
            user_id, application_id,
        )
        return self._application(row) if row else None

    @staticmethod
    def _application(row: Any) -> dict[str, Any]:
        manifest = json_value(row["manifest"])
        if not isinstance(manifest, dict):
            manifest = {}
        allowed = {
            "id", "name", "project_id", "application_type", "runtime", "provider", "audience",
            "entrypoint", "requires_gpu", "requires_server_packages", "persistent_service",
            "estimated_input_mb", "metadata",
        }
        fields = {key: item for key, item in manifest.items() if key in allowed}
        fields["id"] = str(row["id"])
        fields["project_id"] = str(row["project_id"])
        application = ApplicationManifest(**fields)
        return {
            **application.public_dict(),
            "version": int(row["version"]), "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    async def list_workspaces(self, user_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT w.* FROM workspaces w
               JOIN project_memberships m ON m.project_id=w.project_id
               WHERE m.user_id=$1 AND w.deleted_at IS NULL
                 AND ($2::varchar IS NULL OR w.project_id=$2)
                 AND (w.owner_id=$1 OR m.role IN ('instructor','researcher','project_admin','platform_admin','service'))
               ORDER BY w.updated_at DESC""",
            user_id, project_id,
        )
        return [self._workspace(row) for row in rows]

    async def get_workspace(self, user_id: str, workspace_id: str) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(
            """SELECT w.* FROM workspaces w
               JOIN project_memberships m ON m.project_id=w.project_id
               WHERE m.user_id=$1 AND w.id=$2 AND w.deleted_at IS NULL
                 AND (w.owner_id=$1 OR m.role IN ('instructor','researcher','project_admin','platform_admin','service'))""",
            user_id, workspace_id,
        )
        return self._workspace(row) if row else None

    async def list_provider_resources(self, user_id: str, project_id: str, provider_id: str = "") -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT id,provider_id,provider_resource_id,resource_kind,owner_id,project_id,workspace_id,
                      state,allocation,application_type,cluster,name,created_at,last_seen_at,metadata
               FROM provider_resources
               WHERE owner_id=$1 AND project_id=$2 AND ($3='' OR provider_id=$3)
               ORDER BY last_seen_at DESC LIMIT 500""",
            user_id, project_id, provider_id,
        )
        return [{
            "resource_id": str(row["provider_resource_id"]),
            "provider_resource_id": str(row["id"]),
            "provider": str(row["provider_id"]),
            "kind": str(row["resource_kind"]),
            "owner_id": str(row["owner_id"] or ""),
            "project_id": str(row["project_id"] or ""),
            "workspace_id": str(row["workspace_id"] or ""),
            "state": str(row["state"]), "allocation": str(row["allocation"]),
            "application_type": str(row["application_type"]), "cluster": str(row["cluster"]),
            "name": str(row["name"]),
            "created_at": row["created_at"].isoformat() if row["created_at"] else "",
            "last_seen_at": row["last_seen_at"].isoformat(), "metadata": json_value(row["metadata"]),
        } for row in rows]

    async def _authorized_jobs(self, user_id: str, workspace_id: str | None = None, job_id: str | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT j.id,j.workspace_id,j.provider_resource_id,j.provider_id,j.state,j.name,j.submitted_at,
                      j.finished_at,j.command_sha256,j.resource_request,j.metadata,w.project_id,w.owner_id,
                      m.role
               FROM jobs j JOIN workspaces w ON w.id=j.workspace_id
               JOIN project_memberships m ON m.project_id=w.project_id
               WHERE m.user_id=$1 AND w.deleted_at IS NULL
                 AND (w.owner_id=$1 OR m.role IN ('instructor','researcher','project_admin','platform_admin','service'))
                 AND ($2::varchar IS NULL OR j.workspace_id=$2)
                 AND ($3::varchar IS NULL OR j.id=$3)
               ORDER BY j.submitted_at DESC NULLS LAST LIMIT 1000""",
            user_id, workspace_id, job_id,
        )
        return [{
            "id": str(row["id"]), "workspace_id": str(row["workspace_id"]),
            "provider_resource_id": str(row["provider_resource_id"] or ""),
            "provider_id": str(row["provider_id"]), "state": str(row["state"]),
            "name": str(row["name"]),
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else "",
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else "",
            "command_sha256": str(row["command_sha256"] or ""),
            "resource_request": safe_resource_request(row["resource_request"]),
            "metadata": safe_public_metadata(row["metadata"]),
        } for row in rows]

    async def list_jobs(self, user_id: str, workspace_id: str) -> list[dict[str, Any]]:
        return await self._authorized_jobs(user_id, workspace_id=workspace_id)

    async def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        rows = await self._authorized_jobs(user_id, job_id=job_id)
        return rows[0] if rows else None

    async def list_artifacts(self, user_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT a.id,a.project_id,a.workspace_id,a.owner_id,a.type,a.media_type,a.content_sha256,a.metadata,a.created_at
               FROM artifacts a JOIN project_memberships m ON m.project_id=a.project_id
               WHERE m.user_id=$1 AND a.deleted_at IS NULL
                 AND ($2::varchar IS NULL OR a.project_id=$2)
                 AND (a.owner_id=$1 OR m.role IN ('instructor','researcher','project_admin','platform_admin','service'))
               ORDER BY a.created_at DESC LIMIT 1000""",
            user_id, project_id,
        )
        return [{
            "id": str(row["id"]), "project_id": str(row["project_id"]),
            "workspace_id": str(row["workspace_id"] or ""), "type": str(row["type"]),
            "media_type": str(row["media_type"]), "content_sha256": str(row["content_sha256"] or ""),
            "metadata": safe_public_metadata(row["metadata"]), "created_at": row["created_at"].isoformat(),
        } for row in rows]

    async def list_deployments(self, user_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(
            """SELECT d.id,d.project_id,d.application_id,d.created_by,d.provider_id,d.environment,d.state,
                      d.image_digest,d.manifest_revision,d.endpoint_id,d.version,d.created_at,d.updated_at,m.role
               FROM deployments d JOIN project_memberships m ON m.project_id=d.project_id
               WHERE m.user_id=$1 AND d.deleted_at IS NULL AND ($2::varchar IS NULL OR d.project_id=$2)
               ORDER BY d.updated_at DESC LIMIT 1000""",
            user_id, project_id,
        )
        return [{
            "id": str(row["id"]), "project_id": str(row["project_id"]),
            "application_id": str(row["application_id"]), "provider_id": str(row["provider_id"]),
            "environment": str(row["environment"]), "state": str(row["state"]),
            "image_digest": str(row["image_digest"] or ""),
            "manifest_revision": str(row["manifest_revision"] or ""),
            "endpoint_id": str(row["endpoint_id"] or ""), "version": int(row["version"]),
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
        } for row in rows]

    async def events_since(self, user_id: str, last_seq: int, *, limit: int = 100) -> list[Any]:
        return await self.pool.fetch(
            """SELECT e.event_seq,e.id,e.event_type,e.object_type,e.object_id,e.payload,e.created_at
               FROM platform_events e JOIN project_memberships m ON m.project_id=e.project_id
               WHERE m.user_id=$1 AND e.event_seq>$2
               ORDER BY e.event_seq LIMIT $3""",
            user_id, last_seq, max(1, min(500, limit)),
        )

    async def check_rate_limit(self, actor_id: str, limit: int) -> bool:
        key = hashlib.sha256(actor_id.encode("utf-8")).hexdigest()
        async with self.pool.acquire() as connection:
            count = await connection.fetchval(
                """INSERT INTO rate_limits(actor_key,window_start,request_count)
                   VALUES($1,date_trunc('minute',now()),1)
                   ON CONFLICT(actor_key,window_start) DO UPDATE
                     SET request_count=rate_limits.request_count+1
                   RETURNING request_count""",
                key,
            )
            await connection.execute("DELETE FROM rate_limits WHERE window_start < now()-interval '2 days'")
        return int(count) <= limit

    @staticmethod
    def _workspace(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]), "project_id": str(row["project_id"]),
            "application_id": str(row["application_id"] or ""),
            "provider_id": str(row["provider_id"]), "kind": str(row["kind"]),
            "state": str(row["state"]), "provider_state": str(row["provider_state"]),
            "display_name": str(row["display_name"]),
            "job_ids": json_value(row["job_ids"]),
            "provider_resource_ids": json_value(row["provider_resource_ids"]),
            "endpoint_ids": json_value(row["endpoint_ids"]),
            "artifact_ids": json_value(row["artifact_ids"]),
            "deployment_ids": json_value(row["deployment_ids"]),
            "metadata": safe_public_metadata(row["metadata"]),
            "version": int(row["version"]),
            "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat(),
            "last_seen_at": row["last_seen_at"].isoformat(),
        }

    async def run_idempotent(
        self,
        *,
        actor_id: str,
        key: str,
        request_hash: str,
        operation: Callable[[Any], Awaitable[tuple[int, dict[str, Any]]]],
    ) -> tuple[int, dict[str, Any], bool]:
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                inserted = await connection.fetchval(
                    """INSERT INTO idempotency_keys(actor_id,key_hash,request_hash,expires_at)
                       VALUES($1,$2,$3,now()+interval '24 hours')
                       ON CONFLICT(actor_id,key_hash) DO NOTHING RETURNING actor_id""",
                    actor_id, key_hash, request_hash,
                )
                if not inserted:
                    prior = await connection.fetchrow(
                        "SELECT request_hash,status_code,response_body FROM idempotency_keys WHERE actor_id=$1 AND key_hash=$2 FOR UPDATE",
                        actor_id, key_hash,
                    )
                    if not prior or prior["request_hash"] != request_hash:
                        return 409, {"code": "IDEMPOTENCY_CONFLICT", "message": "This idempotency key was already used for a different request.", "recovery_action": "new_idempotency_key"}, False
                    if prior["status_code"] is None or prior["response_body"] is None:
                        return 409, {"code": "RESOURCE_CREATING", "message": "An identical resource request is already being processed.", "recovery_action": "retry_same_key"}, False
                    return int(prior["status_code"]), json_value(prior["response_body"]), True
                status, body = await operation(connection)
                await connection.execute(
                    "UPDATE idempotency_keys SET status_code=$3,response_body=$4::jsonb WHERE actor_id=$1 AND key_hash=$2",
                    actor_id, key_hash, status, json.dumps(body, separators=(",", ":")),
                )
                return status, body, False

    async def add_audit(
        self,
        connection: Any,
        *,
        actor_id: str,
        project_id: str | None,
        object_type: str,
        object_id: str | None,
        action: str,
        result: str,
        request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await connection.execute(
            """INSERT INTO audit_events(id,actor_id,project_id,object_type,object_id,action,result,request_id,metadata)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)""",
            new_id("audit_event"), actor_id, project_id, object_type, object_id, action, result,
            request_id, json.dumps(metadata or {}, separators=(",", ":")),
        )

    async def add_event(
        self,
        connection: Any,
        *,
        actor_id: str,
        project_id: str,
        event_type: str,
        object_type: str,
        object_id: str,
        payload: dict[str, Any],
    ) -> None:
        await connection.execute(
            """INSERT INTO platform_events(id,actor_id,project_id,event_type,object_type,object_id,payload)
               VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)""",
            new_id("platform_event"), actor_id, project_id, event_type, object_type, object_id,
            json.dumps(payload, separators=(",", ":")),
        )
