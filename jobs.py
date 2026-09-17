"""ARC Slurm job contracts and documented SSH-backed gateway."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import os
import re
import shlex
import shutil
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


ACCOUNT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PARTITION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
QOS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
WALLTIME_RE = re.compile(r"^(?:\d+-)?\d{1,2}:\d{2}:\d{2}$")
JOB_ID_RE = re.compile(r"^\d{1,20}$")
NODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
LOGIN_HOSTS = {"falcon1.arc.vt.edu", "falcon2.arc.vt.edu"}


@dataclass(frozen=True)
class JobSpec:
    account: str
    command: str
    partition: str = "l40s_normal_q"
    walltime: str = "1-00:00:00"
    nodes: int = 1
    ntasks_per_node: int = 1
    cpus_per_task: int = 8
    gpus: int = 0
    gpu_type: str = "l40s"
    memory_gb: int | None = None
    qos: str = ""
    output: str = "arc-chat-%j.log"
    name: str = "arc-chat"

    def __post_init__(self) -> None:
        if not ACCOUNT_RE.fullmatch(self.account):
            raise ValueError("Slurm account must be a simple ARC account name.")
        if not PARTITION_RE.fullmatch(self.partition):
            raise ValueError("Invalid Slurm partition.")
        if self.qos and not QOS_RE.fullmatch(self.qos):
            raise ValueError("Invalid Slurm QoS.")
        if not WALLTIME_RE.fullmatch(self.walltime):
            raise ValueError("Walltime must look like HH:MM:SS or D-HH:MM:SS.")
        if not (1 <= self.nodes <= 32 and 1 <= self.ntasks_per_node <= 1024 and 1 <= self.cpus_per_task <= 1024):
            raise ValueError("Requested Slurm CPU/task shape is outside ARC Chat safety limits.")
        if not (0 <= self.gpus <= 32):
            raise ValueError("GPU count is outside ARC Chat safety limits.")
        if self.gpus and not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", self.gpu_type):
            raise ValueError("Invalid GPU type.")
        if self.memory_gb is not None and not (1 <= self.memory_gb <= 4096):
            raise ValueError("Memory request is outside ARC Chat safety limits.")
        if not self.command.strip() or "\x00" in self.command:
            raise ValueError("Job command cannot be empty or contain NUL bytes.")
        if not re.fullmatch(r"[A-Za-z0-9_.%/-]{1,160}", self.output):
            raise ValueError("Invalid Slurm output filename.")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", self.name):
            raise ValueError("Invalid Slurm job name.")

    def script(self) -> str:
        lines = [
            "#!/bin/bash",
            f"#SBATCH --job-name={self.name}",
            f"#SBATCH --account={self.account}",
            f"#SBATCH --partition={self.partition}",
            f"#SBATCH --time={self.walltime}",
            f"#SBATCH --nodes={self.nodes}",
            f"#SBATCH --ntasks-per-node={self.ntasks_per_node}",
            f"#SBATCH --cpus-per-task={self.cpus_per_task}",
        ]
        if self.gpus:
            lines.append(f"#SBATCH --gres=gpu:{self.gpu_type}:{self.gpus}")
        if self.memory_gb is not None:
            lines.append(f"#SBATCH --mem={self.memory_gb}G")
        if self.qos:
            lines.append(f"#SBATCH --qos={self.qos}")
        lines.extend([f"#SBATCH --output={self.output}", "", "set -euo pipefail", self.command.strip(), ""])
        return "\n".join(lines)

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceProfile:
    id: str
    label: str
    partition: str
    cpus_per_task: int
    gpus: int = 0
    gpu_type: str = "l40s"
    walltime: str = "01:00:00"
    memory_gb: int | None = None
    qos: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


RESOURCE_PROFILES: dict[str, ResourceProfile] = {
    "falcon-l40s-small": ResourceProfile(
        id="falcon-l40s-small", label="Falcon L40S small", partition="l40s_normal_q",
        cpus_per_task=8, gpus=1, gpu_type="l40s", walltime="01:00:00", qos="fal_l40s_normal_base",
    ),
    "falcon-a30-small": ResourceProfile(
        id="falcon-a30-small", label="Falcon A30 small", partition="a30_normal_q",
        cpus_per_task=8, gpus=1, gpu_type="a30", walltime="01:00:00", qos="fal_a30_normal_base",
    ),
    "falcon-v100-small": ResourceProfile(
        id="falcon-v100-small", label="Falcon V100 small", partition="v100_normal_q",
        cpus_per_task=6, gpus=1, gpu_type="v100", walltime="01:00:00", qos="fal_v100_normal_base",
    ),
    "falcon-t4-small": ResourceProfile(
        id="falcon-t4-small", label="Falcon T4 small", partition="t4_normal_q",
        cpus_per_task=6, gpus=1, gpu_type="t4", walltime="01:00:00", qos="fal_t4_normal_base",
    ),
    "falcon-l40s-vllm": ResourceProfile(
        id="falcon-l40s-vllm", label="Falcon L40S vLLM", partition="l40s_normal_q",
        cpus_per_task=32, gpus=2, gpu_type="l40s", walltime="1-00:00:00", qos="fal_l40s_normal_base",
    ),
}


def get_resource_profile(profile_id: str) -> ResourceProfile:
    try:
        return RESOURCE_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown ARC resource profile: {profile_id}") from exc


@dataclass
class JobRecord:
    job_id: str
    kind: str
    name: str
    submitted_at: str
    state: str = "SUBMITTED"
    node: str = ""
    reason: str = ""
    resources: dict[str, Any] = field(default_factory=dict)
    command_sha256: str = ""
    artifact_ids: list[str] = field(default_factory=list)

    @classmethod
    def submitted(cls, job_id: str, spec: JobSpec, *, kind: str = "slurm") -> "JobRecord":
        _job_id(job_id)
        public = spec.public_dict()
        command = str(public.pop("command", ""))
        return cls(
            job_id=job_id,
            kind=kind,
            name=spec.name,
            submitted_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            resources=public,
            command_sha256=hashlib.sha256(command.encode("utf-8")).hexdigest(),
        )

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobHistory:
    """Bounded non-secret scheduler provenance suitable for local recovery."""

    def __init__(self, records: list[JobRecord] | None = None, *, limit: int = 100):
        self.limit = max(1, min(1000, int(limit)))
        self._records: dict[str, JobRecord] = {}
        for record in records or []:
            self.add(record)

    def add(self, record: JobRecord) -> JobRecord:
        _job_id(record.job_id)
        self._records[record.job_id] = record
        while len(self._records) > self.limit:
            self._records.pop(next(iter(self._records)))
        return record

    def record_submission(self, job_id: str, spec: JobSpec, *, kind: str = "slurm") -> JobRecord:
        return self.add(JobRecord.submitted(job_id, spec, kind=kind))

    def update(self, job_id: str, *, state: str, node: str = "", reason: str = "") -> JobRecord:
        job_id = _job_id(job_id)
        record = self._records.get(job_id)
        if record is None:
            record = JobRecord(job_id=job_id, kind="unknown", name="unknown", submitted_at="")
            self.add(record)
        record.state = str(state)[:64]
        record.node = str(node)[:128]
        record.reason = str(reason)[:500]
        return record

    def link_artifact(self, job_id: str, artifact_id: str) -> JobRecord:
        record = self._records.get(_job_id(job_id))
        if record is None:
            raise KeyError(f"Unknown job: {job_id}")
        if artifact_id not in record.artifact_ids:
            record.artifact_ids.append(artifact_id)
        return record

    def list(self) -> list[JobRecord]:
        return list(self._records.values())

    def export_records(self) -> list[dict[str, Any]]:
        return [record.public_dict() for record in self.list()]

    @classmethod
    def from_records(cls, values: Any, *, limit: int = 100) -> "JobHistory":
        records: list[JobRecord] = []
        if not isinstance(values, list):
            return cls(limit=limit)
        allowed = {field.name for field in __import__("dataclasses").fields(JobRecord)}
        for value in values[-limit:]:
            if not isinstance(value, dict):
                continue
            try:
                filtered = {key: value[key] for key in allowed if key in value}
                record = JobRecord(**filtered)
                _job_id(record.job_id)
            except (TypeError, ValueError):
                continue
            records.append(record)
        return cls(records, limit=limit)


class CommandGateway(Protocol):
    async def run(self, command: str, *, stdin: str = "", timeout: float = 30.0) -> str: ...


class SshCommandGateway:
    """Execute lightweight scheduler commands on an ARC login node.

    ARC documents SSH access to Falcon login nodes and recommends SSH keys for
    a reliable workflow. BatchMode deliberately refuses password/Duo prompts so
    the packaged helper cannot hang invisibly waiting for credentials.
    """

    def __init__(self, username: str, host: str = "falcon2.arc.vt.edu"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username or ""):
            raise ValueError("Invalid ARC username.")
        if host not in LOGIN_HOSTS:
            raise ValueError("ARC Chat currently supports documented Falcon login hosts only.")
        self.username = username
        self.host = host

    async def run(self, command: str, *, stdin: str = "", timeout: float = 30.0) -> str:
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("OpenSSH client is not installed or not on PATH.")
        process = await asyncio.create_subprocess_exec(
            ssh,
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=12",
            f"{self.username}@{self.host}",
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(stdin.encode()), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("ARC SSH command timed out. Check VPN and SSH-key authentication.")
        if process.returncode:
            detail = stderr.decode(errors="replace").strip()[-1200:]
            raise RuntimeError("ARC SSH command failed. Check VPN, host-key trust, and SSH-key authentication. " + detail)
        return stdout.decode(errors="replace").strip()


class JobBackend(Protocol):
    async def submit(self, spec: JobSpec) -> str: ...
    async def status(self, job_id: str) -> dict[str, str]: ...
    async def logs(self, job_id: str, lines: int = 200) -> str: ...
    async def cancel(self, job_id: str) -> None: ...
    async def list_active(self) -> list[dict[str, str]]: ...


def _job_id(value: str) -> str:
    if not JOB_ID_RE.fullmatch(str(value)):
        raise ValueError("Invalid Slurm job id.")
    return str(value)


class SlurmBackend:
    def __init__(self, gateway: CommandGateway):
        self.gateway = gateway

    async def submit(self, spec: JobSpec) -> str:
        result = await self.gateway.run("sbatch --parsable", stdin=spec.script(), timeout=45)
        job_id = result.split(";", 1)[0].strip()
        return _job_id(job_id)

    async def status(self, job_id: str) -> dict[str, str]:
        job_id = _job_id(job_id)
        text = await self.gateway.run(f"squeue -h -j {job_id} -o '%i|%T|%N|%R'", timeout=20)
        if not text:
            return {"job_id": job_id, "state": "NOT_IN_QUEUE", "node": "", "reason": ""}
        job, state, node, reason = (text.splitlines()[0].split("|", 3) + ["", "", "", ""])[:4]
        return {"job_id": job, "state": state, "node": node if node != "(null)" else "", "reason": reason}

    async def list_active(self) -> list[dict[str, str]]:
        text = await self.gateway.run("squeue -h -u \"$USER\" -o '%i|%T|%N|%R|%j'", timeout=20)
        result = []
        for line in text.splitlines():
            parts = (line.split("|", 4) + ["", "", "", "", ""])[:5]
            result.append(dict(zip(("job_id", "state", "node", "reason", "name"), parts)))
        return result

    async def cancel(self, job_id: str) -> None:
        await self.gateway.run(f"scancel {_job_id(job_id)}", timeout=20)

    async def logs(self, job_id: str, lines: int = 200) -> str:
        job_id = _job_id(job_id)
        lines = min(2000, max(1, int(lines)))
        details = await self.gateway.run(f"scontrol show job -o {job_id}", timeout=20)
        match = re.search(r"(?:^|\s)StdOut=(\S+)", details)
        if not match or match.group(1) in {"(null)", ""}:
            raise RuntimeError("Slurm did not report a stdout path for this job.")
        path = match.group(1)
        # scontrol supplies this path; shell-quote before sending it back.
        return await self.gateway.run(f"tail -n {lines} -- {shlex.quote(path)}", timeout=20)

