"""First-class artifact/provenance contracts for ARC Chat outputs."""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable

from identifiers import new_id
from security import validate_public_metadata


ARTIFACT_ID_RE = re.compile(r"^(?:art_[0-9a-f]{32}|artifact-[0-9a-f]{16,32})$")
MEDIA_OVERRIDES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".ipynb": "application/x-ipynb+json",
}


def safe_workspace_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\\" in path or path.startswith("/"):
        raise ValueError("Artifact path must be a relative workspace path.")
    pure = PurePosixPath(path)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("Artifact path traversal is not allowed.")
    return pure.as_posix()


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    type: str
    path: str
    workspace: str
    created_by: str
    created_at: str
    media_type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not ARTIFACT_ID_RE.fullmatch(self.id):
            raise ValueError("Invalid artifact id.")
        safe_workspace_path(self.path)
        if not self.workspace or len(self.workspace) > 128:
            raise ValueError("Invalid artifact workspace id.")
        validate_public_metadata(self.metadata, label="Artifact metadata")

    @classmethod
    def create(
        cls,
        path: str,
        *,
        workspace: str,
        created_by: str,
        type: str = "file",
        media_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ArtifactRecord":
        safe = safe_workspace_path(path)
        return cls(
            id=new_id("artifact"),
            type=type,
            path=safe,
            workspace=workspace or "workspace-unknown",
            created_by=created_by or "unknown",
            created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            media_type=media_type or MEDIA_OVERRIDES.get(PurePosixPath(safe).suffix.lower()) or mimetypes.guess_type(safe)[0] or "application/octet-stream",
            metadata=validate_public_metadata(metadata or {}, label="Artifact metadata"),
        )


class ArtifactStore:
    def __init__(self):
        self._records: dict[str, ArtifactRecord] = {}

    def add(self, record: ArtifactRecord) -> ArtifactRecord:
        if not ARTIFACT_ID_RE.fullmatch(record.id):
            raise ValueError("Invalid artifact id.")
        self._records[record.id] = record
        return record

    def register(self, path: str, **kwargs: Any) -> ArtifactRecord:
        return self.add(ArtifactRecord.create(path, **kwargs))

    def get(self, artifact_id: str) -> ArtifactRecord:
        try:
            return self._records[artifact_id]
        except KeyError as exc:
            raise KeyError(f"Unknown artifact: {artifact_id}") from exc

    def list(self, *, workspace: str | None = None) -> list[ArtifactRecord]:
        values = self._records.values()
        return [item for item in values if workspace is None or item.workspace == workspace]

    def export_json(self) -> str:
        return json.dumps([asdict(item) for item in self._records.values()], indent=2, sort_keys=True)

    def export_records(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._records.values()]

    @classmethod
    def from_records(cls, values: Any, *, limit: int = 500) -> "ArtifactStore":
        store = cls()
        if not isinstance(values, list):
            return store
        allowed = {item.name for item in __import__("dataclasses").fields(ArtifactRecord)}
        for value in values[-max(1, min(5000, int(limit))):]:
            if not isinstance(value, dict):
                continue
            try:
                filtered = {key: value[key] for key in allowed if key in value}
                record = ArtifactRecord(**filtered)
                if not ARTIFACT_ID_RE.fullmatch(record.id):
                    continue
                safe_workspace_path(record.path)
                if not isinstance(record.metadata, dict):
                    continue
                validate_public_metadata(record.metadata, label="Artifact metadata")
                store.add(record)
            except (TypeError, ValueError):
                continue
        return store


@dataclass(frozen=True)
class PipelineStep:
    id: str
    kind: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    approval: str = "workspace_mutation"


class PipelineGraph:
    """Small validated DAG contract; execution remains explicitly approved."""

    def __init__(self, steps: Iterable[PipelineStep]):
        self.steps = tuple(steps)
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)) or any(not item for item in ids):
            raise ValueError("Pipeline step ids must be unique and non-empty.")
        valid = set(ids)
        for step in self.steps:
            unknown = set(step.inputs) - valid
            if unknown:
                raise ValueError(f"Pipeline step {step.id!r} references unknown inputs: {sorted(unknown)}")
        dependencies = {step.id: set(step.inputs) for step in self.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Pipeline graph contains a cycle.")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)

