"""Operator CLI for provisioning canonical projects, apps, and course groups."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

from apps import ApplicationManifest
from identifiers import new_id, require_canonical_id
from projects import ProjectManifest
from .store import PostgresStore


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ROLES = {"student", "instructor", "researcher", "project_admin", "platform_admin", "service"}
_PROVIDERS = {"browser", "arc", "common-platform", "cloud"}
_APP_FIELDS = {
    "name", "application_type", "runtime", "provider", "audience", "entrypoint", "requires_gpu",
    "requires_server_packages", "persistent_service", "estimated_input_mb", "metadata",
}


def validate_project_spec(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Project spec must be a JSON object.")
    allowed = {
        "id", "slug", "name", "kind", "audience", "default_provider", "allowed_providers",
        "data_classification", "metadata", "course_groups", "applications",
    }
    if set(value) - allowed:
        raise ValueError("Project spec contains unsupported top-level fields.")
    slug = str(value.get("slug") or "").strip().lower()
    if not _SLUG.fullmatch(slug):
        raise ValueError("slug must contain 1-64 lowercase letters, numbers, or hyphens.")
    project_id = str(value.get("id") or new_id("project"))
    if value.get("id"):
        require_canonical_id("project", project_id)
    project_fields = {
        "id": project_id,
        "name": value.get("name"),
        "kind": value.get("kind", "course"),
        "audience": value.get("audience", "course"),
        "default_provider": value.get("default_provider", "auto"),
        "allowed_providers": tuple(value.get("allowed_providers", ("browser", "arc"))),
        "data_classification": value.get("data_classification", "low"),
        "metadata": value.get("metadata", {}),
    }
    allowed_providers = project_fields["allowed_providers"]
    if not isinstance(allowed_providers, (list, tuple)) or not allowed_providers or not set(allowed_providers) <= _PROVIDERS:
        raise ValueError("allowed_providers must list supported provider IDs.")
    project_fields["allowed_providers"] = tuple(allowed_providers)
    if not isinstance(project_fields["name"], str):
        raise ValueError("Project name is required.")
    project = ProjectManifest(**project_fields)

    groups = value.get("course_groups", [])
    if not isinstance(groups, list):
        raise ValueError("course_groups must be a list.")
    clean_groups = []
    seen_groups: set[str] = set()
    for item in groups:
        if not isinstance(item, dict) or set(item) != {"name", "role"}:
            raise ValueError("Each course_groups item must have exactly name and role.")
        name = str(item["name"]).strip()
        role = str(item["role"]).strip()
        if not name or len(name) > 255 or "," in name or any(ord(ch) < 32 for ch in name):
            raise ValueError("Course group names must be short, non-empty strings without control characters.")
        if role not in _ROLES:
            raise ValueError(f"Unsupported course group role: {role}.")
        if name in seen_groups:
            raise ValueError(f"Duplicate course group mapping: {name}.")
        seen_groups.add(name)
        clean_groups.append({"name": name, "role": role})

    apps = value.get("applications", [])
    if not isinstance(apps, list):
        raise ValueError("applications must be a list.")
    clean_apps = []
    seen_keys: set[str] = set()
    for item in apps:
        if not isinstance(item, dict):
            raise ValueError("Each application must be an object.")
        app_allowed = _APP_FIELDS | {"key", "id"}
        if set(item) - app_allowed:
            raise ValueError("Application spec contains unsupported fields.")
        key = str(item.get("key") or "").strip()
        if key and (not _KEY.fullmatch(key) or key in seen_keys):
            raise ValueError("Application keys must be unique lower-case identifiers.")
        if key:
            seen_keys.add(key)
        app_id = str(item.get("id") or new_id("application"))
        if item.get("id"):
            require_canonical_id("application", app_id)
        fields = {field: item[field] for field in _APP_FIELDS if field in item}
        for field in ("requires_gpu", "requires_server_packages", "persistent_service"):
            if field in fields and not isinstance(fields[field], bool):
                raise ValueError(f"Application {field} must be a boolean.")
        if "estimated_input_mb" in fields and (not isinstance(fields["estimated_input_mb"], int) or isinstance(fields["estimated_input_mb"], bool)):
            raise ValueError("Application estimated_input_mb must be an integer.")
        fields.update({"id": app_id, "project_id": project.id})
        application = ApplicationManifest(**fields)
        clean_apps.append({"key": key, "requested_id": str(item.get("id") or ""), "manifest": application})

    return {
        "slug": slug, "project": project, "requested_project_id": str(value.get("id") or ""),
        "course_groups": clean_groups, "applications": clean_apps,
    }


async def provision(store: PostgresStore, spec: dict[str, Any]) -> dict[str, Any]:
    project: ProjectManifest = spec["project"]
    async with store.pool.acquire() as connection:
        async with connection.transaction():
            existing = await connection.fetchrow("SELECT id FROM projects WHERE slug=$1 FOR UPDATE", spec["slug"])
            if existing:
                project_id = str(existing["id"])
                if spec["requested_project_id"] and spec["requested_project_id"] != project_id:
                    raise ValueError("The supplied project id does not match this slug's existing project.")
            else:
                project_id = project.id

            project = ProjectManifest(
                id=project_id,
                name=project.name,
                kind=project.kind,
                audience=project.audience,
                default_provider=project.default_provider,
                allowed_providers=project.allowed_providers,
                data_classification=project.data_classification,
                metadata=project.metadata,
            )
            await connection.execute(
                """INSERT INTO projects(id,slug,name,kind,audience,allowed_providers,default_provider,data_classification,manifest,updated_at,deleted_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,now(),NULL)
                   ON CONFLICT(id) DO UPDATE SET slug=EXCLUDED.slug,name=EXCLUDED.name,kind=EXCLUDED.kind,
                     audience=EXCLUDED.audience,allowed_providers=EXCLUDED.allowed_providers,
                     default_provider=EXCLUDED.default_provider,data_classification=EXCLUDED.data_classification,
                     manifest=EXCLUDED.manifest,updated_at=now(),deleted_at=NULL,version=projects.version+1""",
                project.id, spec["slug"], project.name, project.kind, project.audience,
                list(project.allowed_providers), project.default_provider, project.data_classification,
                json.dumps(project.public_dict(), separators=(",", ":")),
            )

            app_ids: dict[str, str] = {}
            for entry in spec["applications"]:
                key = entry["key"]
                app: ApplicationManifest = entry["manifest"]
                prior = None
                if key:
                    prior = await connection.fetchrow(
                        "SELECT id,project_id FROM applications WHERE project_id=$1 AND manifest->>'admin_key'=$2 AND deleted_at IS NULL FOR UPDATE",
                        project.id, key,
                    )
                if prior:
                    app_id = str(prior["id"])
                    if entry["requested_id"] and entry["requested_id"] != app_id:
                        raise ValueError("The supplied application id does not match the existing application key.")
                else:
                    app_id = entry["requested_id"] or app.id
                app_values = app.public_dict()
                app_values["id"] = app_id
                app_values["project_id"] = project.id
                stored_manifest = {**app_values, **({"admin_key": key} if key else {})}
                existing_app = await connection.fetchrow("SELECT project_id FROM applications WHERE id=$1 FOR UPDATE", app_id)
                if existing_app and str(existing_app["project_id"]) != project.id:
                    raise ValueError("Application ID is already assigned to another project.")
                await connection.execute(
                    """INSERT INTO applications(id,project_id,manifest,updated_at,deleted_at)
                       VALUES($1,$2,$3::jsonb,now(),NULL)
                       ON CONFLICT(id) DO UPDATE SET manifest=EXCLUDED.manifest,
                         updated_at=now(),deleted_at=NULL,version=applications.version+1""",
                    app_id, project.id, json.dumps(stored_manifest, separators=(",", ":")),
                )
                app_ids[key or app.name] = app_id

            await connection.execute("DELETE FROM course_group_mappings WHERE project_id=$1", project.id)
            for group in spec["course_groups"]:
                await connection.execute(
                    "INSERT INTO course_group_mappings(auth_group,project_id,role) VALUES($1,$2,$3)",
                    group["name"], project.id, group["role"],
                )
            await connection.execute(
                """INSERT INTO audit_events(id,project_id,object_type,object_id,action,result,request_id,metadata)
                   VALUES($1,$2,'project',$2,'platform.project.provision','success',$3,$4::jsonb)""",
                new_id("audit_event"), project.id, f"admin-provision:{new_id('platform_event')}",
                json.dumps({"course_group_count": len(spec["course_groups"]), "application_count": len(app_ids)}, separators=(",", ":")),
            )
    return {"project_id": project.id, "slug": spec["slug"], "application_ids": app_ids}


async def _run(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    spec = validate_project_spec(config)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Set DATABASE_URL to the approved PostgreSQL service.")
    store = await PostgresStore.connect(database_url)
    try:
        result = await provision(store, spec)
    finally:
        await store.close()
    print(json.dumps(result, indent=2, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="Operator-owned project manifest JSON")
    args = parser.parse_args()
    asyncio.run(_run(args.spec))


if __name__ == "__main__":
    main()
