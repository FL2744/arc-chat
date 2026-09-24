"""Provider-neutral workspace records for ARC Chat's control plane."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from security import validate_public_metadata


WORKSPACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
class WorkspaceState(str, Enum):
    NEW = "new"
    PLANNING = "planning"
    STARTING = "starting"
    QUEUED = "queued"
    READY = "ready"
    BUSY = "busy"
    INPUT_REQUIRED = "input_required"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"  # Read-only compatibility state for older recovery files.


WORKSPACE_STATES = {item.value for item in WorkspaceState}
WORKSPACE_KINDS = {"browser", "interactive", "batch", "service"}
WORKSPACE_TRANSITIONS = {
    WorkspaceState.NEW: {WorkspaceState.PLANNING, WorkspaceState.STARTING, WorkspaceState.STOPPED, WorkspaceState.FAILED},
    WorkspaceState.PLANNING: {WorkspaceState.STARTING, WorkspaceState.STOPPING, WorkspaceState.STOPPED, WorkspaceState.FAILED},
    WorkspaceState.STARTING: {WorkspaceState.QUEUED, WorkspaceState.READY, WorkspaceState.DEGRADED, WorkspaceState.STOPPING, WorkspaceState.FAILED},
    WorkspaceState.QUEUED: {WorkspaceState.STARTING, WorkspaceState.READY, WorkspaceState.DEGRADED, WorkspaceState.STOPPING, WorkspaceState.FAILED},
    WorkspaceState.READY: {WorkspaceState.BUSY, WorkspaceState.INPUT_REQUIRED, WorkspaceState.DEGRADED, WorkspaceState.RECOVERING, WorkspaceState.STOPPING, WorkspaceState.STOPPED, WorkspaceState.FAILED},
    WorkspaceState.BUSY: {WorkspaceState.READY, WorkspaceState.INPUT_REQUIRED, WorkspaceState.DEGRADED, WorkspaceState.RECOVERING, WorkspaceState.STOPPING, WorkspaceState.FAILED},
    WorkspaceState.INPUT_REQUIRED: {WorkspaceState.BUSY, WorkspaceState.READY, WorkspaceState.DEGRADED, WorkspaceState.RECOVERING, WorkspaceState.STOPPING, WorkspaceState.FAILED},
    WorkspaceState.DEGRADED: {WorkspaceState.READY, WorkspaceState.RECOVERING, WorkspaceState.STOPPING, WorkspaceState.STOPPED, WorkspaceState.FAILED},
    WorkspaceState.RECOVERING: {WorkspaceState.READY, WorkspaceState.BUSY, WorkspaceState.INPUT_REQUIRED, WorkspaceState.DEGRADED, WorkspaceState.STOPPING, WorkspaceState.FAILED},
    WorkspaceState.STOPPING: {WorkspaceState.STOPPED, WorkspaceState.DEGRADED, WorkspaceState.FAILED},
    WorkspaceState.STOPPED: {WorkspaceState.PLANNING, WorkspaceState.STARTING},
    WorkspaceState.FAILED: set(),
    WorkspaceState.UNKNOWN: {WorkspaceState.PLANNING, WorkspaceState.STARTING, WorkspaceState.STOPPED, WorkspaceState.FAILED},
}
_PROVIDER_STATES = {
    "arc": {"PENDING": WorkspaceState.QUEUED, "CONFIGURING": WorkspaceState.QUEUED,
            "RUNNING": WorkspaceState.STARTING, "COMPLETING": WorkspaceState.STOPPING,
            "COMPLETED": WorkspaceState.STOPPED, "CANCELLED": WorkspaceState.STOPPED,
            "CANCELED": WorkspaceState.STOPPED, "FAILED": WorkspaceState.FAILED,
            "TIMEOUT": WorkspaceState.FAILED, "PREEMPTED": WorkspaceState.FAILED},
    "jupyter": {"IDLE": WorkspaceState.READY, "BUSY": WorkspaceState.BUSY,
                "STARTING": WorkspaceState.STARTING, "RESTARTING": WorkspaceState.RECOVERING,
                "DEAD": WorkspaceState.FAILED},
    "browser": {"READY": WorkspaceState.READY, "BUSY": WorkspaceState.BUSY},
    "common-platform": {"PENDING": WorkspaceState.QUEUED, "PROGRESSING": WorkspaceState.STARTING,
                         "READY": WorkspaceState.READY, "AVAILABLE": WorkspaceState.READY,
                         "DEGRADED": WorkspaceState.DEGRADED, "FAILED": WorkspaceState.FAILED,
                         "DELETING": WorkspaceState.STOPPING, "DELETED": WorkspaceState.STOPPED},
}


def map_provider_state(provider_id: str, provider_state: str) -> WorkspaceState:
    """Map a provider-specific state into the common workspace lifecycle."""
    provider = str(provider_id or "").strip().lower()
    raw = str(provider_state or "").strip().upper().replace(" ", "_")
    mapped = _PROVIDER_STATES.get(provider, {}).get(raw)
    if mapped is not None:
        return mapped
    if raw in {"SUBMITTED", "PENDING", "QUEUED", "CONFIGURING"}:
        return WorkspaceState.QUEUED
    if raw in {"STARTING", "PROVISIONING", "RUNNING"}:
        return WorkspaceState.STARTING
    if raw in {"STOPPED", "CANCELLED", "CANCELED", "COMPLETED", "DELETED"}:
        return WorkspaceState.STOPPED
    if raw in {"FAILED", "ERROR", "DEAD", "TIMEOUT"}:
        return WorkspaceState.FAILED
    return WorkspaceState.DEGRADED


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
    last_seen_at: str = ""
    owner_id: str = ""
    provider_state: str = ""
    job_ids: list[str] = field(default_factory=list)
    provider_resource_ids: list[str] = field(default_factory=list)
    endpoint_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    deployment_ids: list[str] = field(default_factory=list)
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
        if self.owner_id and not WORKSPACE_ID_RE.fullmatch(self.owner_id):
            raise ValueError("Invalid workspace owner id.")
        for values in (self.job_ids, self.provider_resource_ids, self.endpoint_ids, self.artifact_ids, self.deployment_ids):
            if any(not WORKSPACE_ID_RE.fullmatch(str(value)) for value in values):
                raise ValueError("Invalid linked workspace resource id.")
        self.metadata = validate_public_metadata(self.metadata, label="Workspace metadata")

    def transition(self, state: str) -> None:
        state_value = state.value if isinstance(state, WorkspaceState) else str(state or "").strip().lower()
        try:
            target = WorkspaceState(state_value)
        except ValueError as exc:
            raise ValueError("Unsupported workspace state.") from exc
        current = WorkspaceState(self.state)
        if target != current and target not in WORKSPACE_TRANSITIONS[current]:
            raise ValueError(f"Invalid workspace transition: {current.value} -> {target.value}.")
        self.state = target.value
        self.updated_at = utc_now()

    def observe_provider_state(self, provider_state: str) -> WorkspaceState:
        """Record raw provider status and safely update the generic state."""
        target = map_provider_state(self.provider_id, provider_state)
        self.provider_state = str(provider_state or "")[:80]
        self.transition(target.value)
        self.seen()
        return target

    def seen(self) -> None:
        now = utc_now()
        self.last_seen_at = now
        self.updated_at = now

    def link(self, kind: str, resource_id: str) -> None:
        if kind not in {"job", "provider_resource", "endpoint", "artifact", "deployment"}:
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
        owner_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceRecord:
        if kind not in WORKSPACE_KINDS:
            raise ValueError("Unsupported workspace kind.")
        if state not in WORKSPACE_STATES:
            raise ValueError("Unsupported workspace state.")
        if owner_id and not WORKSPACE_ID_RE.fullmatch(owner_id):
            raise ValueError("Invalid workspace owner id.")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Workspace metadata must be an object.")
        existing = self._items.get(workspace_id)
        if existing:
            if existing.project_id != project_id or existing.provider_id != provider_id:
                raise ValueError("Workspace identity cannot be reassigned to another project/provider.")
            if owner_id and existing.owner_id and existing.owner_id != owner_id:
                raise ValueError("Workspace ownership cannot be reassigned.")
            existing.kind = kind
            if owner_id:
                existing.owner_id = owner_id
            existing.display_name = display_name or existing.display_name
            if metadata:
                existing.metadata.update(metadata)
                existing.__post_init__()
            if existing.state != state:
                existing.transition(state)
            existing.seen()
            return existing
        return self.upsert(WorkspaceRecord(
            id=workspace_id,
            project_id=project_id,
            provider_id=provider_id,
            kind=kind,
            state=state,
            display_name=display_name,
            last_seen_at=utc_now(),
            owner_id=owner_id,
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

    def expire_stale(
        self,
        *,
        stale_after: dt.timedelta,
        terminal_after: dt.timedelta,
        now: dt.datetime | None = None,
    ) -> list[str]:
        """Degrade stale live records and purge expired terminal metadata.

        Provider resources are never stopped here. This only ages local control
        plane records; provider cleanup must be performed by an authorized
        provider adapter and recorded separately.
        """
        current = now or dt.datetime.now(dt.timezone.utc)
        if stale_after.total_seconds() < 0 or terminal_after.total_seconds() < 0:
            raise ValueError("Workspace retention intervals cannot be negative.")
        removed: list[str] = []
        terminal = {WorkspaceState.STOPPED.value, WorkspaceState.FAILED.value}
        for record in list(self._items.values()):
            timestamp = record.last_seen_at or record.updated_at or record.created_at
            try:
                seen = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                seen = current
            age = current - seen.astimezone(dt.timezone.utc)
            if record.state in terminal and age >= terminal_after:
                self._items.pop(record.id, None)
                removed.append(record.id)
            elif record.state not in terminal | {WorkspaceState.UNKNOWN.value} and age >= stale_after:
                record.transition(WorkspaceState.DEGRADED.value)
        if self.current_workspace_id in removed:
            self.current_workspace_id = ""
        return removed

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
                    last_seen_at=str(value.get("last_seen_at") or ""),
                    owner_id=str(value.get("owner_id") or ""),
                    provider_state=str(value.get("provider_state") or ""),
                    job_ids=list(value.get("job_ids") or []),
                    provider_resource_ids=list(value.get("provider_resource_ids") or []),
                    endpoint_ids=list(value.get("endpoint_ids") or []),
                    artifact_ids=list(value.get("artifact_ids") or []),
                    deployment_ids=list(value.get("deployment_ids") or []),
                    metadata=dict(value.get("metadata") or {}),
                )
                registry.upsert(record)
            except (TypeError, ValueError):
                continue
        if current_workspace_id in registry._items:
            registry.current_workspace_id = current_workspace_id
        return registry
