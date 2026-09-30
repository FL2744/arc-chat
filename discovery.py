"""ARC resource discovery: lightweight, login-node-safe scheduler queries and parsers.

Only real scheduler output is parsed; nothing here invents availability. The
exact command set should be validated against current ARC documentation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Standard read-only Slurm queries (light on a login node).
SINFO_COMMAND = "sinfo -h -o '%P|%a|%D|%T|%G|%c|%m'"
SQUEUE_PENDING_COMMAND = "squeue -h -t PENDING -o '%P|%i'"
SQUEUE_MINE_COMMAND = "squeue -h -u \"$USER\" -o '%i|%T|%N|%R|%j|%P'"
ACCOUNTS_COMMAND = "sacctmgr -nP show assoc user=\"$USER\" format=account"

_GRES_GPU = re.compile(r"gpu(?::([A-Za-z0-9_.-]+))?:(\d+)")
_USABLE_STATES = {"idle", "mixed", "allocated"}


@dataclass(frozen=True)
class PartitionStatus:
    partition: str
    available: bool
    nodes: int
    state: str
    gpu_type: str
    gpus_per_node: int
    cpus_per_node: int
    memory_mb_per_node: int


def _int(value: str) -> int:
    match = re.match(r"\d+", value.strip())
    return int(match.group()) if match else 0


def parse_sinfo(text: str) -> list[PartitionStatus]:
    rows = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 7:
            continue
        name, avail, nodes, state, gres, cpus, mem = parts[:7]
        gpu = _GRES_GPU.search(gres)
        rows.append(PartitionStatus(
            partition=name.rstrip("*"), available=avail.strip().lower() == "up", nodes=_int(nodes),
            state=state.strip().lower(), gpu_type=(gpu.group(1) or "") if gpu else "",
            gpus_per_node=int(gpu.group(2)) if gpu else 0, cpus_per_node=_int(cpus),
            memory_mb_per_node=_int(mem),
        ))
    return rows


def summarize_partitions(rows: list[PartitionStatus], pending_by_partition: dict[str, int] | None = None) -> list[dict]:
    """Per-partition node counts by state, plus queue depth when supplied."""
    summary: dict[str, dict] = {}
    for row in rows:
        entry = summary.setdefault(row.partition, {
            "partition": row.partition, "up": row.available, "gpu_type": row.gpu_type,
            "gpus_per_node": row.gpus_per_node, "nodes_by_state": {}, "idle_nodes": 0,
        })
        entry["nodes_by_state"][row.state] = entry["nodes_by_state"].get(row.state, 0) + row.nodes
        if row.state == "idle":
            entry["idle_nodes"] += row.nodes
    for name, entry in summary.items():
        if pending_by_partition is not None:
            entry["pending_jobs"] = pending_by_partition.get(name, 0)
    return sorted(summary.values(), key=lambda e: e["partition"])


def parse_pending_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        part = line.split("|")[0].strip()
        if part:
            counts[part] = counts.get(part, 0) + 1
    return counts


def parse_accounts(text: str) -> list[str]:
    return sorted({line.strip() for line in text.splitlines() if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", line.strip())})


def recommend(rows: list[PartitionStatus], *, gpus: int, gpu_memory_gb: int | None = None,
              gpu_memory_table: dict[str, int] | None = None) -> list[dict]:
    """Rank partitions by idle-node availability, explaining the facts used.

    No start-time prediction is made; ordering reflects current idle nodes only.
    """
    table = gpu_memory_table or {}
    results = []
    for p in summarize_partitions(rows):
        if not p["up"] or p["gpus_per_node"] < gpus:
            continue
        notes = [f"{p['idle_nodes']} idle node(s) right now", f"{p['gpus_per_node']}x {p['gpu_type'] or 'GPU'} per node"]
        mem = table.get(p["gpu_type"])
        if gpu_memory_gb and mem and mem * gpus < gpu_memory_gb:
            notes.append(f"{mem * gpus} GB total GPU memory is below the {gpu_memory_gb} GB needed")
            fits = False
        else:
            fits = True
        results.append({"partition": p["partition"], "fits_memory": fits, "idle_nodes": p["idle_nodes"], "reasons": notes})
    return sorted(results, key=lambda r: (not r["fits_memory"], -r["idle_nodes"], r["partition"]))
