"""Persistent run records for reproducibility (duplicate, rerun, export)."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from identifiers import new_id
from workload import WorkloadSpec


@dataclass
class RunRecord:
    run_id: str
    project: str
    spec: dict[str, Any]
    submitted_at: str
    job_id: str = ""
    status: str = "SUBMITTED"
    node: str = ""
    commands: list[str] = field(default_factory=list)
    output_locations: list[str] = field(default_factory=list)
    log_references: list[str] = field(default_factory=list)
    parent_run_id: str = ""

    def workload(self) -> WorkloadSpec:
        return WorkloadSpec.from_dict(self.spec)


class RunHistory:
    """JSON-file backed run registry (written atomically, non-secret data only)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self._runs: dict[str, RunRecord] = {}
        if self.path and self.path.exists():
            for item in json.loads(self.path.read_text(encoding="utf-8")).get("runs", []):
                record = RunRecord(**item)
                self._runs[record.run_id] = record

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "runs": [asdict(r) for r in self._runs.values()]}, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, self.path)

    def record_submission(self, spec: WorkloadSpec, job_id: str, *, parent_run_id: str = "") -> RunRecord:
        record = RunRecord(
            run_id=new_id("run"), project=spec.project, spec=spec.to_dict(), job_id=job_id,
            submitted_at=dt.datetime.now(dt.timezone.utc).isoformat(), parent_run_id=parent_run_id,
            commands=[spec.command],
        )
        self._runs[record.run_id] = record
        self._save()
        return record

    def update(self, run_id: str, **changes: Any) -> RunRecord:
        record = self.get(run_id)
        for key, value in changes.items():
            if not hasattr(record, key) or key in {"run_id", "spec"}:
                raise ValueError(f"Cannot update run field: {key}")
            setattr(record, key, value)
        self._save()
        return record

    def get(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError:
            raise ValueError(f"Unknown run: {run_id}") from None

    def list(self, project: str | None = None) -> list[RunRecord]:
        runs = sorted(self._runs.values(), key=lambda r: r.submitted_at, reverse=True)
        return [r for r in runs if project is None or r.project == project]

    def duplicate_spec(self, run_id: str, **changes: Any) -> WorkloadSpec:
        """Return an editable copy of a run's spec (use for duplicate / modify-and-rerun)."""
        return self.get(run_id).workload().duplicate(**changes)

    def export_manifest(self, run_id: str) -> str:
        record = self.get(run_id)
        return json.dumps(asdict(record), indent=2, sort_keys=True)
