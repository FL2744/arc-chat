"""Project-level state for ARC Chat and future CLAHS research applications.

Projects are intentionally infrastructure-agnostic. They group workspaces,
jobs, endpoints, artifacts, and deployments without embedding credentials or
provider-specific connection details. The registry is safe to persist in ARC
Chat's local recovery state.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Mapping


PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
PROJECT_SCHEMA_VERSION = 1
_RESOURCE_KINDS = {"workspace", "job", "endpoint", "artifact", "deployment"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def stable_resource_id(prefix: str, value: str) -> str:
    """Return an opaque stable ID without persisting the source URL/path."""
    prefix = str(prefix or "resource").strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", prefix):
        raise ValueError("Invalid resource id prefix.")
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Cannot derive a resource id from an empty value.")
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True)
class ProjectManifest:
    id: str
    name: str
    kind: str = "research"
    audience: str = "private"
    course_profile: str = ""
    default_provider: str = "auto"
    allowed_providers: tuple[str, ...] = ("browser", "arc")
    data_classification: str = "low"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not PROJECT_ID_RE.fullmatch(self.id):
            raise ValueError(f"Invalid project id: {self.id!r}")
        if not self.name.strip() or len(self.name) > 160:
            raise ValueError("Project name must be 1-160 characters.")
        if self.kind not in {"course", "research", "application", "personal"}:
            raise ValueError("Unsupported project kind.")
        if self.audience not in {"private", "course", "vt", "public"}:
            raise ValueError("Unsupported project audience.")
        if self.data_classification not in {"low", "moderate", "restricted"}:
            raise ValueError("Unsupported project data classification.")
        providers = tuple(str(item).strip() for item in self.allowed_providers)
        if not providers or any(not item or len(item) > 64 for item in providers):
            raise ValueError("allowed_providers must contain short provider IDs.")
        object.__setattr__(self, "allowed_providers", providers)
        if self.default_provider != "auto" and self.default_provider not in providers:
            raise ValueError("default_provider must be auto or listed in allowed_providers.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Project metadata must be an object.")
        safe_metadata: dict[str, Any] = {}
        for key, value in self.metadata.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise ValueError("Project metadata keys must be short strings.")
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("Project metadata values must be scalar JSON values.")
            if isinstance(value, str) and len(value) > 1000:
                raise ValueError("Project metadata text is too long.")
            safe_metadata[key] = value
        object.__setattr__(self, "metadata", safe_metadata)

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_providers"] = list(self.allowed_providers)
        return data


@dataclass
class ProjectRecord:
    manifest: ProjectManifest
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    workspace_ids: list[str] = field(default_factory=list)
    job_ids: list[str] = field(default_factory=list)
    endpoint_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    deployment_ids: list[str] = field(default_factory=list)
    active_workspace_id: str = ""
    active_job_id: str = ""

    def _collection(self, kind: str) -> list[str]:
        if kind not in _RESOURCE_KINDS:
            raise ValueError(f"Unsupported project resource kind: {kind}")
        return getattr(self, f"{kind}_ids")

    def link(self, kind: str, resource_id: str, *, active: bool = False) -> None:
        resource_id = str(resource_id or "").strip()
        if not RESOURCE_ID_RE.fullmatch(resource_id):
            raise ValueError("Invalid project resource id.")
        values = self._collection(kind)
        if resource_id not in values:
            values.append(resource_id)
        if active and kind == "workspace":
            self.active_workspace_id = resource_id
        if active and kind == "job":
            self.active_job_id = resource_id
        self.updated_at = utc_now()

    def public_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.public_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workspace_ids": list(self.workspace_ids),
            "job_ids": list(self.job_ids),
            "endpoint_ids": list(self.endpoint_ids),
            "artifact_ids": list(self.artifact_ids),
            "deployment_ids": list(self.deployment_ids),
            "active_workspace_id": self.active_workspace_id,
            "active_job_id": self.active_job_id,
        }


class ProjectRegistry:
    """Bounded project registry used as ARC Chat's top-level recovery model."""

    def __init__(self, records: list[ProjectRecord] | None = None, *, limit: int = 100):
        self.limit = max(1, min(1000, int(limit)))
        self._records: dict[str, ProjectRecord] = {}
        self.current_project_id = ""
        for record in records or []:
            self.upsert_record(record)

    def upsert_record(self, record: ProjectRecord) -> ProjectRecord:
        self._records[record.manifest.id] = record
        while len(self._records) > self.limit:
            oldest = min(self._records.values(), key=lambda item: item.updated_at)
            self._records.pop(oldest.manifest.id, None)
        return record

    def ensure(self, manifest: ProjectManifest) -> ProjectRecord:
        existing = self._records.get(manifest.id)
        if existing:
            existing.manifest = manifest
            existing.updated_at = utc_now()
            return existing
        return self.upsert_record(ProjectRecord(manifest=manifest))

    def get(self, project_id: str) -> ProjectRecord:
        try:
            return self._records[str(project_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown project: {project_id}") from exc

    def set_current(self, project_id: str) -> ProjectRecord:
        record = self.get(project_id)
        self.current_project_id = record.manifest.id
        return record

    def current(self) -> ProjectRecord | None:
        if not self.current_project_id:
            return None
        return self._records.get(self.current_project_id)

    def link(self, project_id: str, kind: str, resource_id: str, *, active: bool = False) -> ProjectRecord:
        record = self.get(project_id)
        record.link(kind, resource_id, active=active)
        return record

    def list(self) -> list[ProjectRecord]:
        return sorted(self._records.values(), key=lambda item: item.updated_at, reverse=True)

    def export_records(self) -> list[dict[str, Any]]:
        return [record.public_dict() for record in self.list()]

    @classmethod
    def from_records(cls, values: Any, *, current_project_id: str = "", limit: int = 100) -> "ProjectRegistry":
        registry = cls(limit=limit)
        if not isinstance(values, list):
            return registry
        for value in values[-limit:]:
            if not isinstance(value, Mapping):
                continue
            manifest_value = value.get("manifest")
            if not isinstance(manifest_value, Mapping):
                continue
            try:
                manifest_allowed = {field.name for field in fields(ProjectManifest)}
                manifest_data = {key: manifest_value[key] for key in manifest_allowed if key in manifest_value}
                if isinstance(manifest_data.get("allowed_providers"), list):
                    manifest_data["allowed_providers"] = tuple(manifest_data["allowed_providers"])
                manifest = ProjectManifest(**manifest_data)
                record = ProjectRecord(
                    manifest=manifest,
                    created_at=str(value.get("created_at") or utc_now()),
                    updated_at=str(value.get("updated_at") or utc_now()),
                    workspace_ids=list(value.get("workspace_ids") or []),
                    job_ids=list(value.get("job_ids") or []),
                    endpoint_ids=list(value.get("endpoint_ids") or []),
                    artifact_ids=list(value.get("artifact_ids") or []),
                    deployment_ids=list(value.get("deployment_ids") or []),
                    active_workspace_id=str(value.get("active_workspace_id") or ""),
                    active_job_id=str(value.get("active_job_id") or ""),
                )
                for kind in _RESOURCE_KINDS:
                    for resource_id in list(record._collection(kind)):
                        if not RESOURCE_ID_RE.fullmatch(str(resource_id)):
                            raise ValueError("Invalid persisted resource id.")
                if record.active_workspace_id and record.active_workspace_id not in record.workspace_ids:
                    record.active_workspace_id = ""
                if record.active_job_id and record.active_job_id not in record.job_ids:
                    record.active_job_id = ""
                registry.upsert_record(record)
            except (TypeError, ValueError):
                continue
        if current_project_id in registry._records:
            registry.current_project_id = current_project_id
        return registry
