"""Hybrid planning: a model (ARC-hosted or external) proposes commands; ARC executes them.

The planner never executes anything. Its output is a list of command *proposals*
that flow through ``CommandSurface`` for visible review. Only non-secret ARC
context is sent to the model; credentials and SSH keys are never included.
"""

from __future__ import annotations

import re
from typing import Any

SYSTEM_PROMPT = (
    "You are a planning assistant for researchers using Virginia Tech ARC (Slurm). "
    "Propose shell commands for the user to review; you cannot run anything. "
    "Never put compute-heavy work (Python, vLLM, training, large data processing) on login nodes: "
    "use sbatch or an interactive allocation instead. Scheduler queries (squeue, sinfo, sacct), "
    "file inspection and job submission are fine on login nodes. "
    "Reply with a short explanation followed by each command in its own ```bash fenced block. "
    "Do not request or include passwords, tokens or keys."
)
_FENCE = re.compile(r"```(?:bash|sh|shell)?\n(.*?)```", re.S)


def build_context(*, allocations: list[str], partitions: list[dict[str, Any]], active_jobs: list[dict[str, Any]],
                  working_dir: str = "") -> str:
    """Compact, non-secret description of the user's ARC state for the model."""
    lines = [f"Allocations: {', '.join(allocations) or 'unknown'}"]
    for p in partitions[:20]:
        lines.append(f"Partition {p['partition']}: {p.get('gpus_per_node', 0)}x {p.get('gpu_type') or 'no GPU'} per node, "
                     f"{p.get('idle_nodes', 0)} idle node(s), {p.get('pending_jobs', 'n/a')} pending")
    lines.append(f"Active jobs: {len(active_jobs)}")
    if working_dir:
        lines.append(f"Working directory: {working_dir}")
    return "\n".join(lines)


def build_request(model: str, goal: str, context: str) -> dict[str, Any]:
    if not str(goal or "").strip():
        raise ValueError("Describe what you want to accomplish on ARC.")
    return {"model": model, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"ARC context:\n{context}\n\nGoal: {goal.strip()}"},
    ]}


def parse_plan(text: str, *, max_commands: int = 8) -> dict[str, Any]:
    """Split a model reply into explanation text and proposed commands."""
    commands = [block.strip() for block in _FENCE.findall(text or "") if block.strip()]
    explanation = _FENCE.sub("", text or "").strip()
    return {"explanation": explanation, "commands": commands[:max_commands]}
