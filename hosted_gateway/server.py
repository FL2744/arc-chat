"""Authenticated, database-backed API for the hosted CLAHS control plane.

Provider mutations are intentionally capability-gated. Browser workspaces are
available immediately; ARC and Common Platform mutations stay disabled until
their institutional delegation and operator paths are configured.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from apps import ApplicationManifest
from control_plane import ControlPlane
from identifiers import new_id, require_canonical_id
from projects import ProjectManifest, ProjectRecord, ProjectRegistry
from providers import PlacementRequest, ProviderDescriptor, ProviderRegistry, default_provider_registry
from resolver import ResourceCandidate, ResourceExpectation, ResourceResolver
from workspaces import WorkspaceRecord
from .config import GatewayConfig
from .store import PostgresStore, json_value


logger = logging.getLogger("arc_chat.hosted_gateway")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_ACTIVE_WORKSPACE_STATES = {"new", "planning", "starting", "queued", "ready", "busy", "input_required", "degraded", "recovering", "stopping"}
CONFIG_KEY = web.AppKey("config", GatewayConfig)
STORE_KEY = web.AppKey("store", object)
METRICS_KEY = web.AppKey("metrics", dict)
OWNS_STORE_KEY = web.AppKey("owns_store", bool)
REQUEST_ID_KEY = web.RequestKey("request_id", str)
ACTOR_KEY = web.RequestKey("actor", dict)


def error_body(code: str, message: str, request_id: str, recovery_action: str = "") -> dict[str, Any]:
    return {
        "error_version": 1,
        "code": code,
        "message": message,
        "request_id": request_id,
        "recovery_action": recovery_action or "contact_support",
    }


def _idempotent_error(status: int, body: dict[str, Any], request_id: str) -> dict[str, Any]:
    if status < 400:
        return body
    if "error_version" not in body:
        normalized = error_body(
            str(body.get("code") or "REQUEST_FAILED"),
            str(body.get("message") or "The request could not be completed."),
            request_id,
            str(body.get("recovery_action") or "retry_or_contact_support"),
        )
        normalized.update({key: value for key, value in body.items() if key not in normalized})
        return normalized
    return {**body, "request_id": request_id}


def _provider_registry(config: GatewayConfig) -> ProviderRegistry:
    registry = default_provider_registry(jupyterlite_url=config.jupyterlite_url)
    providers = []
    for item in registry.list():
        if item.id == "browser":
            providers.append(item)
        else:
            providers.append(replace(
                item,
                status="planned",
                notes=(item.notes + " Hosted mutation is disabled pending an approved institutional adapter.").strip(),
            ))
    return ProviderRegistry(providers)


def _domain_control_plane(project: dict[str, Any], config: GatewayConfig) -> ControlPlane:
    manifests = ProjectRegistry()
    manifest = ProjectManifest(
        id=project["id"],
        name=project["name"],
        kind=project["kind"],
        audience=project["audience"],
        default_provider=project["default_provider"],
        allowed_providers=tuple(project["allowed_providers"]),
        data_classification=project["data_classification"],
    )
    manifests.ensure(manifest)
    manifests.set_current(manifest.id)
    providers = _provider_registry(config)
    from apps import ApplicationRegistry
    return ControlPlane(projects=manifests, providers=providers, applications=ApplicationRegistry())


def _application_domain(value: dict[str, Any]) -> ApplicationManifest:
    allowed = {
        "id", "name", "project_id", "application_type", "runtime", "provider", "audience",
        "entrypoint", "requires_gpu", "requires_server_packages", "persistent_service",
        "estimated_input_mb", "metadata",
    }
    manifest = {key: value[key] for key in allowed if key in value}
    return ApplicationManifest(**manifest)


def _origin_allowed(config: GatewayConfig, origin: str) -> bool:
    if not origin:
        return False
    return origin.rstrip("/").lower() in config.allowed_origins


def _trusted_proxy(request: web.Request, config: GatewayConfig) -> bool:
    remote = request.remote or ""
    if config.environment == "development" and config.read_header_identity_in_development:
        return True
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False
    return any(address in network for network in config.trusted_proxy_networks)


def _csrf_token(actor_id: str, config: GatewayConfig) -> tuple[str, str]:
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    message = f"{actor_id}:{day}".encode("utf-8")
    digest = hmac.new(config.csrf_hmac_key, message, hashlib.sha256).hexdigest()
    return day, digest


def _valid_csrf(request: web.Request, actor_id: str, config: GatewayConfig) -> bool:
    supplied = request.headers.get("X-CSRF-Token", "")
    cookie = request.cookies.get("arcchat_csrf", "")
    if not supplied or not cookie or not hmac.compare_digest(supplied, cookie):
        return False
    try:
        day, digest = cookie.split(".", 1)
    except ValueError:
        return False
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    yesterday = (datetime.now(timezone.utc).timestamp() - 86400)
    previous_day = datetime.fromtimestamp(yesterday, timezone.utc).strftime("%Y%m%d")
    if day not in {today, previous_day}:
        return False
    expected = hmac.new(config.csrf_hmac_key, f"{actor_id}:{day}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


@web.middleware
async def gateway_middleware(request: web.Request, handler):
    config: GatewayConfig = request.app[CONFIG_KEY]
    started = time.monotonic()
    incoming_id = request.headers.get("X-Request-ID", "")
    request_id = incoming_id if _REQUEST_ID.fullmatch(incoming_id) else str(uuid.uuid4())
    request[REQUEST_ID_KEY] = request_id
    origin = request.headers.get("Origin", "")
    actor: dict[str, str] | None = None

    def finish(response: web.StreamResponse) -> web.StreamResponse:
        if not response.prepared:
            response.headers["X-Request-ID"] = request_id
            if actor:
                response.headers["Cache-Control"] = "no-store"
            _secure_response(response, request_id, origin=origin)
        duration_ms = round((time.monotonic() - started) * 1000, 2)
        logger.info(json.dumps({
            "event": "http_request", "request_id": request_id, "method": request.method,
            "path": request.path, "status": response.status, "duration_ms": duration_ms,
            "actor_id": actor["id"] if actor else "",
        }, separators=(",", ":")))
        request.app[METRICS_KEY][f"{request.method}:{response.status}"] = request.app[METRICS_KEY].get(f"{request.method}:{response.status}", 0) + 1
        return response

    if origin and not _origin_allowed(config, origin):
        response = web.json_response(error_body("ORIGIN_DENIED", "This browser origin is not allowed.", request_id, "use_authorized_site"), status=403)
        return finish(response)
    if request.method == "OPTIONS":
        if not _origin_allowed(config, origin):
            response = web.json_response(error_body("ORIGIN_DENIED", "This browser origin is not allowed.", request_id), status=403)
        else:
            response = web.Response(status=204)
        return finish(response)

    public_paths = {"/health/live", "/health/ready", "/metrics"}
    if request.path not in public_paths:
        if not _trusted_proxy(request, config):
            response = web.json_response(error_body("UNTRUSTED_AUTH_PROXY", "Authentication headers are accepted only from the configured auth proxy.", request_id, "contact_support"), status=401)
            return finish(response)
        subject = request.headers.get(config.auth_user_header, "").strip()
        email = request.headers.get(config.auth_email_header, "").strip()
        if (not subject or len(subject) > 320 or any(ord(ch) < 32 for ch in subject)
                or len(email) > 320 or any(ord(ch) < 32 for ch in email)):
            response = web.json_response(error_body("AUTH_REQUIRED", "Sign in with Virginia Tech to continue.", request_id, "sign_in"), status=401)
            return finish(response)
        try:
            actor = await request.app[STORE_KEY].user_for_subject(subject, email, config.identity_hmac_key)
        except PermissionError:
            response = web.json_response(error_body("ACCOUNT_DISABLED", "This account is not enabled for the hosted gateway.", request_id), status=403)
            return finish(response)
        raw_groups = request.headers.get(config.auth_groups_header, "")
        groups = [part.strip() for part in raw_groups.split(",") if part.strip()]
        if (len(groups) > config.max_auth_groups
                or any(len(group) > 255 or any(ord(ch) < 32 for ch in group) for group in groups)):
            response = web.json_response(error_body("AUTH_CLAIMS_INVALID", "The identity provider returned invalid group claims.", request_id, "contact_support"), status=401)
            return finish(response)
        try:
            await request.app[STORE_KEY].sync_course_group_memberships(actor["id"], groups)
        except Exception:
            logger.exception(json.dumps({"event": "authorization_sync_failed", "request_id": request_id}))
            response = web.json_response(error_body("AUTHORIZATION_UNAVAILABLE", "Project access could not be checked.", request_id, "retry_or_contact_support"), status=503)
            return finish(response)
        request[ACTOR_KEY] = actor
        if request.method in _MUTATING:
            if not _origin_allowed(config, origin) or not _valid_csrf(request, actor["id"], config):
                response = web.json_response(error_body("CSRF_REJECTED", "Refresh the session and retry from the authorized site.", request_id, "refresh_session"), status=403)
                return finish(response)
        if not request.path.startswith("/api/v1/session"):
            allowed = await request.app[STORE_KEY].check_rate_limit(actor["id"], config.rate_limit_per_minute)
            if not allowed:
                response = web.json_response(error_body("RATE_LIMITED", "Request rate is temporarily limited.", request_id, "retry_later"), status=429)
                return finish(response)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = web.json_response(error_body("HTTP_ERROR", exc.reason or "Request failed.", request_id), status=exc.status)
    except Exception:
        logger.exception(json.dumps({"event": "request_failed", "request_id": request_id, "path": request.path}))
        response = web.json_response(error_body("INTERNAL_ERROR", "The request could not be completed.", request_id, "retry_or_contact_support"), status=500)
    # Streaming routes install security headers before prepare; finish logs and
    # counts them without mutating headers after bytes have gone on the wire.
    return finish(response)


def _secure_response(response: web.StreamResponse, request_id: str, *, origin: str = ""):
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Idempotency-Key, X-CSRF-Token, X-Request-ID, Last-Event-ID"
        response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
        response.headers["Vary"] = "Origin"
    return response


def _actor(request: web.Request) -> dict[str, str]:
    actor = request.get(ACTOR_KEY)
    if not actor:
        raise web.HTTPUnauthorized(text="Authentication required.")
    return actor


async def _json_body(request: web.Request) -> dict[str, Any]:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Request body must use application/json.")
    try:
        value = await request.json()
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text="Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(text="Request body must be a JSON object.")
    return value


async def _project_or_error(request: web.Request, project_id: str) -> dict[str, Any]:
    try:
        require_canonical_id("project", project_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Project not found.") from exc
    project = await request.app[STORE_KEY].get_project(_actor(request)["id"], project_id)
    if not project:
        raise web.HTTPNotFound(text="Project not found.")
    return project


def _placement_body(value: dict[str, Any]) -> PlacementRequest:
    allowed = {"project_id", "mode", "preferred_provider", "needs_gpu", "requires_server_packages", "persistent_service", "estimated_input_mb"}
    if set(value) - allowed:
        raise web.HTTPBadRequest(text="Placement request contains unsupported fields.")
    for field in ("needs_gpu", "requires_server_packages", "persistent_service"):
        if field in value and not isinstance(value[field], bool):
            raise web.HTTPBadRequest(text=f"{field} must be a boolean.")
    try:
        size = value.get("estimated_input_mb")
        return PlacementRequest(
            mode=str(value.get("mode") or "interactive"),
            preferred_provider=str(value.get("preferred_provider") or "auto"),
            needs_gpu=bool(value.get("needs_gpu", False)),
            requires_server_packages=bool(value.get("requires_server_packages", False)),
            persistent_service=bool(value.get("persistent_service", False)),
            estimated_input_mb=int(size) if size is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc


async def health_live(_: web.Request) -> web.Response:
    return web.json_response({"status": "live"})


async def health_ready(request: web.Request) -> web.Response:
    try:
        await request.app[STORE_KEY].ready()
    except Exception:
        return web.json_response({"status": "not_ready"}, status=503)
    return web.json_response({"status": "ready", "database": "ready"})


async def metrics(request: web.Request) -> web.Response:
    lines = ["# TYPE arcchat_http_requests_total counter"]
    for label, value in sorted(request.app[METRICS_KEY].items()):
        method, status = label.split(":", 1)
        lines.append(f'arcchat_http_requests_total{{method="{method}",status="{status}"}} {value}')
    return web.Response(text="\n".join(lines) + "\n", content_type="text/plain; version=0.0.4")


async def session(request: web.Request) -> web.Response:
    actor = _actor(request)
    day, mac = _csrf_token(actor["id"], request.app[CONFIG_KEY])
    token = f"{day}.{mac}"
    projects = await request.app[STORE_KEY].list_projects(actor["id"])
    response = web.json_response({
        "authenticated": True,
        "user": {"id": actor["id"], "display_name": actor.get("email", "")},
        "projects": [item["id"] for item in projects],
        "capabilities": {
            "read_projects": True,
            "browser_workspaces": True,
            "arc_workspace_start": False,
            "common_platform_deploy": False,
            "events_sse": True,
        },
        "csrf_token": token,
    })
    response.set_cookie("arcchat_csrf", token, max_age=43200, secure=True, httponly=True, samesite="None", path="/")
    return response


async def projects_list(request: web.Request) -> web.Response:
    return web.json_response({"items": await request.app[STORE_KEY].list_projects(_actor(request)["id"])})


async def project_detail(request: web.Request) -> web.Response:
    project = await _project_or_error(request, request.match_info["project_id"])
    return web.json_response(project)


async def providers_list(request: web.Request) -> web.Response:
    return web.json_response({"items": _provider_registry(request.app[CONFIG_KEY]).public_dicts()})


async def applications_list(request: web.Request) -> web.Response:
    actor = _actor(request)
    project_id = request.query.get("project_id", "")
    if project_id:
        await _project_or_error(request, project_id)
        items = await request.app[STORE_KEY].list_applications(actor["id"], project_id)
    else:
        projects = await request.app[STORE_KEY].list_projects(actor["id"])
        grouped = await asyncio.gather(*(request.app[STORE_KEY].list_applications(actor["id"], project["id"]) for project in projects))
        items = [item for group in grouped for item in group]
    return web.json_response({"items": items})


async def application_detail(request: web.Request) -> web.Response:
    application_id = request.match_info["application_id"]
    try:
        require_canonical_id("application", application_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Application not found.") from exc
    item = await request.app[STORE_KEY].get_application(_actor(request)["id"], application_id)
    if not item:
        raise web.HTTPNotFound(text="Application not found.")
    return web.json_response(item)


async def placement_plan(request: web.Request) -> web.Response:
    value = await _json_body(request)
    project_id = str(value.get("project_id") or "")
    project = await _project_or_error(request, project_id)
    plan = _placement_body(value)
    decision = _domain_control_plane(project, request.app[CONFIG_KEY]).plan(plan)
    result = decision.public_dict()
    if decision.provider_id:
        result["provider"] = _provider_registry(request.app[CONFIG_KEY]).get(decision.provider_id).public_dict()
    result["project_id"] = project_id
    result["executed"] = False
    return web.json_response(result)


async def application_plan(request: web.Request) -> web.Response:
    application_id = request.match_info["application_id"]
    try:
        require_canonical_id("application", application_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Application not found.") from exc
    application = await request.app[STORE_KEY].get_application(_actor(request)["id"], application_id)
    if not application:
        raise web.HTTPNotFound(text="Application not found.")
    project = await _project_or_error(request, application["project_id"])
    try:
        manifest = _application_domain(application)
        plan = _domain_control_plane(project, request.app[CONFIG_KEY]).plan_application(manifest)
    except (TypeError, ValueError, KeyError) as exc:
        raise web.HTTPBadRequest(text="Application manifest is invalid.") from exc
    return web.json_response({"plan": plan.public_dict(), "executed": False})


async def workspaces_list(request: web.Request) -> web.Response:
    project_id = request.query.get("project_id")
    if project_id:
        await _project_or_error(request, project_id)
    items = await request.app[STORE_KEY].list_workspaces(_actor(request)["id"], project_id)
    return web.json_response({"items": items})


async def workspace_detail(request: web.Request) -> web.Response:
    workspace_id = request.match_info["workspace_id"]
    try:
        require_canonical_id("workspace", workspace_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Workspace not found.") from exc
    workspace = await request.app[STORE_KEY].get_workspace(_actor(request)["id"], workspace_id)
    if not workspace:
        raise web.HTTPNotFound(text="Workspace not found.")
    return web.json_response(workspace)


async def workspace_create(request: web.Request) -> web.Response:
    value = await _json_body(request)
    if set(value) - {"project_id", "provider_id", "application_id", "display_name", "reviewed_request_id"}:
        raise web.HTTPBadRequest(text="Workspace request contains unsupported fields.")
    actor = _actor(request)
    project_id = str(value.get("project_id") or "")
    project = await _project_or_error(request, project_id)
    provider_id = str(value.get("provider_id") or "")
    if provider_id != "browser":
        return web.json_response(error_body(
            "PROVIDER_UNAVAILABLE",
            "This provider cannot be started from the hosted gateway until its institutional delegation path is approved and configured.",
            request[REQUEST_ID_KEY],
            "use_local_arc_helper" if provider_id == "arc" else "contact_platform_admin",
        ), status=503)
    if not request.app[CONFIG_KEY].jupyterlite_url:
        return web.json_response(error_body(
            "PROVIDER_UNAVAILABLE",
            "The static JupyterLite deployment is not configured for this gateway.",
            request[REQUEST_ID_KEY], "contact_platform_admin",
        ), status=503)
    if "browser" not in project["allowed_providers"]:
        raise web.HTTPForbidden(text="Browser workspaces are not allowed for this project.")
    application_id = str(value.get("application_id") or "")
    if application_id:
        application = await request.app[STORE_KEY].get_application(actor["id"], application_id)
        if not application or application["project_id"] != project_id:
            raise web.HTTPNotFound(text="Application not found in this project.")
        if application.get("provider", "auto") not in {"auto", "browser"}:
            raise web.HTTPForbidden(text="This application is not configured for browser workspaces.")
    display_name = str(value.get("display_name") or "Browser workspace").strip()
    if not display_name or len(display_name) > 160:
        raise web.HTTPBadRequest(text="display_name must be 1-160 characters.")
    key = request.headers.get("Idempotency-Key", "")
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise web.HTTPBadRequest(text="Mutation requests require an Idempotency-Key between 16 and 128 safe characters.")
    request_hash = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    async def create_on_connection(connection):
        membership = await connection.fetchrow(
            """SELECT p.allowed_providers,m.role FROM projects p
               JOIN project_memberships m ON m.project_id=p.id
               WHERE p.id=$1 AND m.user_id=$2 AND p.deleted_at IS NULL""",
            project_id, actor["id"],
        )
        if not membership or "browser" not in membership["allowed_providers"]:
            return 403, error_body("PROJECT_FORBIDDEN", "You cannot start a browser workspace for this project.", request[REQUEST_ID_KEY])
        # Different idempotency keys can still represent the same semantic
        # workspace request. Serialize that check-and-create section so two
        # concurrent requests cannot both observe an empty active-workspace set.
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1),hashtext($2))",
            actor["id"], f"{project_id}:{application_id or ''}",
        )
        existing = await connection.fetchrow(
            """SELECT id,state FROM workspaces WHERE owner_id=$1 AND project_id=$2 AND provider_id='browser'
                 AND application_id IS NOT DISTINCT FROM $3 AND state=ANY($4::varchar[]) AND deleted_at IS NULL
               ORDER BY updated_at DESC LIMIT 1 FOR UPDATE""",
            actor["id"], project_id, application_id or None, list(_ACTIVE_WORKSPACE_STATES),
        )
        if existing:
            return 409, error_body("WORKSPACE_ALREADY_EXISTS", "An identical browser workspace is already active.", request[REQUEST_ID_KEY], "resume_workspace") | {"workspace_id": str(existing["id"]), "state": str(existing["state"])}
        workspace_id = new_id("workspace")
        metadata = {"source": "hosted_gateway"}
        await connection.execute(
            """INSERT INTO workspaces(id,owner_id,project_id,application_id,provider_id,kind,state,display_name,metadata)
               VALUES($1,$2,$3,$4,'browser','browser','ready',$5,$6::jsonb)""",
            workspace_id, actor["id"], project_id, application_id or None, display_name,
            json.dumps(metadata, separators=(",", ":")),
        )
        record = await connection.fetchrow("SELECT * FROM workspaces WHERE id=$1", workspace_id)
        public = request.app[STORE_KEY]._workspace(record)
        public["launch_url"] = request.app[CONFIG_KEY].jupyterlite_url
        await request.app[STORE_KEY].add_audit(
            connection, actor_id=actor["id"], project_id=project_id,
            object_type="workspace", object_id=workspace_id, action="workspace.create",
            result="success", request_id=request[REQUEST_ID_KEY], metadata={"provider_id": "browser", "kind": "browser"},
        )
        await request.app[STORE_KEY].add_event(
            connection, actor_id=actor["id"], project_id=project_id,
            event_type="workspace.ready", object_type="workspace", object_id=workspace_id,
            payload={"workspace_id": workspace_id, "state": "ready", "provider_id": "browser"},
        )
        return 201, {"workspace": public}

    status, response, _replayed = await request.app[STORE_KEY].run_idempotent(
        actor_id=actor["id"], key=key, request_hash=request_hash, operation=create_on_connection,
    )
    response = _idempotent_error(status, response, request[REQUEST_ID_KEY])
    return web.json_response(response, status=status)


async def workspace_resolve(request: web.Request) -> web.Response:
    value = await _json_body(request)
    if set(value) - {"project_id", "workspace_id", "provider_id", "expectation"}:
        raise web.HTTPBadRequest(text="Resolution accepts project/workspace/provider intent only; provider candidates are server-owned.")
    project = await _project_or_error(request, str(value.get("project_id") or ""))
    # Provider resources are never trusted when sent by the browser. Only records
    # discovered and persisted by a server-side provider adapter may be resolved.
    candidates = await request.app[STORE_KEY].list_provider_resources(
        _actor(request)["id"], project["id"], str(value.get("provider_id") or ""),
    )
    records = await request.app[STORE_KEY].list_workspaces(_actor(request)["id"], project["id"])
    registry = ProjectRegistry([ProjectRecord(
        manifest=ProjectManifest(
            id=project["id"], name=project["name"], kind=project["kind"],
            audience=project["audience"], default_provider=project["default_provider"],
            allowed_providers=tuple(project["allowed_providers"]), data_classification=project["data_classification"],
        ),
        workspace_ids=[item["id"] for item in records],
        job_ids=[rid for item in records for rid in item["job_ids"]],
    )])
    registry.set_current(project["id"])
    expected_value = value.get("expectation") if isinstance(value.get("expectation"), dict) else {}
    workspace_id = str(value.get("workspace_id") or expected_value.get("workspace_id") or "")
    allowed_expectations = {"workspace_id", "allocation", "application_type", "job_name", "expected_state", "cluster", "provider", "created_after"}
    if set(expected_value) - allowed_expectations:
        raise web.HTTPBadRequest(text="Resolution expectation contains unsupported fields.")
    if workspace_id:
        try:
            require_canonical_id("workspace", workspace_id)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="workspace_id must be a canonical workspace ID.") from exc
        selected_workspace = await request.app[STORE_KEY].get_workspace(_actor(request)["id"], workspace_id)
        if not selected_workspace or selected_workspace["project_id"] != project["id"]:
            raise web.HTTPNotFound(text="Workspace not found in this project.")
    expectation = ResourceExpectation(
        workspace_id=workspace_id,
        owner_id=_actor(request)["id"],
        allocation=str(expected_value.get("allocation") or ""),
        application_type=str(expected_value.get("application_type") or ""),
        job_name=str(expected_value.get("job_name") or ""),
        expected_state=str(expected_value.get("expected_state") or ""),
        cluster=str(expected_value.get("cluster") or ""),
        provider=str(value.get("provider_id") or expected_value.get("provider") or ""),
        created_after=str(expected_value.get("created_after") or ""),
    )
    candidates = [ResourceCandidate.from_job(item) for item in candidates]
    workspace_records = [WorkspaceRecord(
        id=item["id"], project_id=project["id"], provider_id=item["provider_id"], kind=item["kind"],
        state=item["state"], display_name=item["display_name"], job_ids=item["job_ids"],
        provider_resource_ids=item["provider_resource_ids"], endpoint_ids=item["endpoint_ids"],
        artifact_ids=item["artifact_ids"], deployment_ids=item["deployment_ids"],
        last_seen_at=item["last_seen_at"], created_at=item["created_at"], updated_at=item["updated_at"],
        metadata=item["metadata"],
    ) for item in records]
    decision = ResourceResolver().resolve(
        registry.current(), candidates, workspaces=workspace_records, expected=expectation,
    )
    # Public resource IDs are opaque DB IDs, never provider URLs or scheduler IDs.
    lookup = {item.resource_id: item.provider_resource_id for item in candidates if item.resource_id and item.provider_resource_id}
    public = decision.public_dict(include_provider_identifiers=False)
    if decision.resource_id:
        public["resource_id"] = lookup.get(decision.resource_id, "")
    return web.json_response(public)


async def workspace_stop(request: web.Request) -> web.Response:
    actor = _actor(request)
    workspace_id = request.match_info["workspace_id"]
    try:
        require_canonical_id("workspace", workspace_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Workspace not found.") from exc
    key = request.headers.get("Idempotency-Key", "")
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise web.HTTPBadRequest(text="Mutation requests require an Idempotency-Key.")
    request_hash = hashlib.sha256(f"stop:{workspace_id}".encode()).hexdigest()

    async def stop_on_connection(connection):
        row = await connection.fetchrow(
            """SELECT w.* FROM workspaces w JOIN project_memberships m ON m.project_id=w.project_id
               WHERE w.id=$1 AND m.user_id=$2 AND w.deleted_at IS NULL
                 AND (w.owner_id=$2 OR m.role IN ('instructor','researcher','project_admin','platform_admin'))
               FOR UPDATE OF w""",
            workspace_id, actor["id"],
        )
        if not row:
            return 404, error_body("WORKSPACE_NOT_FOUND", "Workspace not found.", request[REQUEST_ID_KEY])
        if row["provider_id"] != "browser":
            return 503, error_body("PROVIDER_UNAVAILABLE", "This workspace cannot be stopped through the hosted gateway until its provider adapter is approved and configured.", request[REQUEST_ID_KEY], "use_local_arc_helper")
        if row["state"] not in {"stopped", "failed"}:
            await connection.execute(
                "UPDATE workspaces SET state='stopped',updated_at=now(),last_seen_at=now(),version=version+1 WHERE id=$1 AND version=$2",
                workspace_id, row["version"],
            )
            await request.app[STORE_KEY].add_audit(
                connection, actor_id=actor["id"], project_id=str(row["project_id"]),
                object_type="workspace", object_id=workspace_id, action="workspace.stop",
                result="success", request_id=request[REQUEST_ID_KEY], metadata={"provider_id": "browser"},
            )
            await request.app[STORE_KEY].add_event(
                connection, actor_id=actor["id"], project_id=str(row["project_id"]),
                event_type="workspace.state_changed", object_type="workspace", object_id=workspace_id,
                payload={"workspace_id": workspace_id, "state": "stopped"},
            )
        updated = await connection.fetchrow("SELECT * FROM workspaces WHERE id=$1", workspace_id)
        return 202, {"workspace": request.app[STORE_KEY]._workspace(updated)}

    status, response, _ = await request.app[STORE_KEY].run_idempotent(
        actor_id=actor["id"], key=key, request_hash=request_hash, operation=stop_on_connection,
    )
    response = _idempotent_error(status, response, request[REQUEST_ID_KEY])
    return web.json_response(response, status=status)


async def workspace_jobs(request: web.Request) -> web.Response:
    workspace_id = request.match_info["workspace_id"]
    workspace = await request.app[STORE_KEY].get_workspace(_actor(request)["id"], workspace_id)
    if not workspace:
        raise web.HTTPNotFound(text="Workspace not found.")
    items = await request.app[STORE_KEY].list_jobs(_actor(request)["id"], workspace_id)
    return web.json_response({"items": items})


async def job_detail(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    try:
        require_canonical_id("job", job_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Job not found.") from exc
    job = await request.app[STORE_KEY].get_job(_actor(request)["id"], job_id)
    if not job:
        raise web.HTTPNotFound(text="Job not found.")
    return web.json_response(job)


async def job_logs(request: web.Request) -> web.Response:
    job = await request.app[STORE_KEY].get_job(_actor(request)["id"], request.match_info["job_id"])
    if not job:
        raise web.HTTPNotFound(text="Job not found.")
    return web.json_response(error_body("PROVIDER_UNAVAILABLE", "Provider log access is not enabled on the hosted gateway.", request[REQUEST_ID_KEY], "use_local_arc_helper"), status=503)


async def job_cancel(request: web.Request) -> web.Response:
    actor = _actor(request)
    job_id = request.match_info["job_id"]
    try:
        require_canonical_id("job", job_id)
    except ValueError as exc:
        raise web.HTTPNotFound(text="Job not found.") from exc
    if not await request.app[STORE_KEY].get_job(actor["id"], job_id):
        raise web.HTTPNotFound(text="Job not found.")
    key = request.headers.get("Idempotency-Key", "")
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise web.HTTPBadRequest(text="Mutation requests require an Idempotency-Key.")
    fingerprint = hashlib.sha256(f"cancel:{job_id}".encode("utf-8")).hexdigest()

    async def disabled_cancel(_connection):
        return 503, error_body(
            "PROVIDER_UNAVAILABLE", "Provider job cancellation is not enabled on the hosted gateway.",
            request[REQUEST_ID_KEY], "use_local_arc_helper",
        )

    status, body, _ = await request.app[STORE_KEY].run_idempotent(
        actor_id=actor["id"], key=key, request_hash=fingerprint, operation=disabled_cancel,
    )
    body = _idempotent_error(status, body, request[REQUEST_ID_KEY])
    return web.json_response(body, status=status)


async def artifacts_list(request: web.Request) -> web.Response:
    project_id = request.query.get("project_id", "")
    if project_id:
        await _project_or_error(request, project_id)
    return web.json_response({"items": await request.app[STORE_KEY].list_artifacts(_actor(request)["id"], project_id or None)})


async def deployments_list(request: web.Request) -> web.Response:
    project_id = request.query.get("project_id", "")
    if project_id:
        await _project_or_error(request, project_id)
    return web.json_response({"items": await request.app[STORE_KEY].list_deployments(_actor(request)["id"], project_id or None)})


async def events_stream(request: web.Request) -> web.StreamResponse:
    actor = _actor(request)
    try:
        last_seq = max(0, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError:
        last_seq = 0
    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })
    response.headers["Cache-Control"] = "no-store"
    _secure_response(response, request[REQUEST_ID_KEY], origin=request.headers.get("Origin", ""))
    await response.prepare(request)
    end_at = time.monotonic() + 1800
    while time.monotonic() < end_at and request.transport and not request.transport.is_closing():
        rows = await request.app[STORE_KEY].events_since(actor["id"], last_seq, limit=100)
        for row in rows:
            last_seq = int(row["event_seq"])
            payload = json.dumps({
                "id": row["id"], "type": row["event_type"],
                "object_type": row["object_type"], "object_id": row["object_id"],
                "data": json_value(row["payload"]), "created_at": row["created_at"].isoformat(),
            }, separators=(",", ":"))
            await response.write(f"id: {last_seq}\nevent: {row['event_type']}\ndata: {payload}\n\n".encode("utf-8"))
        if not rows:
            await response.write(b": heartbeat\n\n")
        await asyncio.sleep(2)
    await response.write_eof()
    return response


async def create_app(config: GatewayConfig | None = None, store: PostgresStore | None = None) -> web.Application:
    config = config or GatewayConfig.from_env()
    app = web.Application(middlewares=[gateway_middleware], client_max_size=config.max_request_bytes)
    app[CONFIG_KEY] = config
    app[STORE_KEY] = store
    app[METRICS_KEY] = {}
    app.router.add_get("/health/live", health_live)
    app.router.add_get("/health/ready", health_ready)
    app.router.add_get("/metrics", metrics)
    app.router.add_get("/api/v1/session", session)
    app.router.add_get("/api/v1/projects", projects_list)
    app.router.add_get("/api/v1/projects/{project_id}", project_detail)
    app.router.add_get("/api/v1/providers", providers_list)
    app.router.add_get("/api/v1/applications", applications_list)
    app.router.add_get("/api/v1/applications/{application_id}", application_detail)
    app.router.add_post("/api/v1/applications/{application_id}/plan", application_plan)
    app.router.add_post("/api/v1/placement", placement_plan)
    app.router.add_get("/api/v1/workspaces", workspaces_list)
    app.router.add_post("/api/v1/workspaces", workspace_create)
    app.router.add_post("/api/v1/workspaces/resolve", workspace_resolve)
    app.router.add_get("/api/v1/workspaces/{workspace_id}", workspace_detail)
    app.router.add_post("/api/v1/workspaces/{workspace_id}/stop", workspace_stop)
    app.router.add_get("/api/v1/workspaces/{workspace_id}/jobs", workspace_jobs)
    app.router.add_get("/api/v1/jobs/{job_id}", job_detail)
    app.router.add_get("/api/v1/jobs/{job_id}/logs", job_logs)
    app.router.add_post("/api/v1/jobs/{job_id}/cancel", job_cancel)
    app.router.add_get("/api/v1/artifacts", artifacts_list)
    app.router.add_get("/api/v1/deployments", deployments_list)
    app.router.add_get("/api/v1/events", events_stream)

    async def startup(application: web.Application):
        if application[STORE_KEY] is None:
            application[STORE_KEY] = await PostgresStore.connect(config.database_url)
            application[OWNS_STORE_KEY] = True
        else:
            application[OWNS_STORE_KEY] = False

    async def cleanup(application: web.Application):
        if application.get(OWNS_STORE_KEY):
            await application[STORE_KEY].close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = GatewayConfig.from_env()
    app = asyncio.run(create_app(config))
    web.run_app(app, host=config.host, port=config.port, access_log=None, shutdown_timeout=20)


if __name__ == "__main__":
    main()
