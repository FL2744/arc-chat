"""Login-node safety classification for commands.

Classifies a shell command as lightweight (safe on an ARC login node) or heavy
(must be submitted as a compute job). The classifier is deliberately
conservative: unknown programs are ``unknown`` and treated as needing review,
never silently run as if they were safe.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

LIGHT = "light"
HEAVY = "heavy"
UNKNOWN = "unknown"

LIGHT_PROGRAMS = {
    "ls", "pwd", "cd", "cat", "head", "tail", "less", "wc", "echo", "whoami", "id", "hostname",
    "date", "env", "printenv", "which", "type", "file", "stat", "du", "df", "find", "grep", "mkdir",
    "touch", "cp", "mv", "rm", "ln", "chmod", "sinfo", "squeue", "sacct", "sacctmgr", "scontrol",
    "sbatch", "scancel", "sshare", "sprio", "sstat", "module", "quota", "showusage", "git", "tree",
    "nvidia-smi", "sort", "uniq", "cut", "diff", "basename", "dirname", "sleep",
}
HEAVY_PROGRAMS = {
    "vllm", "python", "python3", "ipython", "jupyter", "torchrun", "accelerate", "deepspeed",
    "mpirun", "mpiexec", "srun", "make", "cmake", "gcc", "g++", "gfortran", "nvcc", "ffmpeg", "tar",
    "zip", "unzip", "gzip", "bzip2", "xz", "rsync", "R", "Rscript", "matlab", "julia", "blender",
    "stress", "stress-ng", "xmrig", "ollama", "llama-server", "llama-cli", "pytest", "pip", "conda",
}
# Patterns inside an otherwise-light command that mark it as compute work.
HEAVY_PATTERNS = [
    (re.compile(r"\b(torch|tensorflow|jax|transformers|vllm)\b"), "imports an ML framework"),
    (re.compile(r"--gpus?\b|CUDA_VISIBLE_DEVICES"), "requests GPU use"),
    (re.compile(r"\bpython3?\s+-m\s+(vllm|torch|pytest)"), "runs a compute module"),
]
_SEPARATORS = re.compile(r"\|\||&&|;|\||\n")
_SUBMIT_WRAPPERS = {"sbatch", "srun", "salloc"}


@dataclass(frozen=True)
class SafetyDecision:
    verdict: str
    reason: str
    program: str = ""

    @property
    def allowed_on_login_node(self) -> bool:
        return self.verdict == LIGHT

    def explanation(self) -> str:
        if self.verdict == HEAVY:
            return ("This command appears computationally intensive "
                    f"({self.reason}). ARC Research will submit it as a compute job "
                    "rather than run it on the login node.")
        if self.verdict == UNKNOWN:
            return (f"ARC Research cannot tell whether '{self.program}' is lightweight. "
                    "Review it; it will not run on the login node until you confirm or submit it as a job.")
        return "Lightweight command; safe to run on the login node."


def _programs(command: str) -> list[list[str]]:
    segments = []
    for raw in _SEPARATORS.split(command):
        raw = raw.strip()
        if not raw:
            continue
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        # Drop leading VAR=value assignments.
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens.pop(0)
        if tokens:
            segments.append(tokens)
    return segments


def classify_command(command: str) -> SafetyDecision:
    if not str(command or "").strip():
        return SafetyDecision(LIGHT, "empty command")
    worst = SafetyDecision(LIGHT, "only lightweight programs")
    for tokens in _programs(command):
        program = tokens[0].rsplit("/", 1)[-1]
        if program in _SUBMIT_WRAPPERS and program != "srun":
            continue  # sbatch/salloc hand work to the scheduler
        if program in HEAVY_PROGRAMS:
            return SafetyDecision(HEAVY, f"'{program}' runs compute workloads", program)
        joined = " ".join(tokens)
        for pattern, reason in HEAVY_PATTERNS:
            if pattern.search(joined):
                return SafetyDecision(HEAVY, reason, program)
        if program not in LIGHT_PROGRAMS and worst.verdict == LIGHT:
            worst = SafetyDecision(UNKNOWN, "unrecognised program", program)
    return worst
