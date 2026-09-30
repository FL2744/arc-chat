"""Structured ARC workload specification (the basis for submission and reproducibility)."""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from jobs import JobSpec
from naming import validate_job_name
from safety import classify_command
from security import validate_public_metadata

SCHEMA_VERSION = 1


@dataclass
class WorkloadSpec:
    project: str = ""
    job_name: str = ""
    allocation: str = ""
    partition: str = ""
    qos: str = ""
    nodes: int = 1
    cpus: int = 1
    memory_gb: int | None = None
    gpus: int = 0
    gpu_type: str = ""
    walltime: str = "01:00:00"
    modules: list[str] = field(default_factory=list)
    environment: str = ""          # e.g. a venv/conda environment name
    container: str = ""
    working_directory: str = ""
    command: str = ""
    application: str = ""          # e.g. "vllm", "jupyter", "batch"
    model: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    input_files: list[str] = field(default_factory=list)
    output_directory: str = ""

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty when submittable)."""
        problems: list[str] = []
        try:
            validate_job_name(self.job_name)
        except ValueError as exc:
            problems.append(str(exc))
        if not self.allocation:
            problems.append("Select an ARC allocation (Slurm account).")
        if not self.partition:
            problems.append("Select a partition/resource class.")
        if not self.command.strip():
            problems.append("Enter the command the job should run.")
        if self.gpus and not self.gpu_type:
            problems.append("GPU jobs need a GPU type.")
        for key in self.env_vars:
            try:
                validate_public_metadata({key: ""}, label="environment variable")
            except ValueError:
                problems.append(f"Environment variable '{key}' looks like a credential; "
                                "secrets are not stored in workload specs.")
        return problems

    def to_job_spec(self) -> JobSpec:
        """Convert to the submission contract; raises ValueError if invalid."""
        problems = self.validate()
        if problems:
            raise ValueError(" ".join(problems))
        lines = [f"module load {shlex.quote(m)}" for m in self.modules]
        if self.working_directory:
            lines.append(f"cd {shlex.quote(self.working_directory)}")
        lines += [f"export {k}={shlex.quote(v)}" for k, v in sorted(self.env_vars.items())]
        lines.append(self.command)
        return JobSpec(
            account=self.allocation, command="\n".join(lines), partition=self.partition,
            walltime=self.walltime, nodes=self.nodes, cpus_per_task=self.cpus, gpus=self.gpus,
            gpu_type=self.gpu_type or "l40s", memory_gb=self.memory_gb, qos=self.qos, name=self.job_name,
        )

    def login_node_safe(self) -> bool:
        return classify_command(self.command).allowed_on_login_node

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    def export_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkloadSpec":
        known = {f.name for f in fields(cls)}
        version = data.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported workload schema version: {version}")
        return cls(**{k: v for k, v in data.items() if k in known})

    def duplicate(self, **changes: Any) -> "WorkloadSpec":
        data = self.to_dict()
        data.update(changes)
        return WorkloadSpec.from_dict(data)
