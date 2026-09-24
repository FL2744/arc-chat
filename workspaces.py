"""Provider-neutral workspace records for ARC Chat's control plane."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
WORKSPACE_STATES = {
    "new", "starting", "queued", "ready", "busy", "input_required",
    "degraded", "stopping", "stopped", "failed", "unknown",
}
WORKSPACE_KINDS = {"browser", "interactive", "batch", "service"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@dataclass
class WorkspaceRecord:
    id: str
    project_id: str
    provider_id: str
    kind: str = "interactive"
    state: str = "new"
    display_name: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    job_ids: list[str] = field(default_factory=list)
    endpoint_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not WORKSPACE_ID_RE.fullmatch(self.id):
            raise ValueError("Invalid workspace id.")
        if not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ValueError("Invalid workspace project id.")
        if not PROVIDER_ID_RE.fullmatch(self.provider_id):
            raise ValueError("Invalid workspace provider id.")
        if self.kind not in WORKSPACE_KINDS:
            raise ValueError("Unsupported workspace kind.")
        if self.state not in WORKSPACE_STATES:
            raise ValueError("Unsupported workspace state.")
        if len(self.display_name) > 160:
            raise ValueError("Workspace display name is too long.")
        for values in (self.job_ids, self.endpoint_ids, self.artifact_ids):
            if any(not WORKSPACE_ID_RE.fullmatch(str(value)) for value in values):
                raise ValueError("Invalid linked workspace resource id.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Workspace metadata must be an object.")
        safe: dict[str, Any] = {}
        for key, value in self.metadata.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise ValueError("Workspace metadata keys must be short strings.")
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("Workspace metadata values must be scalar JSON values.")
            if isinstance(value, str) and len(value) > 1000:
                raise ValueError("Workspace metadata text is too long.")
            safe[key] = value
        self.metadata = safe

    def transition(self, state: str) -> None:
        state = str(state or "").strip().lower()
        if state not in WORKSPACE_STATES:
            raise ValueError("Unsupported workspace state.")
        self.state = state
        self.updated_at = utc_now()

    def link(self, kind: str, resource_id: str) -> None:
        if kind not in {"job", "endpoint", "artifact"}:
            raise ValueError("Unsupported workspace resource kind.")
        resource_id = str(resource_id or "").strip()
        if not WORKSPACE_ID_RE.fullmatch(resource_id):
            raise ValueError("Invalid workspace resource id.")
        values = getattr(self, f"{kind}_ids")
        if resource_id not in values:
            values.append(resource_id)
        self.updated_at = utc_now()

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceRegistry:
    def __init__(self, records: list[WorkspaceRecord] | None = None, *, limit: int = 200):
        self.limit = max(1, min(2000, int(limit)))
        self._items: dict[str, WorkspaceRecord] = {}
        self.current_workspace_id = ""
        for record in records or []:
            self.upsert(record)

    def upsert(self, record: WorkspaceRecord) -> WorkspaceRecord:
        self._items[record.id] = record
        while len(self._items) > self.limit:
            oldest = min(self._items.values(), key=lambda item: item.updated_at)
            self._items.pop(oldest.id, None)
        return record

    def ensure(
        self,
        *,
        workspace_id: str,
        project_id: str,
        provider_id: str,
        kind: str = "interactive",
        state: str = "new",
        display_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceRecord:
        existing = self._items.get(workspace_id)
        if existing:
            if existing.project_id != project_id or existing.provider_id != provider_id:
                raise ValueError("Workspace identity cannot be reassigned to another project/provider.")
            existing.kind = kind
            existing.display_name = display_name or existing.display_name
            if metadata:
                existing.metadata.update(metadata)
            existing.transition(state)
            return existing
        return self.upsert(WorkspaceRecord(
            id=workspace_id,
            project_id=project_id,
            provider_id=provider_id,
            kind=kind,
            state=state,
            display_name=display_name,
            metadata=metadata or {},
        ))

    def get(self, workspace_id: str) -> WorkspaceRecord:
        try:
            return self._items[str(workspace_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown workspace: {workspace_id}") from exc

    def set_current(self, workspace_id: str) -> WorkspaceRecord:
        record = self.get(workspace_id)
        self.current_workspace_id = record.id
        return record

    def current(self) -> WorkspaceRecord | None:
        return self._items.get(self.current_workspace_id) if self.current_workspace_id else None

    def list(self, *, project_id: str = "") -> list[WorkspaceRecord]:
        values = list(self._items.values())
        if project_id:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    def export_records(self) -> list[dict[str, Any]]:
        return [record.public_dict() for record in self.list()]

    @classmethod
    def from_records(
        cls,
        values: Any,
        *,
        current_workspace_id: str = "",
        limit: int = 200,
    ) -> "WorkspaceRegistry":
        registry = cls(limit=limit)
        if not isinstance(values, list):
            return registry
        for value in values[-limit:]:
            if not isinstance(value, Mapping):
                continue
            try:
                record = WorkspaceRecord(
                    id=str(value.get("id") or ""),
                    project_id=str(value.get("project_id") or ""),
                    provider_id=str(value.get("provider_id") or ""),
                    kind=str(value.get("kind") or "interactive"),
                    state=str(value.get("state") or "unknown"),
                    display_name=str(value.get("display_name") or ""),
                    created_at=str(value.get("created_at") or utc_now()),
                    updated_at=str(value.get("updated_at") or utc_now()),
                    job_ids=list(value.get("job_ids") or []),
                    endpoint_ids=list(value.get("endpoint_ids") or []),
                    artifact_ids=list(value.get("artifact_ids") or []),
                    metadata=dict(value.get("metadata") or {}),
                )
                registry.upsert(record)
            except (TypeError, ValueError):
                continue
        if current_workspace_id in registry._items:
            registry.current_workspace_id = current_workspace_id
        return registry
