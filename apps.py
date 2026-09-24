"""Application manifests and provider-neutral deployment planning."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from providers import PlacementDecision, PlacementRequest


APP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
APP_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ApplicationManifest:
    id: str
    name: str
    project_id: str
    application_type: str = "interactive"
    runtime: str = "python"
    provider: str = "auto"
    audience: str = "private"
    entrypoint: str = ""
    requires_gpu: bool = False
    requires_server_packages: bool = False
    persistent_service: bool = False
    estimated_input_mb: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not APP_ID_RE.fullmatch(self.id):
            raise ValueError("Invalid application id.")
        if not self.name.strip() or len(self.name) > 160:
            raise ValueError("Application name must be 1-160 characters.")
        if not APP_ID_RE.fullmatch(self.project_id):
            raise ValueError("Invalid project id.")
        if self.application_type not in {"browser", "interactive", "batch", "service", "static"}:
            raise ValueError("Unsupported application type.")
        if self.audience not in {"private", "course", "vt", "public"}:
            raise ValueError("Unsupported application audience.")
        if self.provider not in {"auto", "browser", "arc", "common-platform", "cloud"}:
            raise ValueError("Unsupported application provider.")
        if self.estimated_input_mb is not None and self.estimated_input_mb < 0:
            raise ValueError("estimated_input_mb cannot be negative.")
        if "\x00" in self.entrypoint or len(self.entrypoint) > 500:
            raise ValueError("Invalid application entrypoint.")
        if not isinstance(self.metadata, dict):
            raise ValueError("Application metadata must be an object.")
        safe_metadata: dict[str, Any] = {}
        for key, value in self.metadata.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise ValueError("Application metadata keys must be short strings.")
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError("Application metadata values must be scalar JSON values.")
            if isinstance(value, str) and len(value) > 1000:
                raise ValueError("Application metadata text is too long.")
            safe_metadata[key] = value
        object.__setattr__(self, "metadata", safe_metadata)

    def placement_request(self) -> PlacementRequest:
        mode = "browser" if self.application_type == "static" else self.application_type
        return PlacementRequest(
            mode=mode,
            preferred_provider=self.provider,
            needs_gpu=self.requires_gpu,
            requires_server_packages=self.requires_server_packages,
            persistent_service=self.persistent_service or self.application_type == "service",
            estimated_input_mb=self.estimated_input_mb,
        )

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeploymentPlan:
    application_id: str
    project_id: str
    provider_id: str
    action: str
    reason: str
    confidence: str
    requires_review: bool

    @classmethod
    def from_decision(cls, manifest: ApplicationManifest, decision: PlacementDecision) -> "DeploymentPlan":
        return cls(
            application_id=manifest.id,
            project_id=manifest.project_id,
            provider_id=decision.provider_id,
            action=decision.action,
            reason=decision.reason,
            confidence=decision.confidence,
            requires_review=decision.requires_review,
        )

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApplicationRegistry:
    def __init__(self, applications: list[ApplicationManifest] | None = None):
        self._items: dict[str, ApplicationManifest] = {}
        for application in applications or []:
            self.register(application)

    def register(self, application: ApplicationManifest) -> ApplicationManifest:
        self._items[application.id] = application
        return application

    def get(self, application_id: str) -> ApplicationManifest:
        try:
            return self._items[str(application_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown application: {application_id}") from exc

    def list(self, *, project_id: str = "") -> list[ApplicationManifest]:
        values = list(self._items.values())
        if project_id:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: (item.project_id, item.name.lower(), item.id))

    def public_dicts(self, *, project_id: str = "") -> list[dict[str, Any]]:
        return [item.public_dict() for item in self.list(project_id=project_id)]


def _manifest_from_mapping(value: Mapping[str, Any]) -> ApplicationManifest:
    allowed = {
        "id", "name", "project_id", "application_type", "runtime", "provider",
        "audience", "entrypoint", "requires_gpu", "requires_server_packages",
        "persistent_service", "estimated_input_mb", "metadata",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("Unknown application fields: " + ", ".join(sorted(unknown)))
    return ApplicationManifest(**{key: value[key] for key in allowed if key in value})


def load_applications(path: str | os.PathLike[str] | None = None) -> ApplicationRegistry:
    selected = path or os.environ.get("ARC_CHAT_APP_FILE")
    if not selected:
        return ApplicationRegistry()
    app_path = Path(selected).expanduser()
    with app_path.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, Mapping):
        raise ValueError("Application configuration must be an object.")
    version = raw.get("version", APP_SCHEMA_VERSION)
    if version != APP_SCHEMA_VERSION:
        raise ValueError(f"Unsupported application schema version {version!r}; expected {APP_SCHEMA_VERSION}.")
    values = raw.get("applications")
    if not isinstance(values, list):
        raise ValueError("Application configuration must contain an applications list.")
    manifests: list[ApplicationManifest] = []
    for item in values:
        if not isinstance(item, Mapping):
            raise ValueError("Each application must be an object.")
        manifests.append(_manifest_from_mapping(item))
    return ApplicationRegistry(manifests)
