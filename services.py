"""Managed ARC model-service contracts built on the Slurm job layer."""

from __future__ import annotations

import asyncio
import re
import secrets
import shutil
from dataclasses import asdict, dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from identifiers import new_id
from jobs import JobSpec, SlurmBackend, NODE_RE
from security import validate_public_metadata


MODEL_PATH_RE = re.compile(r"^/common/data/models/[A-Za-z0-9_.+/-]{1,300}$")
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,160}$")
PARSER_RE = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
API_KEY_RE = re.compile(r"^[A-Za-z0-9_.~+/=-]{16,256}$")


@dataclass(frozen=True)
class EndpointRecord:
    id: str
    provider: str
    model: str
    endpoint: str
    reachability: str
    state: str = "available"
    job_id: str = ""
    metadata: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["metadata"] = dict(self.metadata or {})
        return value


class EndpointRegistry:
    """In-memory, non-secret registry of model/service endpoints."""

    def __init__(self):
        self._items: dict[str, EndpointRecord] = {}

    def upsert(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        reachability: str,
        state: str = "available",
        job_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EndpointRecord:
        if not re.fullmatch(r"[a-z_]{2,40}", provider or ""):
            raise ValueError("Invalid endpoint provider id.")
        if reachability not in {"direct", "arc_session", "loopback_tunnel"}:
            raise ValueError("Invalid endpoint reachability.")
        if not MODEL_NAME_RE.fullmatch(model or ""):
            raise ValueError("Invalid endpoint model id.")
        if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 1000:
            raise ValueError("Invalid endpoint URL.")
        parsed = urlsplit(endpoint)
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Endpoint URLs cannot contain credentials, query strings, or fragments.")
        if reachability == "loopback_tunnel":
            if parsed.scheme != "http" or not loopback:
                raise ValueError("Loopback tunnel endpoints must use HTTP on a loopback address.")
        elif parsed.scheme != "https":
            raise ValueError("Non-loopback service endpoints must use HTTPS.")
        if reachability == "arc_session" and not (parsed.hostname or "").lower().endswith(".arc.vt.edu"):
            raise ValueError("ARC session endpoints must use an ARC HTTPS host.")
        metadata = validate_public_metadata(metadata or {}, label="Endpoint metadata")
        existing = next((item for item in self._items.values()
                         if item.provider == provider and item.model == model
                         and (item.job_id == str(job_id) if job_id else item.endpoint == endpoint)), None)
        record_id = existing.id if existing else new_id("endpoint")
        record = EndpointRecord(
            id=record_id,
            provider=provider,
            model=model,
            endpoint=endpoint,
            reachability=reachability,
            state=str(state)[:64],
            job_id=str(job_id)[:20],
            metadata=dict(metadata or {}),
        )
        self._items[record_id] = record
        return record

    def list(self) -> list[EndpointRecord]:
        return list(self._items.values())

    def remove_job(self, job_id: str) -> None:
        for key, item in list(self._items.items()):
            if item.job_id == str(job_id):
                del self._items[key]

    def export_records(self) -> list[dict[str, Any]]:
        return [item.public_dict() for item in self.list()]

    @classmethod
    def from_records(cls, values: Any, *, limit: int = 200) -> "EndpointRegistry":
        registry = cls()
        if not isinstance(values, list):
            return registry
        for value in values[-max(1, min(1000, int(limit))):]:
            if not isinstance(value, Mapping):
                continue
            try:
                record_id = str(value.get("id") or "")
                if not re.fullmatch(r"(?:ep_[0-9a-f]{32}|endpoint-[0-9a-f]{20})", record_id):
                    continue
                candidate = registry.upsert(
                    provider=str(value.get("provider") or ""),
                    model=str(value.get("model") or ""),
                    endpoint=str(value.get("endpoint") or ""),
                    reachability=str(value.get("reachability") or ""),
                    state=str(value.get("state") or "available"),
                    job_id=str(value.get("job_id") or ""),
                    metadata=dict(value.get("metadata") or {}),
                )
                if candidate.id != record_id:
                    registry._items.pop(candidate.id, None)
                    registry._items[record_id] = EndpointRecord(
                        id=record_id,
                        provider=candidate.provider,
                        model=candidate.model,
                        endpoint=candidate.endpoint,
                        reachability=candidate.reachability,
                        state=candidate.state,
                        job_id=candidate.job_id,
                        metadata=candidate.metadata,
                    )
            except (TypeError, ValueError):
                continue
        return registry


@dataclass(frozen=True)
class VllmServiceSpec:
    account: str
    model_path: str
    served_model_name: str
    gpus: int = 2
    cpus: int = 32
    max_model_len: int = 32768
    port: int = 8000
    walltime: str = "1-00:00:00"
    partition: str = "l40s_normal_q"
    gpu_type: str = "l40s"
    qos: str = "fal_l40s_normal_base"
    tool_call_parser: str = "openai"
    reasoning_parser: str = ""
    api_key: str = ""

    def __post_init__(self) -> None:
        if not MODEL_PATH_RE.fullmatch(self.model_path):
            raise ValueError("vLLM model must use an ARC /common/data/models path.")
        if not MODEL_NAME_RE.fullmatch(self.served_model_name):
            raise ValueError("Invalid served model name.")
        if not (1 <= self.gpus <= 8 and 1 <= self.cpus <= 256):
            raise ValueError("vLLM resource request is outside ARC Chat safety limits.")
        if not (1024 <= self.port <= 65535):
            raise ValueError("vLLM port must be between 1024 and 65535.")
        if not (1024 <= self.max_model_len <= 1_048_576):
            raise ValueError("Invalid vLLM context length.")
        if not PARSER_RE.fullmatch(self.tool_call_parser) or not PARSER_RE.fullmatch(self.reasoning_parser):
            raise ValueError("Invalid vLLM parser name.")
        if self.api_key and not API_KEY_RE.fullmatch(self.api_key):
            raise ValueError("Invalid vLLM API key format.")

    def resolved_api_key(self) -> str:
        return self.api_key or secrets.token_urlsafe(32)

    def job_spec(self, *, api_key: str | None = None) -> JobSpec:
        key = api_key or self.resolved_api_key()
        if not API_KEY_RE.fullmatch(key):
            raise ValueError("Invalid vLLM API key format.")
        args = [
            "vllm serve", self.model_path,
            "--served-model-name", self.served_model_name,
            "--tensor-parallel-size", str(self.gpus),
            "--max-model-len", str(self.max_model_len),
            "--port", str(self.port),
            "--api-key", key,
        ]
        if self.tool_call_parser:
            args.extend(["--enable-auto-tool-choice", "--tool-call-parser", self.tool_call_parser])
        if self.reasoning_parser:
            args.extend(["--reasoning-parser", self.reasoning_parser])
        # All values above are validated into shell-safe alphabets/numbers.
        command = "module load vLLM\ncd \"$TMPDIR\"\n" + " \\\n  ".join(args)
        return JobSpec(
            account=self.account,
            command=command,
            partition=self.partition,
            walltime=self.walltime,
            cpus_per_task=self.cpus,
            gpus=self.gpus,
            gpu_type=self.gpu_type,
            qos=self.qos,
            output=f"vllm-{self.served_model_name.replace('/', '-') }-%j.log",
            name="arc-chat-vllm",
        )


@dataclass
class ManagedService:
    job_id: str
    api_key: str
    model: str
    port: int
    node: str = ""
    state: str = "SUBMITTED"

    @property
    def remote_endpoint(self) -> str:
        if not self.node:
            return ""
        return f"http://{self.node}:{self.port}/v1"

    def local_endpoint(self, local_port: int | None = None) -> str:
        return f"http://127.0.0.1:{local_port or self.port}/v1"


class VllmServiceManager:
    def __init__(self, jobs: SlurmBackend):
        self.jobs = jobs

    async def start(self, spec: VllmServiceSpec, *, api_key: str | None = None) -> ManagedService:
        key = api_key or spec.resolved_api_key()
        job_id = await self.jobs.submit(spec.job_spec(api_key=key))
        return ManagedService(job_id=job_id, api_key=key, model=spec.served_model_name, port=spec.port)

    async def refresh(self, service: ManagedService) -> ManagedService:
        status = await self.jobs.status(service.job_id)
        service.state = status["state"]
        node = status.get("node", "")
        if node and NODE_RE.fullmatch(node):
            service.node = node
        return service

    async def stop(self, service: ManagedService) -> None:
        await self.jobs.cancel(service.job_id)
        service.state = "CANCELLED"


class SshTunnel:
    """Managed local forwarding matching ARC's documented vLLM access pattern."""

    def __init__(self, username: str, node: str, remote_port: int, *, local_port: int | None = None, login_host: str = "falcon2.arc.vt.edu"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username or ""):
            raise ValueError("Invalid ARC username.")
        if not NODE_RE.fullmatch(node or ""):
            raise ValueError("Invalid ARC compute-node name.")
        if login_host not in {"falcon1.arc.vt.edu", "falcon2.arc.vt.edu"}:
            raise ValueError("Unsupported Falcon login host.")
        for value in (remote_port, local_port or remote_port):
            if not (1024 <= int(value) <= 65535):
                raise ValueError("Tunnel ports must be between 1024 and 65535.")
        self.username, self.node, self.remote_port = username, node, int(remote_port)
        self.local_port, self.login_host = int(local_port or remote_port), login_host
        self.process: asyncio.subprocess.Process | None = None

    def argv(self) -> list[str]:
        ssh = shutil.which("ssh") or "ssh"
        return [ssh, "-o", "BatchMode=yes", "-N", "-L", f"{self.local_port}:{self.node}:{self.remote_port}", f"{self.username}@{self.login_host}"]

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        if not shutil.which("ssh"):
            raise RuntimeError("OpenSSH client is not installed or not on PATH.")
        self.process = await asyncio.create_subprocess_exec(*self.argv(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        await asyncio.sleep(0.35)
        if self.process.returncode is not None:
            stderr = (await self.process.stderr.read()).decode(errors="replace")[-1200:]
            raise RuntimeError("ARC SSH tunnel could not start. Check VPN and SSH-key authentication. " + stderr)

    async def stop(self) -> None:
        if not self.process or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=3)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()

